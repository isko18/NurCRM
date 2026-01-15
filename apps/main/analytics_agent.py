from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal
from itertools import groupby
from operator import attrgetter

from django.db.models import (
    Prefetch,
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
    AgentSaleAllocation,
    Sale,
    SaleItem,
)
from apps.users.models import User

try:
    from apps.main.cache_utils import cached_result
except ImportError:
    # Fallback если cache_utils не доступен
    def cached_result(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


# ─────────────────────────────────────────────────────────────
# typed defaults / output fields (FIX mixed types)
# ─────────────────────────────────────────────────────────────
Z_MONEY = Decimal("0.00")
Z_QTY = Decimal("0.000")

MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)


def _parse_period(request):
    """
    ?period=day|week|month|custom
    Понимает:
      - day:  ?date=YYYY-MM-DD ИЛИ ?date_from=YYYY-MM-DD
      - week: ?date_from=...&date_to=...
      - month/custom: ?date_from=...&date_to=...
    """
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

    # month default (последние 30 дней)
    date_to = raw_to or today
    date_from = raw_from or (date_to - timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return {"period": "month", "date_from": date_from, "date_to": date_to, "group_by": "day"}


@cached_result(timeout=getattr(settings, "CACHE_TIMEOUT_SHORT", 60), key_prefix="agent_on_hand")
def _compute_agent_on_hand(*, company, branch, agent) -> dict:
    """
    Остатки у агента на руках.
    """
    accepted_returns_qs = ReturnFromAgent.objects.filter(
        company=company,
        status=ReturnFromAgent.Status.ACCEPTED,
    )
    alloc_qs = AgentSaleAllocation.objects.filter(company=company)

    # FIX mixed types: qty может быть Decimal/Integer → приводим к QTY_FIELD
    sold_qty_expr = ExpressionWrapper(F("sale_allocations__qty"), output_field=QTY_FIELD)

    base = (
        ManufactureSubreal.objects
        .filter(company=company, agent=agent)
        .select_related("product")
        .prefetch_related(
            "acceptances",
            Prefetch("returns", queryset=accepted_returns_qs, to_attr="accepted_returns"),
            Prefetch("sale_allocations", queryset=alloc_qs, to_attr="prefetched_allocs"),
        )
        .annotate(
            sold_qty=Coalesce(
                Sum(sold_qty_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            )
        )
        .order_by("product_id", "-created_at")
    )

    if branch is not None:
        base = base.filter(branch=branch)
    else:
        base = base.filter(branch__isnull=True)

    total_qty = 0
    total_amount = Z_MONEY
    by_product_qty = []
    by_product_amount = []

    for product_id, subreals_iter in groupby(base, key=attrgetter("product_id")):
        subreals = list(subreals_iter)
        if not subreals:
            continue

        product = subreals[0].product if getattr(subreals[0], "product", None) else None
        if not product:
            continue

        price = getattr(product, "price", None) or Z_MONEY
        qty_on_hand = 0

        for s in subreals:
            accepted = int(getattr(s, "qty_accepted", 0) or 0)
            returned = int(getattr(s, "qty_returned", 0) or 0)

            sold = int(Decimal(getattr(s, "sold_qty", 0) or 0))
            if not sold and getattr(s, "prefetched_allocs", None) is not None:
                sold = sum(int(getattr(a, "qty", 0) or 0) for a in s.prefetched_allocs)

            qty_on_hand += max(accepted - returned - sold, 0)

        if qty_on_hand <= 0:
            continue

        amount = (Decimal(price) * Decimal(qty_on_hand)).quantize(Decimal("0.01"))
        total_qty += qty_on_hand
        total_amount += amount

        by_product_qty.append({
            "product_id": str(product.id),
            "product_name": getattr(product, "name", "") or "",
            "qty_on_hand": qty_on_hand,
        })
        by_product_amount.append({
            "product_id": str(product.id),
            "product_name": getattr(product, "name", "") or "",
            "qty_on_hand": qty_on_hand,
            "amount": float(amount),
        })

    return {
        "total_qty": total_qty,
        "total_amount": float(total_amount),
        "by_product_qty": by_product_qty,
        "by_product_amount": by_product_amount,
    }


@cached_result(timeout=getattr(settings, "CACHE_TIMEOUT_ANALYTICS", 600), key_prefix="analytics_agent")
def build_agent_analytics_payload(
    *,
    company,
    branch,
    agent,
    period,
    date_from,
    date_to,
    group_by="day",
):
    """
    Аналитика агента.
    """
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
    dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

    # ======================================================
    # П Е Р Е Д А Ч И
    # ======================================================
    sub_qs = ManufactureSubreal.objects.filter(
        company=company,
        agent=agent,
        created_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        sub_qs = sub_qs.filter(branch=branch)
    else:
        sub_qs = sub_qs.filter(branch__isnull=True)

    transfers_count = sub_qs.count()

    # FIX mixed types: qty_transferred может быть int/decimal
    qty_transferred_expr = ExpressionWrapper(F("qty_transferred"), output_field=QTY_FIELD)

    items_transferred = sub_qs.aggregate(
        s=Coalesce(
            Sum(qty_transferred_expr),
            V(Z_QTY, output_field=QTY_FIELD),
            output_field=QTY_FIELD,
        )
    )["s"] or Z_QTY

    # ======================================================
    # П Р И Ё М К И
    # ======================================================
    acc_qs = Acceptance.objects.filter(
        company=company,
        subreal__agent=agent,
        accepted_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        acc_qs = acc_qs.filter(subreal__branch=branch)
    else:
        acc_qs = acc_qs.filter(subreal__branch__isnull=True)

    acceptances_count = acc_qs.count()

    # ======================================================
    # П Р О Д А Ж И
    # ======================================================
    sales_qs = Sale.objects.filter(
        company=company,
        user=agent,
        created_at__range=(dt_from, dt_to),
        status=Sale.Status.PAID,
    )
    if branch is not None:
        sales_qs = sales_qs.filter(branch=branch)
    else:
        sales_qs = sales_qs.filter(branch__isnull=True)

    sales_count = sales_qs.count()
    sales_amount_dec = sales_qs.aggregate(
        s=Coalesce(
            Sum("total"),
            V(Z_MONEY, output_field=MONEY_FIELD),
            output_field=MONEY_FIELD,
        )
    )["s"] or Z_MONEY
    sales_amount = float(sales_amount_dec)

    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    qty_expr = ExpressionWrapper(F("quantity"), output_field=QTY_FIELD)
    revenue_expr = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY_FIELD)

    # ---------------- 1) продажи по товарам ----------------
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
        .order_by("-amount")
    )

    sales_by_product_amount = [
        {
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "amount": float(row["amount"] or Z_MONEY),
        }
        for row in sales_by_product_qs
    ]

    # ---------------- 2) продажи по датам (FIX mixed types) ----------------
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


    # ---------------- 3) распределение по товарам ----------------
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

    # ======================================================
    # Т О В А Р Ы  Н А  Р У К А Х
    # ======================================================
    on_hand = _compute_agent_on_hand(company=company, branch=branch, agent=agent)

    # ======================================================
    # П Е Р Е Д А Ч И  П О  Д Н Я М
    # ======================================================
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

    # ТОП товаров по передачам
    top_products_qs = (
        sub_qs
        .values("product_id", "product__name")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(
                Sum(qty_transferred_expr),
                V(Z_QTY, output_field=QTY_FIELD),
                output_field=QTY_FIELD,
            ),
        )
        .order_by("-items_transferred")[:10]
    )
    top_products_by_transfers = [
        {
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "transfers_count": row["transfers_count"],
            "items_transferred": float(row["items_transferred"] or Z_QTY),
        }
        for row in top_products_qs
    ]

    # история передач
    history_qs = sub_qs.select_related("product").order_by("-created_at")[:200]
    transfers_history = [
        {
            "id": str(s.id),
            "date": s.created_at,
            "product_id": str(s.product_id),
            "product_name": getattr(s.product, "name", "") if getattr(s, "product", None) else "",
            "qty": getattr(s, "qty_transferred", 0),
            "status": s.status,
            "status_label": s.get_status_display(),
        }
        for s in history_qs
    ]

    agent_payload = {
        "id": str(agent.id),
        "first_name": getattr(agent, "first_name", "") or "",
        "last_name": getattr(agent, "last_name", "") or "",
        "track_number": getattr(agent, "track_number", None),
    }

    return {
        "agent": agent_payload,
        "period": {"type": period, "date_from": date_from, "date_to": date_to},
        "summary": {
            "transfers_count": transfers_count,
            "acceptances_count": acceptances_count,
            "items_transferred": float(items_transferred or Z_QTY),
            "sales_count": sales_count,
            "sales_amount": sales_amount,
            "items_on_hand_qty": on_hand["total_qty"],
            "items_on_hand_amount": on_hand["total_amount"],
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "sales_by_product_amount": sales_by_product_amount,
            "sales_distribution_by_product": sales_distribution_by_product,
            "on_hand_by_product_qty": on_hand["by_product_qty"],
            "on_hand_by_product_amount": on_hand["by_product_amount"],
            "transfers_by_date": transfers_by_date,
            "top_products_by_transfers": top_products_by_transfers,
        },
        "transfers_history": transfers_history,
    }
