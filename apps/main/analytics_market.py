from __future__ import annotations

from collections import defaultdict
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
    IntegerField,
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


def _qty_str(x) -> str:
    try:
        return str(Decimal(str(x or 0)).quantize(Decimal("0.001")))
    except Exception:
        return "0.000"


def _users_sold_products_by_user(si_qs, SaleItem):
    """
    По queryset строк чеков (SaleItem с отфильтрованными sale) строит:
    - units_by_user: user_id -> сумма quantity по всем строкам
    - products_by_user: user_id -> список {"name", "quantity"} по убыванию quantity
    - names_by_user: user_id -> список имён в том же порядке
    """
    units_by_user: dict = {}
    for row in si_qs.values("sale__user_id").annotate(
        tq=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
    ):
        uid = row["sale__user_id"]
        if uid is not None:
            units_by_user[uid] = Decimal(str(row["tq"] or 0))

    bucket_qty: dict = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    bucket_name: dict = {}

    if _model_has_field(SaleItem, "product"):
        for row in (
            si_qs.filter(product__isnull=False)
            .values("sale__user_id", "product_id", "product__name")
            .annotate(
                sq=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
            )
        ):
            uid = row["sale__user_id"]
            if uid is None or not row.get("product_id"):
                continue
            key = ("p", str(row["product_id"]))
            bucket_qty[uid][key] += Decimal(str(row["sq"] or 0))
            disp = (row.get("product__name") or "").strip()
            bucket_name[(uid, key)] = disp or "—"

    if _model_has_field(SaleItem, "name_snapshot"):
        for row in (
            si_qs.filter(product__isnull=True)
            .values("sale__user_id", "name_snapshot")
            .annotate(
                sq=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
            )
        ):
            uid = row["sale__user_id"]
            if uid is None:
                continue
            snap = (row.get("name_snapshot") or "").strip() or "Позиция"
            key = ("c", snap)
            bucket_qty[uid][key] += Decimal(str(row["sq"] or 0))
            bucket_name[(uid, key)] = snap

    products_by_user: dict = {}
    names_by_user: dict = {}
    for uid, keys in bucket_qty.items():
        rows = []
        for key, q in keys.items():
            rows.append({"name": bucket_name.get((uid, key), "—"), "quantity": _qty_str(q)})
        rows.sort(key=lambda x: Decimal(x["quantity"]), reverse=True)
        products_by_user[uid] = rows
        names_by_user[uid] = [r["name"] for r in rows]

    return units_by_user, products_by_user, names_by_user


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

    # Backward/forward compatibility:
    # - older clients: date_from/date_to
    # - newer clients: period_start/period_end
    qp = request.query_params
    raw_from = qp.get("date_from") or qp.get("period_start")
    raw_to = qp.get("date_to") or qp.get("period_end")

    df = _parse_dt(raw_from)
    dt = _parse_dt(raw_to)

    if df and timezone.is_naive(df):
        df = timezone.make_aware(df, tz)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, tz)

    if df and dt:
        # если date_to пришёл как дата — делаем +1 день (exclusive)
        # (поддерживаем и period_end)
        if raw_to and len(raw_to) == 10:
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


def _sale_item_net_line_revenue_expr(SaleItem_model):
    """
    Сумма строки чека: quantity × unit_price − line_discount.
    Без line_discount топы товаров и разрезы аналитики расходятся с реальной оплатой при акциях.
    """
    if SaleItem_model is None or not _model_has_field(SaleItem_model, "unit_price"):
        return None
    if _model_has_field(SaleItem_model, "line_discount"):
        return ExpressionWrapper(
            (F("quantity") * F("unit_price"))
            - Coalesce(
                F("line_discount"),
                Value(Z_MONEY, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            ),
            output_field=MONEY_FIELD,
        )
    return ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=MONEY_FIELD,
    )


def _choice_value(model, enum_name: str, member: str, fallback: str) -> str:
    enum = getattr(model, enum_name, None)
    v = getattr(enum, member, None)
    return getattr(v, "value", None) or str(v or fallback)


def _get_cogs_expr(SaleItem, ProductModel=None):
    """
    Возвращает (expr, ok). expr можно суммировать через Sum(expr).

    Логика:
    1) Если есть purchase_price_snapshot -> берём его, но делаем fallback на product.purchase_price
       (чтобы старые строки / незаполненные snapshot не давали 100% маржу).
    2) Если есть unit_cost/cost_price/purchase_price/buy_price в SaleItem -> используем их.
    3) Иначе fallback на product.purchase_price (неисторично, но лучше чем 0).
    """
    if not _model_has_field(SaleItem, "quantity"):
        return None, False

    # 1) snapshot + fallback на product.purchase_price
    if _model_has_field(SaleItem, "purchase_price_snapshot"):
        has_product_fk = _model_has_field(SaleItem, "product")
        has_product_purchase = (
            ProductModel is not None
            and _model_has_field(ProductModel, "purchase_price")
            and has_product_fk
        )

        if has_product_purchase:
            unit_cost = Coalesce(
                F("purchase_price_snapshot"),
                F("product__purchase_price"),
                Value(Z_MONEY, output_field=MONEY_FIELD),
            )
        else:
            unit_cost = Coalesce(
                F("purchase_price_snapshot"),
                Value(Z_MONEY, output_field=MONEY_FIELD),
            )

        return ExpressionWrapper(F("quantity") * unit_cost, output_field=MONEY_FIELD), True

    # 2) другие варианты unit себестоимости
    for f in ("unit_cost", "cost_price", "purchase_price", "buy_price"):
        if _model_has_field(SaleItem, f):
            return ExpressionWrapper(F("quantity") * F(f), output_field=MONEY_FIELD), True

    # 3) fallback через Product.purchase_price
    if ProductModel is not None and _model_has_field(SaleItem, "product") and _model_has_field(ProductModel, "purchase_price"):
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

    def _market_products_queryset(self, request, company, branch):
        """Товары маркета в области компании/филиала (как на вкладке stock)."""
        try:
            Product = apps.get_model("main.Product")
        except Exception:
            return None
        pqs = Product.objects.filter(company=company)
        if branch is not None and _model_has_field(Product, "branch"):
            if self._include_global(request):
                pqs = pqs.filter(Q(branch=branch) | Q(branch__isnull=True))
            else:
                pqs = pqs.filter(branch=branch)
        return pqs

    def _products_tab_supplier_client_ids(self, request, company, branch):
        """
        Фильтр вкладки tab=products по поставщику (Product.client).
        Параметры как у списка товаров: ?supplier=<uuid> или ?suppliers=id1,id2
        Возвращает None — фильтр не задан; список uuid — разрешённые поставщики; [] — после разбора не осталось валидных id.
        """
        qp = request.query_params
        suppliers_csv = (qp.get("suppliers") or "").strip()
        supplier_one = (qp.get("supplier") or "").strip()
        raw_ids = (
            [x.strip() for x in suppliers_csv.split(",") if x.strip()]
            if suppliers_csv
            else ([supplier_one] if supplier_one else [])
        )
        if not raw_ids:
            return None
        try:
            Client = apps.get_model("main.Client")
        except Exception:
            return []
        from uuid import UUID

        parsed = []
        for s in raw_ids:
            try:
                parsed.append(UUID(str(s)))
            except (ValueError, TypeError, AttributeError):
                continue
        if not parsed:
            return []
        supplier_qs = Client.objects.filter(
            company=company,
            type=Client.StatusClient.SUPPLIERS,
            id__in=parsed,
        )
        if branch is not None and _model_has_field(Client, "branch"):
            supplier_qs = supplier_qs.filter(branch__in=[None, branch])
        return list(supplier_qs.values_list("id", flat=True))

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
        elif tab == "products":
            data = self._products_analytics(request, company, branch, period)
        elif tab == "suppliers":
            data = self._suppliers_analytics(request, company, branch, period)
        elif tab == "procurement":
            data = self._procurement(request, company, branch, period)
        elif tab == "purchases":
            data = self._purchases(request, company, branch, period)
        elif tab == "users":
            data = self._users_analytics(request, company, branch, period)
        elif tab == "finance":
            data = self._finance(request, company, branch, period)
        elif tab == "salary":
            data = self._salary(request, company, branch, period)
        else:
            return Response(
                {"detail": "Unknown tab. Use: sales|stock|cashboxes|shifts|products|suppliers|procurement|purchases|users|finance|salary"},
                status=400,
            )

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
        cogs_warning = None

        products_stock = []
        catalog_products_count = 0
        total_stock_quantity = None

        pqs_catalog = self._market_products_queryset(request, company, branch)
        if pqs_catalog is not None:
            ProductModel = pqs_catalog.model
            catalog_products_count = pqs_catalog.count()
            if _model_has_field(ProductModel, "quantity"):
                sum_row = pqs_catalog.aggregate(
                    s=Coalesce(
                        Sum("quantity"),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    )
                )
                sq = sum_row.get("s")
                total_stock_quantity = str(
                    (sq if sq is not None else Z_QTY).quantize(Decimal("0.01"))
                )
                vf = ["id", "name", "quantity"]
                if _model_has_field(ProductModel, "code"):
                    vf.append("code")
                if _model_has_field(ProductModel, "unit"):
                    vf.append("unit")
                if _model_has_field(ProductModel, "kind"):
                    vf.append("kind")
                if _model_has_field(ProductModel, "barcode"):
                    vf.append("barcode")
                for row in pqs_catalog.order_by("name").values(*vf):
                    q = row.get("quantity")
                    try:
                        qd = Decimal(q) if q is not None else Z_QTY
                    except Exception:
                        qd = Z_QTY
                    item = {
                        "id": str(row["id"]),
                        "name": (row.get("name") or "Товар").strip() or "Товар",
                        "quantity": str(qd.quantize(Decimal("0.01"))),
                    }
                    if "code" in vf:
                        item["code"] = row.get("code") or ""
                    if "unit" in vf:
                        item["unit"] = row.get("unit") or ""
                    if "kind" in vf:
                        item["kind"] = row.get("kind") or ""
                    if "barcode" in vf:
                        item["barcode"] = row.get("barcode") or ""
                    products_stock.append(item)

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

            if _money(revenue) > 0 and _money(cogs or Z_MONEY) == 0:
                cogs_warning = "Себестоимость не заполнена (маржа может быть некорректной)."

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

            # ── Payment Method Breakdown ──
            payment_breakdown = []
            if _model_has_field(Sale, "payment_method"):
                payment_rows = (
                    qs.values("payment_method")
                    .annotate(
                        count=Count("id"),
                        total=Coalesce(
                            Sum("total"),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        ),
                    )
                    .order_by("-total")
                )
                payment_breakdown = [
                    {
                        "method": r.get("payment_method") or "unknown",
                        "count": r.get("count") or 0,
                        "total": str(_money(r.get("total") or Z_MONEY)),
                    }
                    for r in payment_rows
                ]

            if SaleItem is not None and _model_has_field(SaleItem, "sale"):
                item_qs = SaleItem.objects.filter(sale__in=qs)

                revenue_expr = _sale_item_net_line_revenue_expr(SaleItem)
                if revenue_expr is not None:
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
                "cogs_warning": cogs_warning,
                "catalog_products_count": catalog_products_count,
                "total_stock_quantity": total_stock_quantity,
            },
            "charts": {
                "sales_dynamics": daily,
                "payment_methods": payment_breakdown,
            },
            "tables": {
                "top_products": top_products,
                "documents": documents,
                "products_stock": products_stock,
            },
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
        total_stock_quantity = None
        products_stock = []

        if Product is not None:
            pqs = Product.objects.filter(company=company)

            if branch is not None and _model_has_field(Product, "branch"):
                if self._include_global(request):
                    pqs = pqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    pqs = pqs.filter(branch=branch)

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

            if qty_field:
                sum_row = pqs.aggregate(
                    s=Coalesce(
                        Sum(qty_field),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    )
                )
                sq = sum_row.get("s")
                total_stock_quantity = str(
                    (sq if sq is not None else Z_QTY).quantize(Decimal("0.01"))
                )

                vf = ["id", "name", qty_field]
                if _model_has_field(Product, "code"):
                    vf.append("code")
                if _model_has_field(Product, "unit"):
                    vf.append("unit")
                if _model_has_field(Product, "kind"):
                    vf.append("kind")
                if _model_has_field(Product, "barcode"):
                    vf.append("barcode")
                for row in pqs.order_by("name").values(*vf):
                    q = row.get(qty_field)
                    try:
                        qd = Decimal(q) if q is not None else Z_QTY
                    except Exception:
                        qd = Z_QTY
                    item = {
                        "id": str(row["id"]),
                        "name": (row.get("name") or "Товар").strip() or "Товар",
                        "quantity": str(qd.quantize(Decimal("0.01"))),
                    }
                    if "code" in vf:
                        item["code"] = row.get("code") or ""
                    if "unit" in vf:
                        item["unit"] = row.get("unit") or ""
                    if "kind" in vf:
                        item["kind"] = row.get("kind") or ""
                    if "barcode" in vf:
                        item["barcode"] = row.get("barcode") or ""
                    products_stock.append(item)

            if qty_field and (pp_field or price_field):
                mul_field = pp_field or price_field
                inv_expr = ExpressionWrapper(F(qty_field) * F(mul_field), output_field=MONEY_FIELD)
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
                "total_stock_quantity": total_stock_quantity,
            },
            "charts": {
                "category_distribution": category_pie,
                "movement_units": movement,
            },
            "tables": {
                "low_stock": low_list,
                "products_stock": products_stock,
            },
        }

    # ─────────────────────────────────────────────────────────
    # PURCHASES (закупки по полю Product.date)
    # ─────────────────────────────────────────────────────────
    def _purchases(self, request, company, branch, period: Period):
        qp = request.query_params
        raw_from = (qp.get("purchase_date_from") or qp.get("date_from") or "").strip() or None
        raw_to = (qp.get("purchase_date_to") or qp.get("date_to") or "").strip() or None

        df = _parse_dt(raw_from) if raw_from else period.start
        dt = _parse_dt(raw_to) if raw_to else period.end
        if df and timezone.is_naive(df):
            df = timezone.make_aware(df, timezone.get_current_timezone())
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        if dt and raw_to and len(raw_to) == 10:
            dt = dt + timedelta(days=1)

        pqs = self._market_products_queryset(request, company, branch)
        if pqs is None:
            return {
                "tab": "purchases",
                "period": {"from": df.isoformat(), "to": dt.isoformat()},
                "filters": {
                    "branch": str(getattr(branch, "id", "")) if branch else None,
                    "include_global": self._include_global(request),
                    "purchase_date_from": raw_from,
                    "purchase_date_to": raw_to,
                },
                "meta": {"note": "Purchase analytics requires main.Product model."},
                "cards": {"purchased_sku_count": 0, "purchased_units": "0.000", "purchased_value": "0.00"},
                "tables": {"by_supplier": []},
            }

        Product = pqs.model
        if not _model_has_field(Product, "date"):
            return {
                "tab": "purchases",
                "period": {"from": df.isoformat(), "to": dt.isoformat()},
                "filters": {
                    "branch": str(getattr(branch, "id", "")) if branch else None,
                    "include_global": self._include_global(request),
                    "purchase_date_from": raw_from,
                    "purchase_date_to": raw_to,
                },
                "meta": {"note": "Purchase analytics requires Product.date field (purchase date)."},
                "cards": {"purchased_sku_count": 0, "purchased_units": "0.000", "purchased_value": "0.00"},
                "tables": {"by_supplier": []},
            }

        qty_field = "quantity" if _model_has_field(Product, "quantity") else None
        pp_field = "purchase_price" if _model_has_field(Product, "purchase_price") else None

        pqs2 = pqs.filter(date__gte=df, date__lt=dt)

        purchased_sku_count = int(pqs2.count() or 0)
        purchased_units = Z_QTY
        purchased_value = Z_MONEY

        if qty_field:
            purchased_units = (
                pqs2.aggregate(
                    s=Coalesce(
                        Sum(qty_field),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    )
                )["s"]
                or Z_QTY
            )

        if qty_field and pp_field:
            val_expr = ExpressionWrapper(F(qty_field) * F(pp_field), output_field=MONEY_FIELD)
            purchased_value = (
                pqs2.aggregate(
                    s=Coalesce(
                        Sum(val_expr),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                )["s"]
                or Z_MONEY
            )

        by_supplier = []
        if _model_has_field(Product, "client"):
            sup_rows = (
                pqs2.values("client_id", "client__full_name", "client__llc", "client__phone")
                .annotate(
                    sku_count=Count("id"),
                    units=Coalesce(
                        Sum(qty_field),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    )
                    if qty_field
                    else Value(Z_QTY, output_field=QTY_FIELD),
                    value=Coalesce(
                        Sum(ExpressionWrapper(F(qty_field) * F(pp_field), output_field=MONEY_FIELD)),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                    if (qty_field and pp_field)
                    else Value(Z_MONEY, output_field=MONEY_FIELD),
                )
                .order_by("-value", "-units")
            )
            for r in sup_rows:
                by_supplier.append(
                    {
                        "supplier_id": str(r.get("client_id")) if r.get("client_id") else None,
                        "supplier": (r.get("client__full_name") or r.get("client__llc") or "—").strip() or "—",
                        "phone": r.get("client__phone"),
                        "sku_count": int(r.get("sku_count") or 0),
                        "units": _qty_str(r.get("units") or Z_QTY),
                        "value": str(_money(r.get("value") or Z_MONEY)),
                    }
                )

        return {
            "tab": "purchases",
            "period": {"from": df.isoformat(), "to": dt.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "include_global": self._include_global(request),
                "purchase_date_from": raw_from,
                "purchase_date_to": raw_to,
            },
            "cards": {
                "purchased_sku_count": purchased_sku_count,
                "purchased_units": _qty_str(purchased_units),
                "purchased_value": str(_money(purchased_value)),
            },
            "tables": {"by_supplier": by_supplier},
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
        cogs_warning = None

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

            if _money(revenue) > 0 and _money(cogs or Z_MONEY) == 0:
                cogs_warning = "Себестоимость не заполнена (маржа может быть некорректной)."

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
                {"hour": int(r["h"]) if r["h"] is not None else 0, "revenue": str(_money(r["v"])), "transactions": int(r["cnt"])}
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
                "cogs_warning": cogs_warning,
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
        status = (qp.get("status") or "").lower() or None

        qs = CashShift.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)

        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)
        if status in ("open", "closed"):
            qs = qs.filter(status=status)

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

        revenue_total = Z_MONEY
        cogs_total = None
        gross_profit_total = None
        margin_percent_total = None
        avg_profit_per_shift = None
        cogs_warning = None

        sqs = None

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

            if _money(revenue_total) > 0 and _money(cogs_total or Z_MONEY) == 0:
                cogs_warning = "Себестоимость не заполнена (маржа может быть некорректной)."

        shifts_cnt = period_qs.count() or 1
        avg_revenue_per_shift = _safe_div(_money(revenue_total), shifts_cnt)

        if gross_profit_total is not None:
            avg_profit_per_shift = _safe_div(_money(gross_profit_total), shifts_cnt)

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
                "cogs_warning": cogs_warning,
            },
            "charts": {"sales_by_shift_bucket": sales_by_shift_bucket},
            "tables": {"active_shifts": active_rows, "best_cashiers": best_cashiers},
        }

    # ─────────────────────────────────────────────────────────
    # PRODUCTS ANALYTICS
    # ─────────────────────────────────────────────────────────
    def _products_analytics(self, request, company, branch, period: Period):
        Product = None
        ProductCategory = None
        ProductBrand = None
        try:
            Product = apps.get_model("main.Product")
            ProductCategory = apps.get_model("main.ProductCategory")
            ProductBrand = apps.get_model("main.ProductBrand")
        except Exception:
            pass

        # Параметры для лимита результатов (по умолчанию показываем ВСЕ)
        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param and limit_param.isdigit() else None  # None = все записи
        
        top_products_by_revenue = []
        top_products_by_qty = []
        categories_performance = []
        brands_performance = []
        stock_value = Z_MONEY
        low_stock_count = 0
        low_stock_products = []  # НОВОЕ: полный список товаров с низким остатком
        sales_without_product = []
        sales_without_product_line_count = 0
        rejected_catalog_products = []
        rejected_products_count = 0

        supplier_ids = None
        if Product and _model_has_field(Product, "client"):
            supplier_ids = self._products_tab_supplier_client_ids(request, company, branch)

        Sale, SaleItem = get_sale_models()
        if Sale and SaleItem and Product:
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"
            
            sqs = Sale.objects.filter(company=company, status=paid_value)
            if branch and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)
            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            def _sale_items_products_tab(sale_qs):
                qi = SaleItem.objects.filter(sale__in=sale_qs)
                if supplier_ids is not None:
                    qi = qi.filter(product__client_id__in=supplier_ids)
                return qi

            # Top Products by Revenue - ДЕТАЛЬНО со всеми полями
            revenue_expr = _sale_item_net_line_revenue_expr(SaleItem)
            if revenue_expr is not None:
                top_by_rev_query = (
                    _sale_items_products_tab(sqs)
                    .values(
                        "product_id", 
                        "name_snapshot",
                        "product__code",
                        "product__article",
                        "product__barcode",
                        "product__category__name",
                        "product__brand__name",
                        "product__quantity",
                        "product__price",
                        "product__purchase_price",
                    )
                    .annotate(
                        revenue=Coalesce(Sum(revenue_expr), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                        qty_sold=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
                        tx_count=Count("sale_id", distinct=True),
                    )
                    .order_by("-revenue")
                )
                if limit:
                    top_by_rev_query = top_by_rev_query[:limit]
                
                top_products_by_revenue = [
                    {
                        "product_id": str(r["product_id"]) if r["product_id"] else None,
                        "name": r["name_snapshot"] or "Товар",
                        "code": r["product__code"],
                        "article": r["product__article"],
                        "barcode": r["product__barcode"],
                        "category": r["product__category__name"],
                        "brand": r["product__brand__name"],
                        "current_stock": str(Decimal(r["product__quantity"] or 0).quantize(Decimal("0.001"))),
                        "price": str(_money(r["product__price"] or 0)),
                        "purchase_price": str(_money(r["product__purchase_price"] or 0)),
                        "revenue": str(_money(r["revenue"])),
                        "qty_sold": str(r["qty_sold"].quantize(Decimal("0.001"))),
                        "transactions": r["tx_count"],
                    }
                    for r in top_by_rev_query
                ]

            # Top Products by Quantity - ДЕТАЛЬНО
            top_by_qty_query = (
                _sale_items_products_tab(sqs)
                .values(
                    "product_id", 
                    "name_snapshot",
                    "product__code",
                    "product__category__name",
                    "product__brand__name",
                    "product__quantity",
                    "product__price",
                )
                .annotate(
                    qty_sold=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
                    tx_count=Count("sale_id", distinct=True),
                )
                .order_by("-qty_sold")
            )
            if limit:
                top_by_qty_query = top_by_qty_query[:limit]
                
            top_products_by_qty = [
                {
                    "product_id": str(r["product_id"]) if r["product_id"] else None,
                    "name": r["name_snapshot"] or "Товар",
                    "code": r["product__code"],
                    "category": r["product__category__name"],
                    "brand": r["product__brand__name"],
                    "current_stock": str(Decimal(r["product__quantity"] or 0).quantize(Decimal("0.001"))),
                    "price": str(_money(r["product__price"] or 0)),
                    "qty_sold": str(r["qty_sold"].quantize(Decimal("0.001"))),
                    "transactions": r["tx_count"],
                }
                for r in top_by_qty_query
            ]

            # Categories Performance - ДЕТАЛЬНО с количеством товаров в каждой
            if _model_has_field(SaleItem, "product") and _model_has_field(Product, "category"):
                if revenue_expr is not None:
                    cat_query = (
                        _sale_items_products_tab(sqs).filter(product__isnull=False)
                        .values("product__category__id", "product__category__name")
                        .annotate(
                            revenue=Coalesce(Sum(revenue_expr), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                            qty_sold=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
                            products_count=Count("product_id", distinct=True),
                            tx_count=Count("sale_id", distinct=True),
                        )
                        .order_by("-revenue")
                    )
                    if limit:
                        cat_query = cat_query[:limit]
                        
                    categories_performance = [
                        {
                            "category_id": str(r["product__category__id"]) if r["product__category__id"] else None,
                            "category": r["product__category__name"] or "Без категории",
                            "revenue": str(_money(r["revenue"])),
                            "qty_sold": str(r["qty_sold"].quantize(Decimal("0.001"))),
                            "products_count": r["products_count"],
                            "transactions": r["tx_count"],
                        }
                        for r in cat_query
                    ]

            # Brands Performance - ДЕТАЛЬНО
            if _model_has_field(SaleItem, "product") and _model_has_field(Product, "brand"):
                if revenue_expr is not None:
                    brand_query = (
                        _sale_items_products_tab(sqs).filter(product__isnull=False)
                        .values("product__brand__id", "product__brand__name")
                        .annotate(
                            revenue=Coalesce(Sum(revenue_expr), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                            qty_sold=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
                            products_count=Count("product_id", distinct=True),
                            tx_count=Count("sale_id", distinct=True),
                        )
                        .order_by("-revenue")
                    )
                    if limit:
                        brand_query = brand_query[:limit]
                        
                    brands_performance = [
                        {
                            "brand_id": str(r["product__brand__id"]) if r["product__brand__id"] else None,
                            "brand": r["product__brand__name"] or "Без бренда",
                            "revenue": str(_money(r["revenue"])),
                            "qty_sold": str(r["qty_sold"].quantize(Decimal("0.001"))),
                            "products_count": r["products_count"],
                            "transactions": r["tx_count"],
                        }
                        for r in brand_query
                    ]

            # Строки продаж без карточки товара (Product удалён → product=NULL, остаётся name_snapshot)
            # При фильтре по поставщику не смешиваем с «призраками» без product.client
            if _model_has_field(SaleItem, "name_snapshot") and supplier_ids is None:
                ghost_base = SaleItem.objects.filter(sale__in=sqs, product__isnull=True)
                sales_without_product_line_count = int(ghost_base.count() or 0)
                gvals = ("name_snapshot", "barcode_snapshot")
                ghost_rev = _sale_item_net_line_revenue_expr(SaleItem)
                if ghost_rev is not None:
                    gq = (
                        ghost_base.values(*gvals)
                        .annotate(
                            qty_sold=Coalesce(
                                Sum("quantity"),
                                Value(Z_QTY, output_field=QTY_FIELD),
                                output_field=QTY_FIELD,
                            ),
                            revenue=Coalesce(
                                Sum(ghost_rev),
                                Value(Z_MONEY, output_field=MONEY_FIELD),
                                output_field=MONEY_FIELD,
                            ),
                            tx_count=Count("sale_id", distinct=True),
                        )
                        .order_by("-qty_sold")
                    )
                else:
                    gq = (
                        ghost_base.values(*gvals)
                        .annotate(
                            qty_sold=Coalesce(
                                Sum("quantity"),
                                Value(Z_QTY, output_field=QTY_FIELD),
                                output_field=QTY_FIELD,
                            ),
                            tx_count=Count("sale_id", distinct=True),
                        )
                        .order_by("-qty_sold")
                    )
                if limit:
                    gq = gq[:limit]
                for r in gq:
                    row = {
                        "name": (r.get("name_snapshot") or "").strip() or "—",
                        "barcode_snapshot": r.get("barcode_snapshot"),
                        "qty_sold": str((r.get("qty_sold") or Z_QTY).quantize(Decimal("0.001"))),
                        "transactions": int(r.get("tx_count") or 0),
                    }
                    if ghost_rev is not None:
                        row["revenue"] = str(_money(r.get("revenue") or Z_MONEY))
                    sales_without_product.append(row)

        # Stock Analysis - ПОЛНЫЙ СПИСОК товаров с низким остатком
        if Product:
            pqs = Product.objects.filter(company=company)
            if branch and _model_has_field(Product, "branch"):
                if self._include_global(request):
                    pqs = pqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    pqs = pqs.filter(branch=branch)
            if supplier_ids is not None and _model_has_field(Product, "client"):
                pqs = pqs.filter(client_id__in=supplier_ids)
            
            qty_field = "quantity" if _model_has_field(Product, "quantity") else None
            pp_field = "purchase_price" if _model_has_field(Product, "purchase_price") else None
            price_field = "price" if _model_has_field(Product, "price") else None
            
            if qty_field and pp_field:
                inv_expr = ExpressionWrapper(F(qty_field) * F(pp_field), output_field=MONEY_FIELD)
                stock_value = pqs.aggregate(v=Coalesce(Sum(inv_expr), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD))["v"] or Z_MONEY
                
                # Товары с низким остатком - ВСЕ 71 товар! (или сколько их есть)
                low_stock_qs = pqs.filter(**{f"{qty_field}__lte": 5}).order_by(qty_field)
                low_stock_count = low_stock_qs.count()
                
                # Применяем лимит только если он задан
                if limit:
                    low_stock_qs = low_stock_qs[:limit]
                
                low_stock_products = [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "code": getattr(p, "code", None),
                        "article": getattr(p, "article", None),
                        "barcode": getattr(p, "barcode", None),
                        "category": getattr(p.category, "name", None) if hasattr(p, "category") and p.category else None,
                        "brand": getattr(p.brand, "name", None) if hasattr(p, "brand") and p.brand else None,
                        "quantity": str(Decimal(getattr(p, qty_field, 0) or 0).quantize(Decimal("0.001"))),
                        "price": str(_money(getattr(p, price_field, 0) or 0)) if price_field else None,
                        "purchase_price": str(_money(getattr(p, pp_field, 0) or 0)),
                        "stock_value": str(_money(Decimal(getattr(p, qty_field, 0) or 0) * Decimal(getattr(p, pp_field, 0) or 0))),
                        "status": "critical" if Decimal(getattr(p, qty_field, 0) or 0) <= 1 else "low",
                    }
                    for p in low_stock_qs
                ]

        catalog_products_count = 0
        pqs_scope = self._market_products_queryset(request, company, branch)
        if pqs_scope is not None and supplier_ids is not None and _model_has_field(pqs_scope.model, "client"):
            pqs_scope = pqs_scope.filter(client_id__in=supplier_ids)
        if pqs_scope is not None:
            catalog_products_count = pqs_scope.count()

        # Карточки товаров со статусом «Отказ» (в каталоге есть, на витрине «нет»)
        if Product is not None and _model_has_field(Product, "status"):
            rej_val = _choice_value(Product, "Status", "REJECTED", "rejected")
            rj = Product.objects.filter(company=company, status=rej_val)
            if branch and _model_has_field(Product, "branch"):
                if self._include_global(request):
                    rj = rj.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    rj = rj.filter(branch=branch)
            if supplier_ids is not None and _model_has_field(Product, "client"):
                rj = rj.filter(client_id__in=supplier_ids)
            rejected_products_count = rj.count()
            rlim = limit if limit is not None else 200
            for p in rj.select_related("category", "brand").order_by("-updated_at")[:rlim]:
                rejected_catalog_products.append({
                    "id": str(p.id),
                    "name": getattr(p, "name", None) or "—",
                    "code": getattr(p, "code", None),
                    "article": getattr(p, "article", None),
                    "barcode": getattr(p, "barcode", None),
                    "quantity": (
                        str(Decimal(str(getattr(p, "quantity", None) or 0)).quantize(Decimal("0.001")))
                        if _model_has_field(Product, "quantity")
                        else None
                    ),
                    "status": getattr(p, "status", None),
                    "updated_at": p.updated_at.isoformat() if getattr(p, "updated_at", None) else None,
                    "category": getattr(p.category, "name", None) if getattr(p, "category", None) else None,
                    "brand": getattr(p.brand, "name", None) if getattr(p, "brand", None) else None,
                })

        return {
            "tab": "products",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "limit": limit,
                "supplier": (request.query_params.get("supplier") or "").strip() or None,
                "suppliers": (request.query_params.get("suppliers") or "").strip() or None,
            },
            "cards": {
                "stock_value": str(_money(stock_value)),
                "low_stock_count": low_stock_count,
                "catalog_products_count": catalog_products_count,
                "sales_lines_missing_product_count": sales_without_product_line_count,
                "rejected_products_count": rejected_products_count,
            },
            "tables": {
                "top_by_revenue": top_products_by_revenue,
                "top_by_quantity": top_products_by_qty,
                "categories": categories_performance,
                "brands": brands_performance,
                "low_stock_products": low_stock_products,  # НОВОЕ: полный список с деталями
                "sales_without_catalog_product": sales_without_product,
                "rejected_products": rejected_catalog_products,
            },
        }

    # ─────────────────────────────────────────────────────────
    # SUPPLIERS (Client type=suppliers → Product.client)
    # ─────────────────────────────────────────────────────────
    def _suppliers_analytics(self, request, company, branch, period: Period):
        """
        Остатки по каталогу (sku с поставщиком), продажи за период, место и условный рейтинг по объёму продаж.
        Отзывы Review не привязаны к товару — rating считается от ранга по period_qty_sold (1..5).
        """
        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param and limit_param.isdigit() else None
        qp = request.query_params

        purchase_raw_from = (qp.get("purchase_date_from") or qp.get("procurement_date_from") or "").strip() or None
        purchase_raw_to = (qp.get("purchase_date_to") or qp.get("procurement_date_to") or "").strip() or None
        purchase_df = _parse_dt(purchase_raw_from) if purchase_raw_from else None
        purchase_dt = _parse_dt(purchase_raw_to) if purchase_raw_to else None
        if purchase_df and timezone.is_naive(purchase_df):
            purchase_df = timezone.make_aware(purchase_df, timezone.get_current_timezone())
        if purchase_dt and timezone.is_naive(purchase_dt):
            purchase_dt = timezone.make_aware(purchase_dt, timezone.get_current_timezone())
        if purchase_dt and purchase_raw_to and len(purchase_raw_to) == 10:
            purchase_dt = purchase_dt + timedelta(days=1)

        def _dec_qty(v) -> Decimal:
            if isinstance(v, Decimal):
                return v
            return Decimal(str(v or 0))

        try:
            Client = apps.get_model("main.Client")
            Product = apps.get_model("main.Product")
        except Exception:
            return {
                "tab": "suppliers",
                "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
                "filters": {
                    "branch": str(getattr(branch, "id", "")) if branch else None,
                    "limit": limit,
                },
                "cards": {"catalog_products_count": 0},
                "tables": {"suppliers": [], "suppliers_by_stock": []},
            }

        if not _model_has_field(Product, "client"):
            return {
                "tab": "suppliers",
                "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
                "filters": {
                    "branch": str(getattr(branch, "id", "")) if branch else None,
                    "limit": limit,
                },
                "cards": {"suppliers_count": 0, "catalog_products_count": 0},
                "tables": {"suppliers": [], "suppliers_by_stock": []},
            }

        sup_type = Client.StatusClient.SUPPLIERS
        pqs = self._market_products_queryset(request, company, branch)
        if pqs is None:
            return {
                "tab": "suppliers",
                "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
                "filters": {
                    "branch": str(getattr(branch, "id", "")) if branch else None,
                    "limit": limit,
                },
                "cards": {"suppliers_count": 0, "catalog_products_count": 0},
                "tables": {"suppliers": [], "suppliers_by_stock": []},
            }

        catalog_products_count = pqs.count()

        p_sup = pqs.filter(
            client_id__isnull=False,
            client__type=sup_type,
        )
        if branch is not None and _model_has_field(Client, "branch"):
            p_sup = p_sup.filter(Q(client__branch=branch) | Q(client__branch__isnull=True))
        if (purchase_df or purchase_dt) and _model_has_field(Product, "date"):
            if purchase_df:
                p_sup = p_sup.filter(date__gte=purchase_df)
            if purchase_dt:
                p_sup = p_sup.filter(date__lt=purchase_dt)

        qty_field = "quantity" if _model_has_field(Product, "quantity") else None
        pp_field = "purchase_price" if _model_has_field(Product, "purchase_price") else None

        ann: dict = {"products_count": Count("id")}
        if qty_field:
            ann["stock_qty"] = Coalesce(
                Sum(qty_field),
                Value(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            )
        else:
            ann["stock_qty"] = Value(Z_QTY, output_field=QTY_FIELD)
        if qty_field and pp_field:
            inv_expr = ExpressionWrapper(F(qty_field) * F(pp_field), output_field=MONEY_FIELD)
            ann["stock_value"] = Coalesce(
                Sum(inv_expr),
                Value(Z_MONEY, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            )
        else:
            ann["stock_value"] = Value(Z_MONEY, output_field=MONEY_FIELD)

        stock_qs = (
            p_sup.values("client_id", "client__full_name", "client__llc", "client__phone")
            .annotate(**ann)
        )

        by_id: dict[str, dict] = {}
        total_stock_val = Z_MONEY
        for r in stock_qs:
            cid = r.get("client_id")
            if not cid:
                continue
            k = str(cid)
            sq_raw = r.get("stock_qty") or 0
            sq_dec = _dec_qty(sq_raw)
            sv = r.get("stock_value") or Z_MONEY
            total_stock_val += _money(sv)
            by_id[k] = {
                "supplier_id": k,
                "name": (
                    (r.get("client__llc") or "").strip()
                    or (r.get("client__full_name") or "").strip()
                    or "—"
                ),
                "phone": (r.get("client__phone") or "") or "",
                "products_count": int(r.get("products_count") or 0),
                "_stock_dec": sq_dec,
                "_sold_dec": Z_QTY,
                "_rev_dec": Z_MONEY,
                "stock_value": str(_money(sv)),
                "period_transactions": 0,
            }

        Sale, SaleItem = get_sale_models()
        if Sale and SaleItem and _model_has_field(SaleItem, "sale") and _model_has_field(SaleItem, "quantity"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"
            sqs = Sale.objects.filter(company=company, status=paid_value)
            if branch and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)
            sqs = self._apply_sale_filters(request, sqs, Sale)
            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            si = SaleItem.objects.filter(sale__in=sqs, product_id__in=p_sup.values("id"))
            revenue_expr = _sale_item_net_line_revenue_expr(SaleItem)
            if _model_has_field(SaleItem, "product"):
                agg_kw: dict = {
                    "qty_sold": Coalesce(
                        Sum("quantity"),
                        Value(Z_QTY, output_field=QTY_FIELD),
                        output_field=QTY_FIELD,
                    ),
                    "tx_count": Count("sale_id", distinct=True),
                }
                if revenue_expr is not None:
                    agg_kw["revenue"] = Coalesce(
                        Sum(revenue_expr),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    )
                sales_rows = si.values("product__client_id").annotate(**agg_kw)
                for r in sales_rows:
                    cid = r.get("product__client_id")
                    if not cid:
                        continue
                    k = str(cid)
                    if k not in by_id:
                        cl = (
                            Client.objects.filter(pk=cid, company=company, type=sup_type)
                            .only("full_name", "llc", "phone")
                            .first()
                        )
                        nm = "—"
                        ph = ""
                        if cl:
                            nm = (getattr(cl, "llc", None) or "").strip() or (getattr(cl, "full_name", None) or "").strip() or "—"
                            ph = getattr(cl, "phone", None) or ""
                        by_id[k] = {
                            "supplier_id": k,
                            "name": nm,
                            "phone": ph,
                            "products_count": 0,
                            "_stock_dec": Z_QTY,
                            "_sold_dec": Z_QTY,
                            "_rev_dec": Z_MONEY,
                            "stock_value": str(Z_MONEY),
                            "period_transactions": 0,
                        }
                    row = by_id[k]
                    row["_sold_dec"] = _dec_qty(r.get("qty_sold"))
                    row["period_transactions"] = int(r.get("tx_count") or 0)
                    if "revenue" in r:
                        row["_rev_dec"] = _money(r.get("revenue") or Z_MONEY)
                    else:
                        row["_rev_dec"] = Z_MONEY

        items = list(by_id.values())
        tot_period_qty = Z_QTY
        tot_period_rev = Z_MONEY
        for it in items:
            tot_period_qty += it["_sold_dec"]
            tot_period_rev += it["_rev_dec"]

        n_sup = len(items)
        items.sort(key=lambda x: (-x["_sold_dec"], -x["_stock_dec"]))
        for i, it in enumerate(items):
            it["rank_by_qty"] = i + 1
            if n_sup <= 1:
                it["rating"] = 5.0
            else:
                it["rating"] = round(float(Decimal("5") - Decimal("4") * Decimal(i) / Decimal(n_sup - 1)), 1)
            it["period_qty_sold"] = _qty_str(it["_sold_dec"])
            it["period_revenue"] = str(_money(it["_rev_dec"]))
            it["stock_qty"] = _qty_str(it["_stock_dec"])
            del it["_sold_dec"]
            del it["_stock_dec"]
            del it["_rev_dec"]

        by_stock = sorted(items, key=lambda x: -Decimal(str(x["stock_qty"])))
        for j, it in enumerate(by_stock, start=1):
            it["rank_by_stock"] = j

        suppliers_by_stock = [
            {
                "supplier_id": it["supplier_id"],
                "name": it["name"],
                "rank_by_stock": it["rank_by_stock"],
                "stock_qty": it["stock_qty"],
                "stock_value": it["stock_value"],
                "products_count": it["products_count"],
            }
            for it in by_stock
        ]

        if limit:
            items = items[:limit]
            suppliers_by_stock = suppliers_by_stock[:limit]

        return {
            "tab": "suppliers",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "limit": limit,
                "purchase_date_from": purchase_raw_from,
                "purchase_date_to": purchase_raw_to,
            },
            "cards": {
                "suppliers_count": n_sup,
                "catalog_products_count": catalog_products_count,
                "total_stock_value": str(_money(total_stock_val)),
                "total_period_qty_sold": _qty_str(tot_period_qty),
                "total_period_revenue": str(_money(tot_period_rev)),
            },
            "tables": {
                "suppliers": items,
                "suppliers_by_stock": suppliers_by_stock,
            },
        }

    # ─────────────────────────────────────────────────────────
    # PROCUREMENT (продажи vs отгрузка агентам; закупки от поставщика без журнала)
    # ─────────────────────────────────────────────────────────
    def _procurement(self, request, company, branch, period: Period):
        from uuid import UUID as UUIDType

        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param and limit_param.isdigit() else 50

        Product = None
        try:
            Product = apps.get_model("main.Product")
        except Exception:
            pass
        Subreal = None
        try:
            Subreal = apps.get_model("main.ManufactureSubreal")
        except Exception:
            pass

        pqs = self._market_products_queryset(request, company, branch)
        catalog_products_count = pqs.count() if pqs is not None else 0

        zero_i = Value(0, output_field=IntegerField())

        s_map: dict[str, dict] = {}
        Sale, SaleItem = get_sale_models()

        if (
            Sale
            and SaleItem
            and Product
            and pqs is not None
            and _model_has_field(SaleItem, "product")
            and _model_has_field(SaleItem, "quantity")
        ):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"
            sqs = Sale.objects.filter(company=company, status=paid_value)
            if branch and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)
            sqs = self._apply_sale_filters(request, sqs, Sale)
            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            si = SaleItem.objects.filter(sale__in=sqs, product_id__in=pqs.values("id"), product__isnull=False)
            base_vals = ("product_id", "product__name", "product__code")
            rev_expr = _sale_item_net_line_revenue_expr(SaleItem)
            if rev_expr is not None:
                rows = (
                    si.values(*base_vals)
                    .annotate(
                        qty_sold=Coalesce(
                            Sum("quantity"),
                            Value(Z_QTY, output_field=QTY_FIELD),
                            output_field=QTY_FIELD,
                        ),
                        revenue=Coalesce(
                            Sum(rev_expr),
                            Value(Z_MONEY, output_field=MONEY_FIELD),
                            output_field=MONEY_FIELD,
                        ),
                    )
                    .order_by("-qty_sold")
                )
            else:
                rows = (
                    si.values(*base_vals)
                    .annotate(
                        qty_sold=Coalesce(
                            Sum("quantity"),
                            Value(Z_QTY, output_field=QTY_FIELD),
                            output_field=QTY_FIELD,
                        ),
                    )
                    .order_by("-qty_sold")
                )
            for r in rows:
                k = str(r["product_id"])
                s_map[k] = {
                    "product_id": k,
                    "name": (r.get("product__name") or "").strip() or "—",
                    "code": r.get("product__code"),
                    "qty_sold_dec": Decimal(str(r.get("qty_sold") or 0)),
                    "revenue_dec": _money(r.get("revenue") or Z_MONEY) if "revenue" in r else Z_MONEY,
                }

        t_map: dict[str, dict] = {}
        if Subreal is not None and Product is not None and pqs is not None:
            rq = Subreal.objects.filter(company=company)
            if branch and _model_has_field(Subreal, "branch"):
                if self._include_global(request):
                    rq = rq.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    rq = rq.filter(branch=branch)
            rq = rq.filter(
                created_at__gte=period.start,
                created_at__lt=period.end,
                product_id__in=pqs.values("id"),
            )
            tr = (
                rq.values("product_id", "product__name", "product__code")
                .annotate(
                    qty_transferred=Coalesce(
                        Sum("qty_transferred"),
                        zero_i,
                        output_field=IntegerField(),
                    ),
                )
                .order_by("-qty_transferred")
            )
            for r in tr:
                k = str(r["product_id"])
                t_map[k] = {
                    "product_id": k,
                    "name": (r.get("product__name") or "").strip() or "—",
                    "code": r.get("product__code"),
                    "qty_transferred_to_agents": int(r.get("qty_transferred") or 0),
                }

        all_ids = set(s_map) | set(t_map)
        meta_prod: dict[str, dict] = {}
        if pqs is not None and all_ids:
            uuids = []
            for x in all_ids:
                try:
                    uuids.append(UUIDType(x))
                except Exception:
                    pass
            if uuids:
                for p in pqs.filter(id__in=uuids).only("id", "name", "code", "quantity", "purchase_price"):
                    meta_prod[str(p.id)] = {
                        "name": (getattr(p, "name", None) or "").strip() or "—",
                        "code": getattr(p, "code", None),
                        "quantity": getattr(p, "quantity", None) or 0,
                        "purchase_price": getattr(p, "purchase_price", None) or 0,
                    }

        combined_rows = []
        for k in all_ids:
            sd = s_map.get(k, {})
            td = t_map.get(k, {})
            mp = meta_prod.get(k, {})
            name = sd.get("name") or td.get("name") or mp.get("name") or "—"
            code = sd.get("code") if sd.get("code") is not None else td.get("code")
            if code is None:
                code = mp.get("code")
            qty_s = sd.get("qty_sold_dec", Z_QTY)
            rev = sd.get("revenue_dec", Z_MONEY)
            qty_t = int(td.get("qty_transferred_to_agents", 0))
            qcur = Decimal(str(mp.get("quantity", 0) or 0))
            pp = Decimal(str(mp.get("purchase_price", 0) or 0))
            stock_val = _money(qcur * pp)
            combined_rows.append({
                "product_id": k,
                "name": name,
                "code": code,
                "qty_sold": _qty_str(qty_s),
                "revenue": str(_money(rev)),
                "qty_transferred_to_agents": qty_t,
                "current_stock": _qty_str(qcur),
                "stock_at_purchase_prices": str(stock_val),
                "_activity": qty_s + Decimal(qty_t),
            })

        combined_rows.sort(key=lambda x: (-x["_activity"], -Decimal(str(x["qty_sold"]))))
        for row in combined_rows:
            del row["_activity"]

        top_sold = []
        for sd in sorted(s_map.values(), key=lambda x: -x["qty_sold_dec"])[:limit]:
            top_sold.append({
                "product_id": sd["product_id"],
                "name": sd["name"],
                "code": sd["code"],
                "qty_sold": _qty_str(sd["qty_sold_dec"]),
                "revenue": str(_money(sd["revenue_dec"])),
            })

        top_transfers = []
        for td in sorted(t_map.values(), key=lambda x: -x["qty_transferred_to_agents"])[:limit]:
            top_transfers.append({
                "product_id": td["product_id"],
                "name": td["name"],
                "code": td["code"],
                "qty_transferred_to_agents": td["qty_transferred_to_agents"],
            })

        combined_out = combined_rows[:limit]

        tot_sold = sum((s_map[k]["qty_sold_dec"] for k in s_map), Z_QTY)
        tot_tr = sum(int(t_map[k]["qty_transferred_to_agents"]) for k in t_map)

        return {
            "tab": "procurement",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "limit": limit,
            },
            "meta": {
                "purchase_note": (
                    "Оприходование от поставщика (POST /api/main/suppliers/<id>/receipt/) не пишется в отдельный журнал; "
                    "в отчёте «отгрузка» — сумма qty_transferred по передачам агентам (ManufactureSubreal) за период."
                ),
            },
            "cards": {
                "catalog_products_count": catalog_products_count,
                "products_with_sales_or_transfers": len(all_ids),
                "total_qty_sold_period": _qty_str(tot_sold),
                "total_qty_transferred_to_agents_period": int(tot_tr),
            },
            "tables": {
                "top_by_sales": top_sold,
                "top_by_transfers_to_agents": top_transfers,
                "sold_vs_transfers": combined_out,
            },
        }

    # ─────────────────────────────────────────────────────────
    # USERS ANALYTICS
    # ─────────────────────────────────────────────────────────
    def _users_analytics(self, request, company, branch, period: Period):
        # Параметры для лимита результатов
        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param and limit_param.isdigit() else None
        
        users_performance = []
        shift_stats = {}

        Sale, SaleItem = get_sale_models()
        units_by_user: dict = {}
        products_by_user: dict = {}
        names_by_user: dict = {}
        if Sale and _model_has_field(Sale, "user"):
            paid_value = _choice_value(Sale, "Status", "PAID", "paid")
            dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"
            
            sqs = Sale.objects.filter(company=company, status=paid_value)
            if branch and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    sqs = sqs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sqs = sqs.filter(branch=branch)
            sqs = sqs.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})

            if (
                SaleItem is not None
                and _model_has_field(SaleItem, "sale")
                and _model_has_field(SaleItem, "quantity")
            ):
                si_qs = SaleItem.objects.filter(sale__in=sqs)
                units_by_user, products_by_user, names_by_user = _users_sold_products_by_user(si_qs, SaleItem)

            # User Performance - ДЕТАЛЬНО ВСЕ сотрудники
            user_query = (
                sqs.values(
                    "user_id",
                    "user__first_name",
                    "user__last_name",
                    "user__email",
                    "user__phone_number",
                )
                .annotate(
                    revenue=Coalesce(Sum("total"), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                    tx_count=Count("id"),
                )
                .order_by("-revenue")
            )
            if limit:
                user_query = user_query[:limit]
            
            for r in user_query:
                rev = _money(r["revenue"])
                txc = r["tx_count"] or 0
                uid = r["user_id"]
                sold_list = products_by_user.get(uid, []) if uid is not None else []
                users_performance.append({
                    "user_id": str(uid) if uid else None,
                    "user": _user_label(
                        None,
                        first_name=r.get("user__first_name"),
                        last_name=r.get("user__last_name"),
                        email=r.get("user__email"),
                        phone=r.get("user__phone_number"),
                        user_id=uid,
                    ),
                    "email": r.get("user__email"),
                    "phone": r.get("user__phone_number"),
                    "revenue": str(rev),
                    "transactions": txc,
                    "avg_check": str(_safe_div(rev, txc)),
                    "units_sold": _qty_str(units_by_user.get(uid, Z_QTY) if uid is not None else Z_QTY),
                    "products_sold_count": len(sold_list),
                    "product_names": names_by_user.get(uid, []) if uid is not None else [],
                    "sold_products": sold_list,
                })

        # Shift Performance - ПОЛНАЯ информация
        shifts_qs = CashShift.objects.filter(company=company)
        if branch:
            shifts_qs = shifts_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        shifts_qs = shifts_qs.filter(opened_at__gte=period.start, opened_at__lt=period.end)
        
        total_shifts = shifts_qs.count()
        closed_shifts = shifts_qs.filter(status=CashShift.Status.CLOSED).count()
        
        # Cash discrepancies - ВСЕ расхождения с деталями
        closed_with_data = shifts_qs.filter(status=CashShift.Status.CLOSED, closing_cash__isnull=False).order_by("-opened_at")
        discrepancies = []
        all_discrepancies_qs = []
        
        for shift in closed_with_data:
            diff = shift.cash_diff
            if abs(diff) > Decimal("0.01"):
                all_discrepancies_qs.append(shift)
        
        # Применяем лимит только если задан
        if limit:
            all_discrepancies_qs = all_discrepancies_qs[:limit]
            
        for shift in all_discrepancies_qs:
            diff = shift.cash_diff
            discrepancies.append({
                "shift_id": str(shift.id),
                "cashier": _user_label(shift.cashier) if shift.cashier else "Unknown",
                "cashbox": shift.cashbox.name if shift.cashbox else None,
                "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
                "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
                "expected_cash": str(_money(shift.expected_cash or Z_MONEY)),
                "closing_cash": str(_money(shift.closing_cash or Z_MONEY)),
                "diff": str(_money(diff)),
                "type": "shortage" if diff < 0 else "overage",
            })

        shift_stats = {
            "total": total_shifts,
            "closed": closed_shifts,
            "open": total_shifts - closed_shifts,
            "discrepancies_count": len(all_discrepancies_qs) if not limit else None,  # общее количество расхождений
        }

        return {
            "tab": "users",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "limit": limit,
            },
            "cards": shift_stats,
            "tables": {
                "users_performance": users_performance,
                "shift_discrepancies": discrepancies,
            },
        }

    # ─────────────────────────────────────────────────────────
    # FINANCE ANALYTICS
    # ─────────────────────────────────────────────────────────
    def _finance(self, request, company, branch, period: Period):
        from apps.construction.models import CashFlow
        
        # Параметры для лимита результатов
        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param and limit_param.isdigit() else None

        income_total = Z_MONEY
        expense_total = Z_MONEY
        net_flow = Z_MONEY
        expense_breakdown = []
        income_breakdown = []
        expense_items = []  # НОВОЕ: полный детальный список расходов
        income_items = []   # НОВОЕ: полный детальный список доходов

        # CashFlow Analysis
        cfqs = CashFlow.objects.filter(company=company, status=CashFlow.Status.APPROVED)
        if branch:
            cfqs = cfqs.filter(Q(branch=branch) | Q(branch__isnull=True))
        cfqs = cfqs.filter(created_at__gte=period.start, created_at__lt=period.end)

        income_total = (
            cfqs.filter(type=CashFlow.Type.INCOME)
            .aggregate(v=Coalesce(Sum("amount"), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD))["v"]
            or Z_MONEY
        )
        expense_total = (
            cfqs.filter(type=CashFlow.Type.EXPENSE)
            .aggregate(v=Coalesce(Sum("amount"), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD))["v"]
            or Z_MONEY
        )
        net_flow = _money(income_total - expense_total)

        # Expense Breakdown by Name - ДЕТАЛЬНО
        exp_query = (
            cfqs.filter(type=CashFlow.Type.EXPENSE)
            .values("name")
            .annotate(
                total=Coalesce(Sum("amount"), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                count=Count("id"),
            )
            .order_by("-total")
        )
        if limit:
            exp_query = exp_query[:limit]
            
        expense_breakdown = [
            {
                "name": r["name"] or "Без названия",
                "total": str(_money(r["total"])),
                "count": r["count"],
            }
            for r in exp_query
        ]

        # Income Breakdown by Name - ДЕТАЛЬНО
        inc_query = (
            cfqs.filter(type=CashFlow.Type.INCOME)
            .values("name")
            .annotate(
                total=Coalesce(Sum("amount"), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD),
                count=Count("id"),
            )
            .order_by("-total")
        )
        if limit:
            inc_query = inc_query[:limit]
            
        income_breakdown = [
            {
                "name": r["name"] or "Без названия",
                "total": str(_money(r["total"])),
                "count": r["count"],
            }
            for r in inc_query
        ]
        
        # ПОЛНЫЙ список всех расходов с деталями
        expense_qs = cfqs.filter(type=CashFlow.Type.EXPENSE).order_by("-created_at")
        if limit:
            expense_qs = expense_qs[:limit]
            
        expense_items = [
            {
                "id": str(cf.id),
                "name": cf.name or "Без названия",
                "amount": str(_money(cf.amount)),
                "cashbox": cf.cashbox.name if cf.cashbox else None,
                "shift": str(cf.shift_id) if hasattr(cf, "shift_id") and cf.shift_id else None,
                "created_at": cf.created_at.isoformat() if cf.created_at else None,
                "created_by": _user_label(cf.created_by) if hasattr(cf, "created_by") and cf.created_by else None,
                "description": getattr(cf, "description", None) or getattr(cf, "comment", None),
            }
            for cf in expense_qs
        ]
        
        # ПОЛНЫЙ список всех доходов с деталями
        income_qs = cfqs.filter(type=CashFlow.Type.INCOME).order_by("-created_at")
        if limit:
            income_qs = income_qs[:limit]
            
        income_items = [
            {
                "id": str(cf.id),
                "name": cf.name or "Без названия",
                "amount": str(_money(cf.amount)),
                "cashbox": cf.cashbox.name if cf.cashbox else None,
                "shift": str(cf.shift_id) if hasattr(cf, "shift_id") and cf.shift_id else None,
                "created_at": cf.created_at.isoformat() if cf.created_at else None,
                "created_by": _user_label(cf.created_by) if hasattr(cf, "created_by") and cf.created_by else None,
                "description": getattr(cf, "description", None) or getattr(cf, "comment", None),
            }
            for cf in income_qs
        ]

        return {
            "tab": "finance",
            "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
            "filters": {
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "limit": limit,
            },
            "cards": {
                "income_total": str(_money(income_total)),
                "expense_total": str(_money(expense_total)),
                "net_flow": str(net_flow),
                "income_count": cfqs.filter(type=CashFlow.Type.INCOME).count(),
                "expense_count": cfqs.filter(type=CashFlow.Type.EXPENSE).count(),
            },
            "tables": {
                "expense_breakdown": expense_breakdown,
                "income_breakdown": income_breakdown,
                "expense_items": expense_items,  # НОВОЕ: полный детальный список
                "income_items": income_items,    # НОВОЕ: полный детальный список
            },
        }

    # ─────────────────────────────────────────────────────────
    # SALARY (продавцы по чекам Sale.user)
    # ─────────────────────────────────────────────────────────
    def _salary(self, request, company, branch, period: Period):
        from apps.main.models import MarketSaleEmployeePayProfile

        Sale, _sale_item = get_sale_models()
        days = (period.end - period.start).days
        if days < 1:
            days = 1

        period_meta = {
            "from": period.start.isoformat(),
            "to": period.end.isoformat(),
        }
        filters_meta = {
            "branch": str(getattr(branch, "id", "")) if branch else None,
            "include_global": self._include_global(request),
        }

        if Sale is None:
            return {
                "tab": "salary",
                "period": period_meta,
                "filters": filters_meta,
                "cards": {},
                "charts": {},
                "tables": {},
                "rows": [],
                "detail": "Модель продаж не найдена.",
            }

        paid_value = _choice_value(Sale, "Status", "PAID", "paid")
        dt_field = "paid_at" if _model_has_field(Sale, "paid_at") else "created_at"

        def _apply_sale_branch(qs):
            if branch is not None and _model_has_field(Sale, "branch"):
                if self._include_global(request):
                    return qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                return qs.filter(branch=branch)
            if branch is None and _model_has_field(Sale, "branch"):
                return qs.filter(branch__isnull=True)
            return qs

        def _sale_qs_period():
            q = Sale.objects.filter(company=company, status=paid_value)
            q = q.filter(**{f"{dt_field}__gte": period.start, f"{dt_field}__lt": period.end})
            return _apply_sale_branch(q)

        prof_qs = MarketSaleEmployeePayProfile.objects.filter(company=company)
        if branch is not None:
            prof_qs = prof_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            prof_qs = prof_qs.filter(branch__isnull=True)

        effective_profiles: dict = {}
        for prof in prof_qs.select_related("user").order_by("user_id", "-branch_id"):
            if prof.user_id not in effective_profiles or prof.branch_id is not None:
                effective_profiles[prof.user_id] = prof

        payroll_user_ids = list(effective_profiles.keys())

        rows = []
        for prof in effective_profiles.values():
            sq = _sale_qs_period().filter(user_id=prof.user_id)

            agg = sq.aggregate(
                s=Coalesce(
                    Sum("total"),
                    Value(Z_MONEY, output_field=MONEY_FIELD),
                    output_field=MONEY_FIELD,
                ),
                c=Count("id"),
            )
            sales_total = agg["s"] or Z_MONEY
            sale_count = int(agg["c"] or 0)

            base_part = ((prof.monthly_base_salary or Z_MONEY) * Decimal(days) / Decimal("30")).quantize(
                Decimal("0.01")
            )
            pct = (prof.sales_percent or Z_MONEY) / Decimal("100")
            bonus = (sales_total * pct).quantize(Decimal("0.01"))

            scheme = prof.pay_scheme
            if scheme == MarketSaleEmployeePayProfile.PayScheme.SALARY:
                total_pay = base_part
            elif scheme == MarketSaleEmployeePayProfile.PayScheme.PERCENT:
                total_pay = bonus
            else:
                total_pay = (base_part + bonus).quantize(Decimal("0.01"))

            user = prof.user
            label = _user_label(user)
            rows.append(
                {
                    "user_id": str(prof.user_id),
                    "employee_label": label,
                    "profile_scope": "branch" if prof.branch_id else "global",
                    "pay_scheme": prof.pay_scheme,
                    "pay_scheme_label": prof.get_pay_scheme_display(),
                    "monthly_base_salary": str(prof.monthly_base_salary),
                    "sales_percent": str(prof.sales_percent),
                    "period_days": days,
                    "base_prorated": str(base_part),
                    "employee_sales_period": str(_money(sales_total)),
                    "percent_bonus": str(bonus),
                    "total": str(total_pay),
                    "sales_count": sale_count,
                }
            )

        rows.sort(key=lambda r: r["employee_label"].lower())

        total_payroll = Z_MONEY
        total_base_prorated = Z_MONEY
        total_percent_bonus = Z_MONEY
        total_staff_sales = Z_MONEY
        total_sales_count = 0
        for r in rows:
            total_payroll += Decimal(r["total"])
            total_base_prorated += Decimal(r["base_prorated"])
            total_percent_bonus += Decimal(r["percent_bonus"])
            total_staff_sales += Decimal(r["employee_sales_period"])
            total_sales_count += int(r.get("sales_count") or 0)

        total_payroll = _money(total_payroll)
        total_base_prorated = _money(total_base_prorated)
        total_percent_bonus = _money(total_percent_bonus)
        total_staff_sales = _money(total_staff_sales)

        by_scheme_map: dict[str, dict] = {}
        for r in rows:
            key = r["pay_scheme"]
            if key not in by_scheme_map:
                by_scheme_map[key] = {
                    "pay_scheme": key,
                    "pay_scheme_label": r["pay_scheme_label"],
                    "employees_count": 0,
                    "total_pay": Z_MONEY,
                    "total_base_prorated": Z_MONEY,
                    "total_percent_bonus": Z_MONEY,
                    "total_employee_sales": Z_MONEY,
                    "sales_count": 0,
                }
            b = by_scheme_map[key]
            b["employees_count"] += 1
            b["total_pay"] += Decimal(r["total"])
            b["total_base_prorated"] += Decimal(r["base_prorated"])
            b["total_percent_bonus"] += Decimal(r["percent_bonus"])
            b["total_employee_sales"] += Decimal(r["employee_sales_period"])
            b["sales_count"] += int(r.get("sales_count") or 0)

        by_scheme = []
        for b in by_scheme_map.values():
            by_scheme.append(
                {
                    **b,
                    "total_pay": str(_money(b["total_pay"])),
                    "total_base_prorated": str(_money(b["total_base_prorated"])),
                    "total_percent_bonus": str(_money(b["total_percent_bonus"])),
                    "total_employee_sales": str(_money(b["total_employee_sales"])),
                }
            )
        by_scheme.sort(key=lambda x: x["pay_scheme_label"])

        avg_per_employee = _safe_div(total_payroll, len(rows)) if rows else Z_MONEY

        blended_commission_pct = None
        if total_staff_sales > 0:
            blended_commission_pct = float(
                (total_percent_bonus / total_staff_sales * Decimal("100")).quantize(Decimal("0.01"))
            )

        staff_sales_by_day = []
        if payroll_user_ids:
            dq = (
                _sale_qs_period()
                .filter(user_id__in=payroll_user_ids)
                .annotate(d=TruncDate(dt_field))
                .values("d")
                .annotate(
                    v=Coalesce(
                        Sum("total"),
                        Value(Z_MONEY, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD,
                    ),
                    c=Count("id"),
                )
                .order_by("d")
            )
            staff_sales_by_day = [
                {
                    "date": r["d"].isoformat(),
                    "sales_total": str(_money(r["v"])),
                    "sales_count": r["c"] or 0,
                }
                for r in dq
                if r["d"]
            ]

        top_by_payroll = sorted(
            rows,
            key=lambda x: Decimal(x["total"]),
            reverse=True,
        )[:15]
        top_by_payroll = [
            {
                "user_id": r["user_id"],
                "employee_label": r["employee_label"],
                "total": r["total"],
                "pay_scheme": r["pay_scheme"],
            }
            for r in top_by_payroll
        ]

        return {
            "tab": "salary",
            "period": period_meta,
            "filters": filters_meta,
            "cards": {
                "employees_with_profile": len(rows),
                "total_payroll": str(total_payroll),
                "total_base_prorated": str(total_base_prorated),
                "total_percent_bonus": str(total_percent_bonus),
                "total_employee_sales": str(total_staff_sales),
                "sales_count": total_sales_count,
                "avg_payroll_per_employee": str(_money(avg_per_employee)),
                "blended_commission_rate_pct": blended_commission_pct,
            },
            "charts": {
                "staff_sales_by_day": staff_sales_by_day,
            },
            "tables": {
                "by_pay_scheme": by_scheme,
                "top_by_payroll": top_by_payroll,
            },
            "rows": rows,
        }
