from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal

from django.db.models import (
    Sum,
    Count,
    Value as V,
    F,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.conf import settings

from .models import (
    ManufactureSubreal,
    Acceptance,
    ReturnFromAgent,
    Sale,
    SaleItem,
)
from apps.users.models import User

try:
    from apps.main.cache_utils import cached_result
except ImportError:
    def cached_result(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


Z_MONEY = Decimal("0.00")
Z_QTY = Decimal("0.000")

MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)


def _parse_period(request):
    q = getattr(request, "query_params", getattr(request, "GET", {}))
    today = timezone.localdate()

    def _parse(name) -> date | None:
        v = q.get(name)
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except Exception:
            return None

    period = (q.get("period") or "month").lower()

    raw_date = _parse("date")
    raw_from = _parse("date_from")
    raw_to = _parse("date_to")

    if period == "day":
        d = raw_date or raw_from or raw_to or today
        return {"period": "day", "date_from": d, "date_to": d, "group_by": "day"}

    if period == "week":
        date_to = raw_to or raw_date or today
        date_from = raw_from or (date_to - timedelta(days=6))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "week", "date_from": date_from, "date_to": date_to, "group_by": "day"}

    if period == "custom":
        date_to = raw_to or today
        date_from = raw_from or (date_to - timedelta(days=29))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "custom", "date_from": date_from, "date_to": date_to, "group_by": "day"}

    date_to = raw_to or today
    date_from = raw_from or (date_to - timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return {"period": "month", "date_from": date_from, "date_to": date_to, "group_by": "day"}


def _apply_branch(qs, *, branch, field_name: str):
    """
    field_name: 'branch' или 'subreal__branch' и т.п.
    """
    if branch is not None:
        return qs.filter(**{field_name: branch})
    return qs.filter(**{f"{field_name}__isnull": True})


@cached_result(timeout=getattr(settings, "CACHE_TIMEOUT_ANALYTICS", 600), key_prefix="analytics_owner")
def build_owner_analytics_payload(
    *,
    company,
    branch,
    period,
    date_from,
    date_to,
    group_by="day",
):
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
    dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

    # ─────────────────────────────────────────────────────────────
    # TRANSFERS (ManufactureSubreal)
    # ─────────────────────────────────────────────────────────────
    sub_qs = ManufactureSubreal.objects.filter(company=company, created_at__range=(dt_from, dt_to))
    sub_qs = _apply_branch(sub_qs, branch=branch, field_name="branch")

    qty_transferred_expr = ExpressionWrapper(F("qty_transferred"), output_field=QTY_FIELD)

    transfers_count = sub_qs.count()
    items_transferred = sub_qs.aggregate(
        v=Coalesce(
            Sum(qty_transferred_expr),
            V(Z_QTY, output_field=QTY_FIELD),
            output_field=QTY_FIELD,
        )
    )["v"] or Z_QTY

    transfers_by_date_qs = (
        sub_qs
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(
                Sum(qty_transferred_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
        )
        .order_by("day")
    )
    transfers_by_date = [
        {
            "date": row["day"],
            "transfers_count": row["transfers_count"],
            "items_transferred": float(row["items_transferred"] or Z_QTY),
        }
        for row in transfers_by_date_qs
    ]

    top_products_by_transfers_qs = (
        sub_qs
        .values("product_id", "product__name")
        .annotate(
            items_transferred=Coalesce(
                Sum(qty_transferred_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
            transfers_count=Count("id"),
        )
        .order_by("-items_transferred")[:10]
    )
    top_products_by_transfers = [
        {
            "product_id": str(r["product_id"]),
            "product_name": r["product__name"],
            "transfers_count": r["transfers_count"],
            "items_transferred": float(r["items_transferred"] or Z_QTY),
        }
        for r in top_products_by_transfers_qs
    ]

    top_agents_by_transfers_qs = (
        sub_qs
        .values("agent_id", "agent__first_name", "agent__last_name")
        .annotate(
            items_transferred=Coalesce(
                Sum(qty_transferred_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
            transfers_count=Count("id"),
        )
        .order_by("-items_transferred")[:10]
    )
    top_agents_by_transfers = [
        {
            "agent_id": str(r["agent_id"]),
            "agent_name": (f"{r.get('agent__first_name') or ''} {r.get('agent__last_name') or ''}").strip() or "—",
            "transfers_count": r["transfers_count"],
            "items_transferred": float(r["items_transferred"] or Z_QTY),
        }
        for r in top_agents_by_transfers_qs
    ]

    # ─────────────────────────────────────────────────────────────
    # ACCEPTANCES
    # ─────────────────────────────────────────────────────────────
    acc_qs = Acceptance.objects.filter(company=company, accepted_at__range=(dt_from, dt_to))
    acc_qs = _apply_branch(acc_qs, branch=branch, field_name="subreal__branch")
    acceptances_count = acc_qs.count()

    # ─────────────────────────────────────────────────────────────
    # RETURNS (optional but useful)
    # ─────────────────────────────────────────────────────────────
    ret_qs = ReturnFromAgent.objects.filter(company=company)
    # если у модели есть accepted_at/created_at — берём что есть
    if hasattr(ReturnFromAgent, "accepted_at"):
        ret_qs = ret_qs.filter(accepted_at__range=(dt_from, dt_to))
    elif hasattr(ReturnFromAgent, "created_at"):
        ret_qs = ret_qs.filter(created_at__range=(dt_from, dt_to))
    ret_qs = _apply_branch(ret_qs, branch=branch, field_name="subreal__branch") if "subreal" in {f.name for f in ReturnFromAgent._meta.get_fields()} else ret_qs
    returns_count = ret_qs.count()

    # ─────────────────────────────────────────────────────────────
    # SALES
    # ─────────────────────────────────────────────────────────────
    sales_qs = Sale.objects.filter(company=company, created_at__range=(dt_from, dt_to), status=Sale.Status.PAID)
    sales_qs = _apply_branch(sales_qs, branch=branch, field_name="branch")

    sales_count = sales_qs.count()
    sales_amount_dec = sales_qs.aggregate(
        v=Coalesce(
            Sum("total"),
            V(Z_MONEY, output_field=MONEY_FIELD),
            output_field=MONEY_FIELD,
        )
    )["v"] or Z_MONEY
    sales_amount = float(sales_amount_dec)

    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    qty_expr = ExpressionWrapper(F("quantity"), output_field=QTY_FIELD)
    revenue_expr = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY_FIELD)

    sales_by_date_qs = (
        items_qs
        .annotate(day=TruncDate("sale__created_at"))
        .values("day")
        .annotate(
            sales_count=Count("sale_id", distinct=True),
            items_sold=Coalesce(
                Sum(qty_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
            amount=Coalesce(
                Sum(revenue_expr),
                V(Z_MONEY, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            ),
        )
        .order_by("day")
    )
    sales_by_date = [
        {
            "date": row["day"],
            "sales_count": row["sales_count"],
            "sales_amount": float(row["amount"] or Z_MONEY),
        }
        for row in sales_by_date_qs
    ]

    sales_by_product_qs = (
        items_qs
        .values("product_id", "product__name")
        .annotate(
            qty=Coalesce(
                Sum(qty_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
            amount=Coalesce(
                Sum(revenue_expr),
                V(Z_MONEY, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            ),
        )
        .order_by("-amount")[:20]
    )
    sales_by_product_amount = [
        {
            "product_id": str(r["product_id"]),
            "product_name": r["product__name"],
            "amount": float(r["amount"] or Z_MONEY),
        }
        for r in sales_by_product_qs
    ]

    sales_by_agent_qs = (
        sales_qs
        .values("user_id", "user__first_name", "user__last_name")
        .annotate(
            amount=Coalesce(
                Sum("total"),
                V(Z_MONEY, output_field=MONEY_FIELD),
                output_field=MONEY_FIELD,
            ),
            tx=Count("id"),
        )
        .order_by("-amount")[:20]
    )
    sales_by_agent_amount = [
        {
            "agent_id": str(r["user_id"]),
            "agent_name": (f"{r.get('user__first_name') or ''} {r.get('user__last_name') or ''}").strip() or "—",
            "amount": float(r["amount"] or Z_MONEY),
            "transactions": int(r["tx"] or 0),
        }
        for r in sales_by_agent_qs
    ]

    # distribution by product
    sales_distribution_by_product = []
    if sales_amount > 0:
        for row in sales_by_product_amount:
            amount = row["amount"]
            sales_distribution_by_product.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "amount": amount,
                "percent": round(amount * 100.0 / sales_amount, 2),
            })

    # summary
    unique_agents = sub_qs.values("agent_id").exclude(agent_id__isnull=True).distinct().count()

    return {
        "period": {"type": period, "date_from": date_from, "date_to": date_to},
        "filters": {
            "branch": str(getattr(branch, "id", "")) if branch else None,
            "group_by": group_by,
        },
        "summary": {
            "unique_agents": unique_agents,
            "transfers_count": transfers_count,
            "items_transferred": float(items_transferred or Z_QTY),
            "acceptances_count": acceptances_count,
            "returns_count": returns_count,
            "sales_count": sales_count,
            "sales_amount": sales_amount,
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "sales_by_product_amount": sales_by_product_amount,
            "sales_distribution_by_product": sales_distribution_by_product,
            "sales_by_agent_amount": sales_by_agent_amount,
            "transfers_by_date": transfers_by_date,
            "top_products_by_transfers": top_products_by_transfers,
            "top_agents_by_transfers": top_agents_by_transfers,
        },
    }
