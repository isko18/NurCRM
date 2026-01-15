from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from decimal import Decimal
import hashlib
import json

from django.apps import apps
from django.db.models import (
    Sum,
    Count,
    Q,
    F,
    Value,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import TruncDate, ExtractHour, ExtractWeekDay, Coalesce
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from apps.users.models import Branch
from apps.construction.models import CashShift

from apps.main.cache_utils import cache_market_analytics_key  # путь поправь под свой проект


# ─────────────────────────────────────────────────────────────
# constants for safe typing
# ─────────────────────────────────────────────────────────────
Z_MONEY = Decimal("0.00")
Z_QTY = Decimal("0.000")

MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)


def _money(x) -> Decimal:
    try:
        return (x or Z_MONEY).quantize(Decimal("0.01"))
    except Exception:
        return Z_MONEY


def _safe_div(a: Decimal, b: int | Decimal) -> Decimal:
    if not b:
        return Z_MONEY
    return _money(Decimal(a) / Decimal(b))


def _pct(a: Decimal, b: Decimal) -> float | None:
    """
    percent(a / b). returns float with 0.1 precision or None.
    """
    try:
        bb = Decimal(b or 0)
        if bb <= 0:
            return None
        return float((Decimal(a or 0) / bb * Decimal("100")).quantize(Decimal("0.1")))
    except Exception:
        return None


def _calc_margin_pack(revenue: Decimal, cogs: Decimal):
    rev = _money(revenue)
    cg = _money(cogs)
    profit = _money(rev - cg)
    margin = _pct(profit, rev)
    return cg, profit, margin


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if len(s) == 10:  # YYYY-MM-DD
            d = datetime.fromisoformat(s)
            return datetime.combine(d.date(), time.min)
        return datetime.fromisoformat(s)
    except Exception:
        return None


@dataclass
class Period:
    start: datetime
    end: datetime  # exclusive


def _get_period(request) -> Period:
    tz = timezone.get_current_timezone()
    now = timezone.now().astimezone(tz)

    df = _parse_dt(request.query_params.get("date_from"))
    dt = _parse_dt(request.query_params.get("date_to"))

    if df and timezone.is_naive(df):
        df = timezone.make_aware(df, tz)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, tz)

    if df and dt:
        # если date_to пришёл как дата — делаем +1 день (exclusive)
        if request.query_params.get("date_to") and len(request.query_params.get("date_to")) == 10:
            dt = dt + timedelta(days=1)
        return Period(start=df, end=dt)

    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    return Period(start=first, end=nxt)


def _model_has_field(model, field_name: str) -> bool:
    try:
        return field_name in {f.name for f in model._meta.get_fields()}
    except Exception:
        return False


def _choice_value(model, enum_name: str, member: str, fallback: str) -> str:
    enum = getattr(model, enum_name, None)
    v = getattr(enum, member, None)
    return getattr(v, "value", None) or str(v or fallback)


def _get_cogs_expr(SaleItem, ProductModel=None):
    """
    Возвращает (expr, ok). expr можно суммировать через Sum(expr).
    Приоритет:
    1) purchase_price_snapshot (исторично, правильно)
    2) unit_cost/cost_price/purchase_price в SaleItem
    3) product__purchase_price (неисторично, fallback)
    """

    # 1) ИСТОРИЧНО: snapshot на момент продажи
    if _model_has_field(SaleItem, "purchase_price_snapshot") and _model_has_field(SaleItem, "quantity"):
        return ExpressionWrapper(F("quantity") * F("purchase_price_snapshot"), output_field=MONEY_FIELD), True

    # 2) другие варианты unit себестоимости
    for f in ("unit_cost", "cost_price", "purchase_price", "buy_price"):
        if _model_has_field(SaleItem, f) and _model_has_field(SaleItem, "quantity"):
            return ExpressionWrapper(F("quantity") * F(f), output_field=MONEY_FIELD), True

    # 3) fallback через Product.purchase_price (может "врать" задним числом)
    if ProductModel is not None and _model_has_field(SaleItem, "product") and _model_has_field(SaleItem, "quantity"):
        if _model_has_field(ProductModel, "purchase_price"):
            return ExpressionWrapper(F("quantity") * F("product__purchase_price"), output_field=MONEY_FIELD), True

    return None, False


# ─────────────────────────────────────────────────────────────
# Sale models lazy
# ─────────────────────────────────────────────────────────────
SALE_MODEL = None
SALE_ITEM_MODEL = None


def _guess_sale_models():
    Sale = None
    SaleItem = None

    for label in ("main.Sale", "pos.Sale", "sales.Sale"):
        try:
            Sale = apps.get_model(label)
            break
        except Exception:
            pass

    for label in ("main.SaleItem", "pos.SaleItem", "sales.SaleItem"):
        try:
            SaleItem = apps.get_model(label)
            break
        except Exception:
            pass

    if Sale is None:
        for m in apps.get_models():
            if _model_has_field(m, "total") and _model_has_field(m, "status") and _model_has_field(m, "cashbox"):
                Sale = m
                break

    if SaleItem is None and Sale is not None:
        for m in apps.get_models():
            if _model_has_field(m, "sale") and _model_has_field(m, "quantity"):
                SaleItem = m
                break

    return Sale, SaleItem


def get_sale_models():
    global SALE_MODEL, SALE_ITEM_MODEL
    if SALE_MODEL is None or SALE_ITEM_MODEL is None:
        SALE_MODEL, SALE_ITEM_MODEL = _guess_sale_models()
    return SALE_MODEL, SALE_ITEM_MODEL


# ─────────────────────────────────────────────────────────────
# company/branch helpers (твоя логика)
# ─────────────────────────────────────────────────────────────
def _get_company(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company
    br = getattr(user, "branch", None)
    if br is not None and getattr(br, "company", None):
        return br.company
    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        m = memberships.select_related("branch__company").first()
        if m and m.branch and m.branch.company:
            return m.branch.company
    return None


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "owned_company", None):
        return True
    if getattr(user, "is_admin", False):
        return True
    role = getattr(user, "role", None)
    return role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор")


def _fixed_branch_from_user(user, company):
    if not user or not company:
        return None
    company_id = getattr(company, "id", None)

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        primary_m = (
            memberships.filter(is_primary=True, branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if primary_m and primary_m.branch:
            return primary_m.branch

        any_m = (
            memberships.filter(branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if any_m and any_m.branch:
            return any_m.branch

    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def _get_active_branch(request):
    user = getattr(request, "user", None)
    company = _get_company(user)
    if not company:
        setattr(request, "branch", None)
        return None

    company_id = getattr(company, "id", None)

    if not _is_owner_like(user):
        fixed = _fixed_branch_from_user(user, company)
        setattr(request, "branch", fixed if fixed else None)
        return fixed if fixed else None

    branch_id = request.query_params.get("branch")
    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(request, "branch", br)
            return br
        except Exception:
            pass

    setattr(request, "branch", None)
    return None


def _user_label(user_obj=None, *, first_name=None, last_name=None, email=None, phone=None, user_id=None) -> str:
    if user_obj is not None:
        fn = (getattr(user_obj, "first_name", "") or "").strip()
        ln = (getattr(user_obj, "last_name", "") or "").strip()
        full = f"{fn} {ln}".strip()
        if full:
            return full
        e = getattr(user_obj, "email", None)
        if e:
            return e
        p = getattr(user_obj, "phone_number", None)
        if p:
            return p
        uid = getattr(user_obj, "id", None)
        return str(uid) if uid else "—"

    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    full = f"{fn} {ln}".strip()
    if full:
        return full
    if email:
        return str(email)
    if phone:
        return str(phone)
    if user_id:
        return str(user_id)
    return "—"


# ─────────────────────────────────────────────────────────────
# Analytics API + caching
# ─────────────────────────────────────────────────────────────
class AnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _include_global(self, request) -> bool:
        return (request.query_params.get("include_global") or "").strip() in ("1", "true", "yes", "on")

    def _apply_sale_filters(self, request, qs, SaleModel):
        cashbox_id = request.query_params.get("cashbox")
        if cashbox_id and _model_has_field(SaleModel, "cashbox"):
            qs = qs.filter(cashbox_id=cashbox_id)

        shift_id = request.query_params.get("shift")
        if shift_id and _model_has_field(SaleModel, "shift"):
            qs = qs.filter(shift_id=shift_id)

        cashier_id = request.query_params.get("cashier")
        if cashier_id:
            if _model_has_field(SaleModel, "user"):
                qs = qs.filter(user_id=cashier_id)
            elif _model_has_field(SaleModel, "shift"):
                qs = qs.filter(shift__cashier_id=cashier_id)

        pm = request.query_params.get("payment_method")
        if pm and _model_has_field(SaleModel, "payment_method"):
            qs = qs.filter(payment_method=pm)

        min_total = request.query_params.get("min_total")
        max_total = request.query_params.get("max_total")
        if min_total:
            try:
                qs = qs.filter(total__gte=Decimal(min_total))
            except Exception:
                pass
        if max_total:
            try:
                qs = qs.filter(total__lte=Decimal(max_total))
            except Exception:
                pass

        return qs

    def _cache_hash_from_query(self, request) -> str:
        qp = {k: request.query_params.getlist(k) for k in request.query_params.keys()}
        raw = json.dumps(qp, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, request):
        tab = (request.query_params.get("tab") or "sales").lower()

        company = _get_company(request.user)
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        branch = _get_active_branch(request)
        period = _get_period(request)

        # ===== caching (per tab + query) =====
        company_id = str(getattr(company, "id", ""))
        branch_id = str(getattr(branch, "id", "")) if branch else None
        qhash = self._cache_hash_from_query(request)
        ck = cache_market_analytics_key(company_id, branch_id, tab, qhash)

        cached = cache.get(ck)
        if cached is not None:
            return Response(cached)

        if tab == "sales":
            data = self._sales(request, company, branch, period)
        elif tab == "stock":
            data = self._stock(request, company, branch, period)
        elif tab == "cashboxes":
            data = self._cashboxes(request, company, branch, period)
        elif tab == "shifts":
            data = self._shifts(request, company, branch, period)
        else:
            return Response({"detail": "Unknown tab. Use: sales|stock|cashboxes|shifts"}, status=400)

        ttl = getattr(settings, "CACHE_TIMEOUT_ANALYTICS", getattr(settings, "CACHE_TIMEOUT_MEDIUM", 300))
        cache.set(ck, data, ttl)
        return Response(data)

    # ─────────────────────────────────────────────────────────
    # SALES
    # ─────────────────────────────────────────────────────────
    def _sales(self, request, company, branch, period: Period):
        revenue = Z_MONEY
        tx = 0
        clients = 0
        daily = []
        top_products = []

        cogs = None
        gross_profit = None
        margin_percent = None

        Sale, SaleItem = get_sale_models()
        if Sale is not None:
            qs = Sale.objects.filter(company=company)

            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    qs = qs.filter(branch=branch)

            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            qs = qs.filter(status=paid_value)

            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"
            qs = qs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})
            qs = self._apply_sale_filters(request, qs, Sale)

            agg = qs.aggregate(
                revenue=Coalesce(
                    Sum("total"),
                    Value(Z_MONEY, output_field=MONEY_FIELD),
                    output_field=MONEY_FIELD,
                ),
                tx=Count("id"),
            )
            revenue = agg["revenue"] or Z_MONEY
            tx = agg["tx"] or 0

            # ── margin (COGS / Profit / Margin%) ──
            if SaleItem is not None and _model_has_field(SaleItem, "sale"):
                ProductModel = None
                try:
                    ProductModel = apps.get_model("main.Product")
                except Exception:
                    ProductModel = None

                cogs_expr, ok = _get_cogs_expr(SaleItem, ProductModel)
                if ok:
                    item_qs_cost = SaleItem.objects.filter(sale__in=qs)
                    cogs_val = item_qs_cost.aggregate(
                        v=Coalesce(
                            Sum(cogs_expr),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        )
                    )["v"] or Z_MONEY

                    cogs, gross_profit, margin_percent = _calc_margin_pack(revenue, cogs_val)

            if _model_has_field(Sale, "client"):
                clients = qs.values("client_id").exclude(client_id__isnull=True).distinct().count()
            else:
                clients = qs.values("user_id").exclude(user_id__isnull=True).distinct().count()

            daily_rows = (
                qs.annotate(d=TruncDate(dt_field))
                .values("d")
                .annotate(
                    v=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                )
                .order_by("d")
            )
            daily = [{"date": r["d"].isoformat(), "value": str(_money(r["v"]))} for r in daily_rows if r["d"]]

            # top products (фикс mixed types)
            if SaleItem is not None and _model_has_field(SaleItem, "sale"):
                item_qs = SaleItem.objects.filter(sale__in=qs)

                if _model_has_field(SaleItem, "unit_price"):
                    revenue_expr = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY_FIELD)
                    item_rows = (
                        item_qs.values("product_id", "name_snapshot")
                        .annotate(
                            sold=Coalesce(
                                Sum("quantity"),
                                Value(Z_QTY, output_field=QTY_FIELD),
                                output_field=QTY_FIELD,
                            ),
                            revenue=Coalesce(
                                Sum(revenue_expr),
                                Value(Z_MONEY, output_field=MONEY_FIELD),
                                output_field=MONEY_FIELD,
                            ),
                        )
                        .order_by("-revenue")[:5]
                    )
                else:
                    item_rows = (
                        item_qs.values("product_id", "name_snapshot")
                        .annotate(
                            sold=Coalesce(
                                Sum("quantity"),
                                Value(Z_QTY, output_field=QTY_FIELD),
                                output_field=QTY_FIELD,
                            ),
                            revenue=Value(Z_MONEY, output_field=MONEY_FIELD),
                        )
                        .order_by("-sold")[:5]
                    )

                top_products = [
                    {
                        "name": (r.get("name_snapshot") or "Товар"),
                        "sold": str((r.get("sold") or Z_QTY).quantize(Decimal("0.001"))),
                        "revenue": str(_money(r.get("revenue") or Z_MONEY)),
                    }
                    for r in item_rows
                ]

        avg_check = _safe_div(_money(revenue), tx)

        documents = [
            {"name": "Продажа", "count": tx, "sum": str(_money(revenue)), "stock": None},
            {"name": "Закупка", "count": 0, "sum": "0.00", "stock": None},
            {"name": "Возврат продажи", "count": 0, "sum": "0.00", "stock": None},
            {"name": "Возврат закупки", "count": 0, "sum": "0.00", "stock": None},
        ]

        return {
            "tab": "sales",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "include_global": self._include_global(request),
            },
            "cards": {
                "revenue": str(_money(revenue)),
                "transactions": tx,
                "avg_check": str(_money(avg_check)),
                "clients": clients,
                "cogs": str(_money(cogs)) if cogs is not None else None,
                "gross_profit": str(_money(gross_profit)) if gross_profit is not None else None,
                "margin_percent": margin_percent,
            },
            "charts": {"sales_dynamics": daily},
            "tables": {"top_products": top_products, "documents": documents},
        }

    # ─────────────────────────────────────────────────────────
    # STOCK
    # ─────────────────────────────────────────────────────────
    def _stock(self, request, company, branch, period: Period):
        Product = None
        ProductCategory = None
        try:
            Product = apps.get_model("main.Product")
        except Exception:
            pass
        try:
            ProductCategory = apps.get_model("main.ProductCategory")
        except Exception:
            pass

        total_products = 0
        categories_count = 0
        inventory_value = Z_MONEY
        low_count = 0
        turnover_days = None
        category_pie = []
        movement = []
        low_list = []

        if Product is not None:
            pqs = Product.objects.filter(company=company)

            if branch is not None and _model_has_field(Product, "branch"):
                if self._include_global(request):
                    pqs = pqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    pqs = pqs.filter(branch=branch)

            # extra filters
            product_id = request.query_params.get("product")
            category_id = request.query_params.get("category")
            kind = request.query_params.get("kind")

            if product_id:
                pqs = pqs.filter(id=product_id)
            if kind and _model_has_field(Product, "kind"):
                pqs = pqs.filter(kind=kind)
            if category_id and _model_has_field(Product, "category"):
                pqs = pqs.filter(category_id=category_id)

            total_products = pqs.count()

            if _model_has_field(Product, "category") and ProductCategory is not None:
                categories_count = (
                    pqs.values("category_id")
                    .exclude(category_id__isnull=True)
                    .distinct()
                    .count()
                )

            qty_field = "quantity" if _model_has_field(Product, "quantity") else None
            pp_field = "purchase_price" if _model_has_field(Product, "purchase_price") else None
            price_field = "price" if _model_has_field(Product, "price") else None

            if qty_field and (pp_field or price_field):
                mul_field = pp_field or price_field
                inv_expr = ExpressionWrapper(
                    F(qty_field) * F(mul_field),
                    output_field=MONEY_FIELD,
                )
                inventory_value = (
                    pqs.aggregate(
                        v=Coalesce(
                            Sum(inv_expr),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        )
                    )["v"]
                    or Z_MONEY
                )

            # min stock field
            min_field = None
            for f in ("min_quantity", "min_stock", "reorder_level", "minimum_quantity"):
                if _model_has_field(Product, f):
                    min_field = f
                    break

            if qty_field:
                if min_field:
                    low_qs = pqs.filter(**{f"{qty_field}__lte": F(min_field)})
                else:
                    low_qs = pqs.filter(**{f"{qty_field}__lte": 5})

                # low_only=1 → показывать только low
                low_only = (request.query_params.get("low_only") or "").strip() in ("1", "true", "yes", "on")
                if low_only:
                    pqs = low_qs

                low_count = low_qs.count()

                low_rows = low_qs.order_by(qty_field)[:10]
                for p in low_rows:
                    q = getattr(p, qty_field, 0) or 0
                    mn = getattr(p, min_field, None) if min_field else 5
                    try:
                        qd = Decimal(q)
                    except Exception:
                        qd = Decimal("0")
                    try:
                        mnd = Decimal(mn) if mn is not None else Decimal("5")
                    except Exception:
                        mnd = Decimal("5")

                    status = "critical" if qd <= max(Decimal("1"), (mnd / 2)) else "low"
                    low_list.append({
                        "name": getattr(p, "name", "Товар"),
                        "qty": str(qd.quantize(Decimal("0.001"))),
                        "min": str(mnd.quantize(Decimal("0.001"))) if mn is not None else None,
                        "status": status,
                    })

            if _model_has_field(Product, "category"):
                rows = (
                    pqs.values("category__name")
                    .annotate(cnt=Count("id"))
                    .order_by("-cnt")[:10]
                )
                total = sum([r["cnt"] for r in rows]) or 1
                category_pie = [
                    {
                        "name": r["category__name"] or "Прочее",
                        "percent": round((r["cnt"] * 100) / total, 1),
                        "count": r["cnt"],
                    }
                    for r in rows
                ]

        # movement: units sold by day (Decimal-safe)
        Sale, SaleItem = get_sale_models()
        if Sale is not None and SaleItem is not None and _model_has_field(SaleItem, "quantity"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

            sqs = Sale.objects.filter(company=company, status=paid_value)
            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)

            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})
            sqs = self._apply_sale_filters(request, sqs, Sale)

            iq = SaleItem.objects.filter(sale__in=sqs)

            rows = (
                iq.annotate(d=TruncDate(f"sale__{dt_field}"))
                .values("d")
                .annotate(
                    units=Coalesce(
                        Sum("quantity"),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    )
                )
                .order_by("d")
            )
            movement = [
                {"date": r["d"].isoformat(), "units": str((r["units"] or Z_QTY).quantize(Decimal("0.001")))}
                for r in rows if r["d"]
            ]

            # turnover_days (примерно)
            if inventory_value and inventory_value > Decimal("0"):
                rev = sqs.aggregate(
                    v=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                )["v"] or Z_MONEY
                days = max(1, (period.end.date() - period.start.date()).days)
                avg_daily_rev = Decimal(rev) / Decimal(days)
                if avg_daily_rev > Decimal("0"):
                    turnover_days = float((Decimal(inventory_value) / avg_daily_rev).quantize(Decimal("0.1")))

        return {
            "tab": "stock",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "include_global": self._include_global(request),
                "product": request.query_params.get("product"),
                "category": request.query_params.get("category"),
                "kind": request.query_params.get("kind"),
                "low_only": (request.query_params.get("low_only") or "").strip() in ("1", "true", "yes", "on"),
            },
            "cards": {
                "total_products": total_products,
                "categories": categories_count,
                "inventory_value": str(_money(inventory_value)),
                "low_stock_count": low_count,
                "turnover_days": turnover_days,
            },
            "charts": {
                "category_distribution": category_pie,
                "movement_units": movement,
            },
            "tables": {
                "low_stock": low_list,
            },
        }

    # ─────────────────────────────────────────────────────────
    # CASHBOXES
    # ─────────────────────────────────────────────────────────
    def _cashboxes(self, request, company, branch, period: Period):
        revenue = Z_MONEY
        tx = 0
        avg_check = Z_MONEY
        cash_in_box = Z_MONEY

        cogs = None
        gross_profit = None
        margin_percent = None

        hourly = []
        pay_pie = []
        pay_detail = []
        tx_week = []
        peak_hours = []

        Sale, SaleItem = get_sale_models()
        if Sale is not None:
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

            qs = Sale.objects.filter(company=company, status=paid_value)
            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    qs = qs.filter(branch=branch)

            qs = qs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})
            qs = self._apply_sale_filters(request, qs, Sale)

            agg = qs.aggregate(
                revenue=Coalesce(
                    Sum("total"),
                    Value(Z_MONEY, output_field=MONEY_FIELD),
                    output_field=MONEY_FIELD,
                ),
                tx=Count("id"),
            )
            revenue = agg["revenue"] or Z_MONEY
            tx = agg["tx"] or 0
            avg_check = _safe_div(_money(revenue), tx)

            # ── margin (COGS / Profit / Margin%) ──
            if SaleItem is not None and _model_has_field(SaleItem, "sale"):
                ProductModel = None
                try:
                    ProductModel = apps.get_model("main.Product")
                except Exception:
                    ProductModel = None

                cogs_expr, ok = _get_cogs_expr(SaleItem, ProductModel)
                if ok:
                    item_qs_cost = SaleItem.objects.filter(sale__in=qs)
                    cogs_val = item_qs_cost.aggregate(
                        v=Coalesce(
                            Sum(cogs_expr),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        )
                    )["v"] or Z_MONEY

                    cogs, gross_profit, margin_percent = _calc_margin_pack(revenue, cogs_val)

            pm_field = "payment_method" if _model_has_field(Sale, "payment_method") else None
            if pm_field:
                rows = (
                    qs.values(pm_field)
                    .annotate(
                        cnt=Count("id"),
                        sm=Coalesce(
                            Sum("total"),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        ),
                    )
                    .order_by("-sm")
                )

                total_sum = Z_MONEY
                for r in rows:
                    total_sum += Decimal(r["sm"] or Z_MONEY)

                for r in rows:
                    name = r[pm_field] or "unknown"
                    cnt = int(r["cnt"] or 0)
                    sm = _money(r["sm"] or Z_MONEY)
                    share = float((sm / total_sum * 100).quantize(Decimal("0.1"))) if total_sum else 0.0
                    pay_detail.append({"method": name, "transactions": cnt, "sum": str(sm), "share": share})

                pay_pie = [{"name": d["method"], "percent": d["share"]} for d in pay_detail]

                cash_value = _choice_value(Sale, "PaymentMethod", "CASH", "cash")
                cash_in_box = qs.filter(**{pm_field: cash_value}).aggregate(
                    v=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                )["v"] or Z_MONEY

            hour_rows = (
                qs.annotate(h=ExtractHour(dt_field))
                .values("h")
                .annotate(
                    v=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    ),
                    cnt=Count("id"),
                )
                .order_by("h")
            )
            hourly = [
                {
                    "hour": int(r["h"]) if r["h"] is not None else 0,
                    "revenue": str(_money(r["v"])),
                    "transactions": int(r["cnt"]),
                }
                for r in hour_rows
            ]

            wd_rows = (
                qs.annotate(wd=ExtractWeekDay(dt_field))
                .values("wd")
                .annotate(cnt=Count("id"))
                .order_by("wd")
            )
            tx_week = [{"weekday": int(r["wd"]), "transactions": int(r["cnt"])} for r in wd_rows if r["wd"] is not None]

            peak_hours = sorted(hourly, key=lambda x: Decimal(x["revenue"]), reverse=True)[:6]
            for r in peak_hours:
                r["avg_check"] = str(_safe_div(Decimal(r["revenue"]), int(r["transactions"])))

        cash_share = 0.0
        try:
            cash_share = float((Decimal(cash_in_box) / Decimal(revenue) * 100).quantize(Decimal("0.1"))) if revenue else 0.0
        except Exception:
            cash_share = 0.0

        return {
            "tab": "cashboxes",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "include_global": self._include_global(request),
            },
            "cards": {
                "revenue": str(_money(revenue)),
                "transactions": tx,
                "avg_check": str(_money(avg_check)),
                "cash_in_box": str(_money(cash_in_box)),
                "cash_share_percent": cash_share,
                "cogs": str(_money(cogs)) if cogs is not None else None,
                "gross_profit": str(_money(gross_profit)) if gross_profit is not None else None,
                "margin_percent": margin_percent,
            },
            "charts": {
                "sales_by_hours": hourly,
                "payment_methods": pay_pie,
                "transactions_by_weekday": tx_week,
            },
            "tables": {
                "payment_detail": pay_detail,
                "peak_hours": peak_hours,
            },
        }

    # ─────────────────────────────────────────────────────────
    # SHIFTS
    # ─────────────────────────────────────────────────────────
    def _shifts(self, request, company, branch, period: Period):
        qp = request.query_params
        cashbox_id = qp.get("cashbox") or None
        cashier_id = qp.get("cashier") or None
        status = (qp.get("status") or "").lower() or None  # open|closed|None

        qs = CashShift.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)

        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)
        if status in ("open", "closed"):
            qs = qs.filter(status=status)

        # cards
        active_cnt = qs.filter(status=CashShift.Status.OPEN).count()

        today = timezone.localdate()
        start_today = timezone.make_aware(datetime.combine(today, time.min))
        end_today = start_today + timedelta(days=1)
        today_cnt = qs.filter(opened_at__gte=start_today, opened_at__lt=end_today).count()

        period_qs = qs.filter(opened_at__gte=period.start, opened_at__lt=period.end)

        closed = period_qs.filter(status=CashShift.Status.CLOSED).exclude(closed_at__isnull=True)
        avg_duration_hours = None
        durations = []
        for r in closed.values("opened_at", "closed_at"):
            if r["opened_at"] and r["closed_at"]:
                durations.append((r["closed_at"] - r["opened_at"]).total_seconds())
        if durations:
            avg_sec = sum(durations) / len(durations)
            avg_duration_hours = round(avg_sec / 3600, 1)

        Sale, SaleItem = get_sale_models()

        # revenue total for period (avg_revenue_per_shift)
        revenue_total = Z_MONEY
        cogs_total = None
        gross_profit_total = None
        margin_percent_total = None
        avg_profit_per_shift = None

        sqs = None  # понадобится для расчетов

        if Sale is not None and _model_has_field(Sale, "shift"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

            sqs = Sale.objects.filter(company=company, status=paid_value)

            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)

            if cashbox_id and _model_has_field(Sale, "cashbox"):
                sqs = sqs.filter(cashbox_id=cashbox_id)
            if cashier_id:
                if _model_has_field(Sale, "user"):
                    sqs = sqs.filter(user_id=cashier_id)
                else:
                    sqs = sqs.filter(shift__cashier_id=cashier_id)

            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            revenue_total = sqs.aggregate(
                v=Coalesce(
                    Sum("total"),
                    Value(Z_MONEY, output_field=MONEY_FIELD),
                    output_field=MONEY_FIELD,
                )
            )["v"] or Z_MONEY

            # ── margin totals for shifts period ──
            if SaleItem is not None and _model_has_field(SaleItem, "sale") and sqs is not None:
                ProductModel = None
                try:
                    ProductModel = apps.get_model("main.Product")
                except Exception:
                    ProductModel = None

                cogs_expr, ok = _get_cogs_expr(SaleItem, ProductModel)
                if ok:
                    item_qs_cost = SaleItem.objects.filter(sale__in=sqs)
                    cogs_val = item_qs_cost.aggregate(
                        v=Coalesce(
                            Sum(cogs_expr),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        )
                    )["v"] or Z_MONEY

                    cogs_total, gross_profit_total, margin_percent_total = _calc_margin_pack(revenue_total, cogs_val)

        shifts_cnt = period_qs.count() or 1
        avg_revenue_per_shift = _safe_div(_money(revenue_total), shifts_cnt)

        if gross_profit_total is not None:
            avg_profit_per_shift = _safe_div(_money(gross_profit_total), shifts_cnt)

        # bucket revenue by shift open time
        def bucket(h: int) -> str:
            if 6 <= h < 12:
                return "morning"
            if 12 <= h < 18:
                return "day"
            return "evening"

        bucket_map = {
            "morning": {"revenue": Z_MONEY, "transactions": 0},
            "day": {"revenue": Z_MONEY, "transactions": 0},
            "evening": {"revenue": Z_MONEY, "transactions": 0},
        }

        if Sale is not None and _model_has_field(Sale, "shift"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

            sqs2 = Sale.objects.filter(company=company, status=paid_value)

            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs2 = sqs2.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs2 = sqs2.filter(branch=branch)

            if cashbox_id and _model_has_field(Sale, "cashbox"):
                sqs2 = sqs2.filter(cashbox_id=cashbox_id)
            if cashier_id:
                if _model_has_field(Sale, "user"):
                    sqs2 = sqs2.filter(user_id=cashier_id)
                else:
                    sqs2 = sqs2.filter(shift__cashier_id=cashier_id)

            sqs2 = sqs2.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            for r in sqs2.values("total", "shift__opened_at"):
                o = r.get("shift__opened_at")
                if not o:
                    continue
                b = bucket(int(o.hour))
                bucket_map[b]["revenue"] += Decimal(r.get("total") or 0)
                bucket_map[b]["transactions"] += 1

        sales_by_shift_bucket = [
            {"name": "Утро", "key": "morning", "revenue": str(_money(bucket_map["morning"]["revenue"])), "transactions": bucket_map["morning"]["transactions"]},
            {"name": "День", "key": "day", "revenue": str(_money(bucket_map["day"]["revenue"])), "transactions": bucket_map["day"]["transactions"]},
            {"name": "Вечер", "key": "evening", "revenue": str(_money(bucket_map["evening"]["revenue"])), "transactions": bucket_map["evening"]["transactions"]},
        ]

        # active shifts table + sales per active shift
        active_rows = []
        act_qs = CashShift.objects.filter(company=company, status=CashShift.Status.OPEN)
        if branch is not None:
            act_qs = act_qs.filter(branch=branch)
        if cashbox_id:
            act_qs = act_qs.filter(cashbox_id=cashbox_id)
        if cashier_id:
            act_qs = act_qs.filter(cashier_id=cashier_id)

        act = act_qs.select_related("cashier", "cashbox").order_by("-opened_at")[:50]
        act_ids = [s.id for s in act]

        shift_sales_map = {}
        if act_ids and Sale is not None and _model_has_field(Sale, "shift"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            sale_qs = Sale.objects.filter(company=company, status=paid_value, shift_id__in=act_ids)

            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sale_qs = sale_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sale_qs = sale_qs.filter(branch=branch)

            if cashbox_id and _model_has_field(Sale, "cashbox"):
                sale_qs = sale_qs.filter(cashbox_id=cashbox_id)

            rows = sale_qs.values("shift_id").annotate(
                rev=Coalesce(
                    Sum("total"),
                    Value(Z_MONEY, output_field=MONEY_FIELD),
                    output_field=MONEY_FIELD,
                )
            )
            shift_sales_map = {r["shift_id"]: _money(r["rev"] or Z_MONEY) for r in rows}

        for sh in act:
            cb_name = getattr(sh.cashbox, "name", None) or f"Касса {sh.cashbox_id}"
            opened = timezone.localtime(sh.opened_at).isoformat() if sh.opened_at else None
            sales_sum = shift_sales_map.get(sh.id, Z_MONEY)

            active_rows.append({
                "cashier": _user_label(sh.cashier),
                "cashbox": cb_name,
                "opened_at": opened,
                "sales": str(_money(sales_sum)),
                "status": "open",
            })

        # best cashiers
        best_cashiers = []
        if Sale is not None and _model_has_field(Sale, "shift"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

            sqs3 = Sale.objects.filter(company=company, status=paid_value)

            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs3 = sqs3.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs3 = sqs3.filter(branch=branch)

            if cashbox_id and _model_has_field(Sale, "cashbox"):
                sqs3 = sqs3.filter(cashbox_id=cashbox_id)
            if cashier_id:
                if _model_has_field(Sale, "user"):
                    sqs3 = sqs3.filter(user_id=cashier_id)
                else:
                    sqs3 = sqs3.filter(shift__cashier_id=cashier_id)

            sqs3 = sqs3.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            rows = (
                sqs3.values(
                    "shift__cashier_id",
                    "shift__cashier__first_name",
                    "shift__cashier__last_name",
                    "shift__cashier__email",
                    "shift__cashier__phone_number",
                )
                .annotate(
                    revenue=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    ),
                    tx=Count("id"),
                    shifts=Count("shift_id", distinct=True),
                )
                .order_by("-revenue")[:10]
            )

            for i, r in enumerate(rows, start=1):
                rev = _money(r["revenue"] or Z_MONEY)
                txc = int(r["tx"] or 0)
                best_cashiers.append({
                    "place": i,
                    "cashier": _user_label(
                        None,
                        first_name=r.get("shift__cashier__first_name"),
                        last_name=r.get("shift__cashier__last_name"),
                        email=r.get("shift__cashier__email"),
                        phone=r.get("shift__cashier__phone_number"),
                        user_id=r.get("shift__cashier_id"),
                    ),
                    "shifts": int(r["shifts"] or 0),
                    "sales": str(rev),
                    "avg_check": str(_safe_div(rev, txc)),
                })

        return {
            "tab": "shifts",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(branch.id) if branch else None,
                "include_global": self._include_global(request),
                "cashbox": cashbox_id,
                "cashier": cashier_id,
                "status": status,
            },
            "cards": {
                "active_shifts": active_cnt,
                "shifts_today": today_cnt,
                "avg_duration_hours": avg_duration_hours,
                "avg_revenue_per_shift": str(_money(avg_revenue_per_shift)),
                "cogs_total": str(_money(cogs_total)) if cogs_total is not None else None,
                "gross_profit_total": str(_money(gross_profit_total)) if gross_profit_total is not None else None,
                "margin_percent_total": margin_percent_total,
                "avg_profit_per_shift": str(_money(avg_profit_per_shift)) if avg_profit_per_shift is not None else None,
            },
            "charts": {"sales_by_shift_bucket": sales_by_shift_bucket},
            "tables": {"active_shifts": active_rows, "best_cashiers": best_cashiers},
        }
