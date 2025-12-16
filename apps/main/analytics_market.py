from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from decimal import Decimal

from django.apps import apps
from django.db.models import (
    Sum, Count, Q, F, Value,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import TruncDate, ExtractHour, ExtractWeekDay, Coalesce
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from apps.users.models import Branch
from apps.construction.models import CashShift


# ─────────────────────────────────────────────────────────────
# helpers (company/branch)
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
            memberships
            .filter(is_primary=True, branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if primary_m and primary_m.branch:
            return primary_m.branch

        any_m = (
            memberships
            .filter(branch__company_id=company_id)
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
    """
    owner/admin: может выбрать ?branch=UUID, иначе branch=None (вся компания)
    не owner/admin: фиксированный филиал
    """
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
        except (Branch.DoesNotExist, ValueError):
            pass

    setattr(request, "branch", None)
    return None


def _money(x) -> Decimal:
    try:
        return (x or Decimal("0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _safe_div(a: Decimal, b: int | Decimal) -> Decimal:
    if not b:
        return Decimal("0.00")
    return _money(Decimal(a) / Decimal(b))


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


@dataclass
class Period:
    start: datetime
    end: datetime  # exclusive


def _get_period(request) -> Period:
    """
    date_from/date_to (ISO).
    Если не дали — текущий месяц.
    end — EXCLUSIVE.
    """
    tz = timezone.get_current_timezone()
    now = timezone.now().astimezone(tz)

    df = _parse_dt(request.query_params.get("date_from"))
    dt = _parse_dt(request.query_params.get("date_to"))

    if df and timezone.is_naive(df):
        df = timezone.make_aware(df, tz)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, tz)

    if df and dt:
        # если date_to пришёл как дата без времени — делаем +1 день (exclusive)
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
    """
    TextChoices/Enum → берём .value максимально мягко.
    """
    enum = getattr(model, enum_name, None)
    v = getattr(enum, member, None)
    return getattr(v, "value", None) or str(v or fallback)


# ─────────────────────────────────────────────────────────────
# Sale models (LAZY)
# ─────────────────────────────────────────────────────────────
SALE_MODEL = None
SALE_ITEM_MODEL = None


def _guess_sale_models():
    Sale = None
    SaleItem = None

    # быстрые варианты
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

    # мягкий fallback (осторожно)
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
# Analytics API
# ─────────────────────────────────────────────────────────────
class AnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tab = (request.query_params.get("tab") or "sales").lower()

        company = _get_company(request.user)
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        branch = _get_active_branch(request)
        period = _get_period(request)

        if tab == "sales":
            return Response(self._sales(request, company, branch, period))
        if tab == "stock":
            return Response(self._stock(request, company, branch, period))
        if tab == "cashboxes":
            return Response(self._cashboxes(request, company, branch, period))
        if tab == "shifts":
            return Response(self._shifts(request, company, branch, period))

        return Response({"detail": "Unknown tab. Use: sales|stock|cashboxes|shifts"}, status=400)

    # ─────────────────────────────────────────────────────────
    # universal filters for Sale qs
    # ─────────────────────────────────────────────────────────
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

    def _include_global(self, request) -> bool:
        """
        include_global=1 → при выбранном branch включаем записи branch=NULL тоже
        """
        return (request.query_params.get("include_global") or "").strip() in ("1", "true", "yes", "on")

    # ─────────────────────────────────────────────────────────
    # SALES
    # ─────────────────────────────────────────────────────────
    def _sales(self, request, company, branch, period: Period):
        z = Decimal("0.00")

        revenue = z
        tx = 0
        clients = 0
        daily = []
        top_products = []
        documents = []

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
                revenue=Coalesce(Sum("total"), Value(z)),
                tx=Count("id"),
            )
            revenue = agg["revenue"] or z
            tx = agg["tx"] or 0

            if _model_has_field(Sale, "client"):
                clients = qs.values("client_id").exclude(client_id__isnull=True).distinct().count()
            else:
                clients = qs.values("user_id").exclude(user_id__isnull=True).distinct().count()

            daily_rows = (
                qs.annotate(d=TruncDate(dt_field))
                .values("d")
                .annotate(v=Coalesce(Sum("total"), Value(z)))
                .order_by("d")
            )
            daily = [{"date": r["d"].isoformat(), "value": str(_money(r["v"]))} for r in daily_rows]

            if SaleItem is not None and _model_has_field(SaleItem, "sale"):
                item_qs = SaleItem.objects.filter(sale__in=qs)

                if _model_has_field(SaleItem, "unit_price"):
                    revenue_expr = ExpressionWrapper(
                        F("quantity") * F("unit_price"),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                    item_rows = (
                        item_qs.values("product_id", "name_snapshot")
                        .annotate(
                            sold=Coalesce(Sum("quantity"), Value(0)),
                            revenue=Coalesce(Sum(revenue_expr), Value(z)),
                        )
                        .order_by("-revenue")[:5]
                    )
                else:
                    item_rows = (
                        item_qs.values("product_id", "name_snapshot")
                        .annotate(
                            sold=Coalesce(Sum("quantity"), Value(0)),
                            revenue=Value(z),
                        )
                        .order_by("-sold")[:5]
                    )

                top_products = [
                    {
                        "name": (r.get("name_snapshot") or "Товар"),
                        "sold": int(r.get("sold") or 0),
                        "revenue": str(_money(r.get("revenue") or z)),
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
            },
            "charts": {
                "sales_dynamics": daily,
            },
            "tables": {
                "top_products": top_products,
                "documents": documents,
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

        z = Decimal("0.00")

        total_products = 0
        categories_count = 0
        inventory_value = z
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
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
                inventory_value = (
                    pqs.aggregate(v=Coalesce(Sum(inv_expr), Value(z)))["v"]
                    or z
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

                # low_only=1 → показывать только low
                if (request.query_params.get("low_only") or "").strip() in ("1", "true", "yes", "on"):
                    pqs = low_qs

                low_count = low_qs.count()

                low_rows = low_qs.order_by(qty_field)[:10]
                for p in low_rows:
                    q = getattr(p, qty_field, 0) or 0
                    mn = getattr(p, min_field, None) if min_field else 5
                    status = "critical" if q <= max(1, int(mn) // 2) else "low"
                    low_list.append({
                        "name": getattr(p, "name", "Товар"),
                        "qty": int(q),
                        "min": int(mn) if mn is not None else None,
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

        # movement: units sold by day
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
                .annotate(units=Coalesce(Sum("quantity"), Value(0)))
                .order_by("d")
            )
            movement = [{"date": r["d"].isoformat(), "units": int(r["units"] or 0)} for r in rows]

            if inventory_value and inventory_value > Decimal("0"):
                rev = sqs.aggregate(v=Coalesce(Sum("total"), Value(Decimal("0.00"))))["v"] or Decimal("0.00")
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
        z = Decimal("0.00")

        revenue = z
        tx = 0
        avg_check = z
        cash_in_box = z

        hourly = []
        pay_pie = []
        pay_detail = []
        tx_week = []
        peak_hours = []

        Sale, _ = get_sale_models()
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
                revenue=Coalesce(Sum("total"), Value(z)),
                tx=Count("id"),
            )
            revenue = agg["revenue"] or z
            tx = agg["tx"] or 0
            avg_check = _safe_div(_money(revenue), tx)

            pm_field = "payment_method" if _model_has_field(Sale, "payment_method") else None
            if pm_field:
                rows = (
                    qs.values(pm_field)
                    .annotate(cnt=Count("id"), sm=Coalesce(Sum("total"), Value(z)))
                    .order_by("-sm")
                )
                total_sum = sum([r["sm"] or z for r in rows]) or z
                for r in rows:
                    name = r[pm_field] or "unknown"
                    cnt = int(r["cnt"] or 0)
                    sm = _money(r["sm"] or z)
                    share = float((sm / total_sum * 100).quantize(Decimal("0.1"))) if total_sum else 0.0
                    pay_detail.append({"method": name, "transactions": cnt, "sum": str(sm), "share": share})
                pay_pie = [{"name": d["method"], "percent": d["share"]} for d in pay_detail]

                cash_value = _choice_value(Sale, "PaymentMethod", "CASH", "cash")
                cash_in_box = qs.filter(**{pm_field: cash_value}).aggregate(v=Coalesce(Sum("total"), Value(z)))["v"] or z

            hour_rows = (
                qs.annotate(h=ExtractHour(dt_field))
                .values("h")
                .annotate(v=Coalesce(Sum("total"), Value(z)), cnt=Count("id"))
                .order_by("h")
            )
            hourly = [{"hour": int(r["h"]), "revenue": str(_money(r["v"])), "transactions": int(r["cnt"])} for r in hour_rows]

            wd_rows = (
                qs.annotate(wd=ExtractWeekDay(dt_field))
                .values("wd")
                .annotate(cnt=Count("id"))
                .order_by("wd")
            )
            tx_week = [{"weekday": int(r["wd"]), "transactions": int(r["cnt"])} for r in wd_rows]

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
        z = Decimal("0.00")

        qs = CashShift.objects.filter(company=company)
        if branch is not None:
            if self._include_global(request):
                qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
            else:
                qs = qs.filter(branch=branch)

        # extra filters
        cashbox_id = request.query_params.get("cashbox")
        cashier_id = request.query_params.get("cashier")
        status = (request.query_params.get("status") or "").strip().lower()

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

        revenue_total = z
        Sale, _ = get_sale_models()
        if Sale is not None and _model_has_field(Sale, "shift"):
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

            revenue_total = sqs.aggregate(v=Coalesce(Sum("total"), Value(z)))["v"] or z

        shifts_cnt = period_qs.count() or 1
        avg_revenue_per_shift = _safe_div(_money(revenue_total), shifts_cnt)

        # buckets by opened_at hour
        def bucket(h: int) -> str:
            if 6 <= h < 12:
                return "morning"
            if 12 <= h < 18:
                return "day"
            return "evening"

        bucket_map = {
            "morning": {"revenue": z, "transactions": 0},
            "day": {"revenue": z, "transactions": 0},
            "evening": {"revenue": z, "transactions": 0},
        }

        if Sale is not None and _model_has_field(Sale, "shift"):
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

            sale_rows = sqs.values("total", "shift__opened_at")
            for r in sale_rows:
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

        # active shifts table
        active_rows = []
        act = qs.filter(status=CashShift.Status.OPEN).select_related("cashier", "cashbox").order_by("-opened_at")[:50]
        for sh in act:
            active_rows.append({
                "cashier": _user_label(sh.cashier),
                "cashbox": getattr(sh.cashbox, "name", None) or f"Касса {sh.cashbox_id}",
                "opened_at": sh.opened_at.isoformat() if sh.opened_at else None,
                "sales": "0.00",
                "status": "open",
            })

        # best cashiers
        best_cashiers = []
        if Sale is not None and _model_has_field(Sale, "shift"):
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

            rows = (
                sqs.values(
                    "shift__cashier_id",
                    "shift__cashier__first_name",
                    "shift__cashier__last_name",
                    "shift__cashier__email",
                    "shift__cashier__phone_number",
                )
                .annotate(
                    revenue=Coalesce(Sum("total"), Value(z)),
                    tx=Count("id"),
                    shifts=Count("shift_id", distinct=True),
                )
                .order_by("-revenue")[:10]
            )

            for i, r in enumerate(rows, start=1):
                rev = _money(r["revenue"] or z)
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
                "branch": str(getattr(branch, "id", "")) if branch else None,
                "include_global": self._include_global(request),
                "cashbox": cashbox_id,
                "cashier": cashier_id,
                "status": status if status in ("open", "closed") else None,
            },
            "cards": {
                "active_shifts": active_cnt,
                "shifts_today": today_cnt,
                "avg_duration_hours": avg_duration_hours,
                "avg_revenue_per_shift": str(_money(avg_revenue_per_shift)),
            },
            "charts": {
                "sales_by_shift_bucket": sales_by_shift_bucket,
            },
            "tables": {
                "active_shifts": active_rows,
                "best_cashiers": best_cashiers,
            },
        }
