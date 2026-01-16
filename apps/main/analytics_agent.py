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
)
    # from django.db.models.functions import Coalesce, TruncDate
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .models import (
    ManufactureSubreal,
    Acceptance,
    ReturnFromAgent,
    AgentSaleAllocation,
    Sale,
    SaleItem,
)
from apps.users.models import User
from django.core.cache import cache
from django.conf import settings

try:
    from apps.main.cache_utils import cached_result
except ImportError:
    # Fallback если cache_utils не доступен
    def cached_result(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


def _parse_period(request):
    """
    ?period=day|week|month|custom

    Понимает оба варианта:
      - day:  ?date=2025-12-01 ИЛИ ?date_from=2025-12-01
      - week: ?date_from=...&date_to=...
      - month/custom: ?date_from=...&date_to=...

    Если что-то не передано или битое — берём дефолты.
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

    # ---------- DAY ----------
    if period == "day":
        # приоритет: ?date > ?date_from > ?date_to > today
        d = raw_date or raw_from or raw_to or today
        return {
            "period": "day",
            "date_from": d,
            "date_to": d,
            "group_by": "day",
        }

    # ---------- WEEK ----------
    if period == "week":
        # date_to: ?date_to > ?date > today
        date_to = raw_to or raw_date or today
        # date_from: ?date_from > (date_to - 6)
        date_from = raw_from or (date_to - timedelta(days=6))

        if date_from > date_to:
            date_from, date_to = date_to, date_from

        return {
            "period": "week",
            "date_from": date_from,
            "date_to": date_to,
            "group_by": "day",
        }

    # ---------- CUSTOM ----------
    if period == "custom":
        # если нет дат — по умолчанию последние 30 дней
        date_to = raw_to or today
        date_from = raw_from or (date_to - timedelta(days=29))

        if date_from > date_to:
            date_from, date_to = date_to, date_from

        return {
            "period": "custom",
            "date_from": date_from,
            "date_to": date_to,
            "group_by": "day",
        }

    # ---------- MONTH (по умолчанию) ----------
    date_to = raw_to or today
    date_from = raw_from or (date_to - timedelta(days=29))

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return {
        "period": "month",
        "date_from": date_from,
        "date_to": date_to,
        "group_by": "day",
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_SHORT, key_prefix="agent_on_hand")
def _compute_agent_on_hand(*, company, branch, agent) -> dict:
    """
    Остатки у агента на руках (логика максимально совпадает с /agents/me/products).
    Кэшируется на 1 минуту (CACHE_TIMEOUT_SHORT).
    """
    accepted_returns_qs = ReturnFromAgent.objects.filter(
        company=company,
        status=ReturnFromAgent.Status.ACCEPTED,
    )
    alloc_qs = AgentSaleAllocation.objects.filter(company=company)

    base = (
        ManufactureSubreal.objects
        .filter(company=company, agent=agent)
        .select_related("product")
        .prefetch_related(
            "acceptances",
            Prefetch(
                "returns",
                queryset=accepted_returns_qs,
                to_attr="accepted_returns",
            ),
            Prefetch(
                "sale_allocations",
                queryset=alloc_qs,
                to_attr="prefetched_allocs",
            ),
        )
        .annotate(sold_qty=Coalesce(Sum("sale_allocations__qty"), V(0)))
        .order_by("product_id", "-created_at")
    )

    # ВАЖНО: та же логика, что и в миксине
    if branch is not None:
        base = base.filter(branch=branch)
    else:
        base = base.filter(branch__isnull=True)

    total_qty = 0
    total_amount = Decimal("0.00")
    by_product_qty = []
    by_product_amount = []

    for product_id, subreals_iter in groupby(base, key=attrgetter("product_id")):
        subreals = list(subreals_iter)
        if not subreals:
            continue

        product = subreals[0].product if getattr(subreals[0], "product", None) else None
        if not product:
            continue

        price = getattr(product, "price", None) or Decimal("0.00")
        qty_on_hand = 0

        for s in subreals:
            accepted = int(s.qty_accepted or 0)
            returned = int(s.qty_returned or 0)

            sold = int(getattr(s, "sold_qty", 0) or 0)
            if not sold and getattr(s, "prefetched_allocs", None) is not None:
                sold = sum(int(a.qty or 0) for a in s.prefetched_allocs)

            qty_on_hand += max(accepted - returned - sold, 0)

        if qty_on_hand <= 0:
            continue

        amount = price * qty_on_hand
        total_qty += qty_on_hand
        total_amount += amount

        by_product_qty.append({
            "product_id": str(product.id),
            "product_name": product.name,
            "qty_on_hand": qty_on_hand,
        })
        by_product_amount.append({
            "product_id": str(product.id),
            "product_name": product.name,
            "qty_on_hand": qty_on_hand,
            "amount": float(amount),
        })

    return {
        "total_qty": total_qty,
        "total_amount": float(total_amount),
        "by_product_qty": by_product_qty,
        "by_product_amount": by_product_amount,
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="analytics_agent")
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
    Кэшируется на 10 минут (CACHE_TIMEOUT_ANALYTICS).
    """
    # ---- диапазон дат ----
    dt_from = timezone.make_aware(
        datetime.combine(date_from, datetime.min.time())
    )
    dt_to = timezone.make_aware(
        datetime.combine(date_to, datetime.max.time())
    )

    # ======================================================
    #              П Е Р Е Д А Ч И
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
    items_transferred = sub_qs.aggregate(
        s=Coalesce(Sum("qty_transferred"), V(0))
    )["s"] or 0

    # ======================================================
    #              П Р И Ё М К И
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
    #              П Р О Д А Ж И
    # ======================================================
    sales_qs = Sale.objects.filter(
        company=company,
        user=agent,
        created_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        sales_qs = sales_qs.filter(branch=branch)
    else:
        sales_qs = sales_qs.filter(branch__isnull=True)

    # считаем только оплаченные
    sales_qs = sales_qs.filter(status=Sale.Status.PAID)

    sales_count = sales_qs.count()
    sales_amount_dec = sales_qs.aggregate(
        s=Coalesce(Sum("total"), V(Decimal("0.00")))
    )["s"] or Decimal("0.00")
    sales_amount = float(sales_amount_dec)

    # ---------------- 1) продажи по товарам ----------------
    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    sales_by_product_qs = (
        items_qs
        .values("product_id", "product__name")
        .annotate(
            qty=Coalesce(Sum("quantity"), V(0)),
            amount=Coalesce(
                Sum(
                    F("quantity") * F("unit_price"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                V(Decimal("0.00")),
            ),
        )
        .order_by("-amount")
    )

    sales_by_product_amount = []
    for row in sales_by_product_qs:
        amount = row["amount"] or Decimal("0.00")
        sales_by_product_amount.append({
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "amount": float(amount),
        })

    # ---------------- 2) продажи по датам ----------------
    sales_by_date_qs = (
        items_qs
        .annotate(day=TruncDate("sale__created_at"))
        .values("day")
        .annotate(
            sales_count=Count("sale_id", distinct=True),
            items_sold=Coalesce(Sum("quantity"), V(0)),
            amount=Coalesce(
                Sum(
                    F("quantity") * F("unit_price"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                V(Decimal("0.00")),
            ),
        )
        .order_by("day")
    )

    sales_by_date = [
        {
            "date": row["day"],
            "sales_count": row["sales_count"],
            "sales_amount": float(row["amount"] or Decimal("0.00")),
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
    #        Т О В А Р Ы  Н А  Р У К А Х
    # ======================================================
    on_hand = _compute_agent_on_hand(
        company=company,
        branch=branch,
        agent=agent,
    )
    on_hand_by_product_qty = on_hand["by_product_qty"]
    on_hand_by_product_amount = on_hand["by_product_amount"]

    # ======================================================
    #      П Е Р Е Д А Ч И  П О  Д Н Я М
    # ======================================================
    transfers_by_date_qs = (
        sub_qs
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(Sum("qty_transferred"), V(0)),
        )
        .order_by("day")
    )
    transfers_by_date = [
        {
            "date": row["day"],
            "transfers_count": row["transfers_count"],
            "items_transferred": row["items_transferred"],
        }
        for row in transfers_by_date_qs
    ]

    # ТОП товаров по передачам
    top_products_qs = (
        sub_qs
        .values("product_id", "product__name")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(Sum("qty_transferred"), V(0)),
        )
        .order_by("-items_transferred")[:10]
    )
    top_products_by_transfers = [
        {
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "transfers_count": row["transfers_count"],
            "items_transferred": row["items_transferred"],
        }
        for row in top_products_qs
    ]

    # история передач
    history_qs = (
        sub_qs
        .select_related("product")
        .order_by("-created_at")[:200]
    )
    transfers_history = [
        {
            "id": str(s.id),
            "date": s.created_at,
            "product_id": str(s.product_id),
            "product_name": getattr(s.product, "name", ""),
            "qty": s.qty_transferred,
            "status": s.status,
            "status_label": s.get_status_display(),
        }
        for s in history_qs
    ]

    # базовая инфа по агенту
    agent_payload = {
        "id": str(agent.id),
        "first_name": getattr(agent, "first_name", "") or "",
        "last_name": getattr(agent, "last_name", "") or "",
        "track_number": getattr(agent, "track_number", None),
    }

    return {
        "agent": agent_payload,
        "period": {
            "type": period,
            "date_from": date_from,
            "date_to": date_to,
        },
        "summary": {
            "transfers_count": transfers_count,
            "acceptances_count": acceptances_count,
            "items_transferred": items_transferred,
            "sales_count": sales_count,
            "sales_amount": sales_amount,
            "items_on_hand_qty": on_hand["total_qty"],
            "items_on_hand_amount": on_hand["total_amount"],
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "sales_by_product_amount": sales_by_product_amount,
            "sales_distribution_by_product": sales_distribution_by_product,
            "on_hand_by_product_qty": on_hand_by_product_qty,
            "on_hand_by_product_amount": on_hand_by_product_amount,
            "transfers_by_date": transfers_by_date,
            "top_products_by_transfers": top_products_by_transfers,
        },
        "transfers_history": transfers_history,
    }
