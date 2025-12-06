from datetime import date, timedelta, datetime
from decimal import Decimal
from itertools import groupby
from operator import attrgetter

from django.db.models import Prefetch, Sum, Count, Value as V
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .models import (
    ManufactureSubreal,
    Acceptance,
    ReturnFromAgent,
    AgentSaleAllocation,
)
from apps.users.models import User


def _parse_period(request):
    """
    Параметры:
      ?period=day|week|month|custom
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD

    Если даты не переданы — подставляем по period.
    """
    q = request.query_params
    period = (q.get("period") or "month").lower()
    today = timezone.now().date()

    def _parse_date(name, default):
        v = q.get(name)
        if not v:
            return default
        try:
            return date.fromisoformat(v)
        except Exception:
            return default

    if period == "day":
        d = _parse_date("date", today)
        return {
            "period": "day",
            "date_from": d,
            "date_to": d,
            "group_by": "day",
        }

    if period == "week":
        # последние 7 дней, включая сегодня
        date_to = _parse_date("date_to", today)
        date_from = _parse_date("date_from", date_to - timedelta(days=6))
        return {
            "period": "week",
            "date_from": date_from,
            "date_to": date_to,
            "group_by": "day",
        }

    if period == "custom":
        date_to = _parse_date("date_to", today)
        date_from = _parse_date("date_from", date_to - timedelta(days=29))
        return {
            "period": "custom",
            "date_from": date_from,
            "date_to": date_to,
            "group_by": "day",
        }

    # по умолчанию — месяц (последние 30 дней)
    date_to = _parse_date("date_to", today)
    date_from = _parse_date("date_from", date_to - timedelta(days=29))
    return {
        "period": "month",
        "date_from": date_from,
        "date_to": date_to,
        "group_by": "day",
    }


def _compute_agent_on_hand(*, company, branch, agent) -> dict:
    """
    Считаем:
      - total_qty      — всего штук на руках
      - total_amount   — их стоимость (qty * product.price)
      - by_product_qty — список по каждому товару (qty)
      - by_product_amount — список по каждому товару (qty + amount)

    Логика такая же, как в AgentMyProductsListAPIView.
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
    if branch is not None:
        base = base.filter(branch=branch)

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
    Считает всё, что нужно для экрана аналитики агента.
    ВСЯ математика по деньгам делается в Python, без Sum(F()*F()).
    """
    # ---- Фильтры по датам ----
    dt_from = timezone.make_aware(
        datetime.combine(date_from, datetime.min.time())
    )
    dt_to = timezone.make_aware(
        datetime.combine(date_to, datetime.max.time())
    )

    # ---- Передачи ----
    sub_qs = ManufactureSubreal.objects.filter(
        company=company,
        agent=agent,
        created_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        sub_qs = sub_qs.filter(branch=branch)

    transfers_count = sub_qs.count()
    items_transferred = sub_qs.aggregate(
        s=Coalesce(Sum("qty_transferred"), V(0))
    )["s"] or 0

    # ---- Приёмки ----
    acc_qs = Acceptance.objects.filter(
        company=company,
        subreal__agent=agent,
        accepted_at__range=(dt_from, dt_to),
    )
    # 🔧 ВАЖНО: фильтруем по филиалу через subreal, а не по полю branch у Acceptance
    if branch is not None:
        acc_qs = acc_qs.filter(subreal__branch=branch)

    acceptances_count = acc_qs.count()

    # ---- Продажи (по AgentSaleAllocation) ----
    sale_alloc_qs = AgentSaleAllocation.objects.filter(
        company=company,
        agent=agent,
        sale__created_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        sale_alloc_qs = sale_alloc_qs.filter(subreal__branch=branch)

    sales_count = sale_alloc_qs.values("sale_id").distinct().count()

    # 1) Продажи по товарам: qty суммируем в БД, amount считаем в Python
    sales_by_product_qs = (
        sale_alloc_qs
        .values("product_id", "product__name", "product__price")
        .annotate(
            qty=Coalesce(Sum("qty"), V(0)),
        )
        .order_by("-qty")
    )

    sales_amount = 0.0
    sales_by_product_amount = []

    for row in sales_by_product_qs:
        qty = int(row["qty"] or 0)
        price = row["product__price"] or Decimal("0.00")
        amount_dec = price * qty
        amount = float(amount_dec)
        sales_amount += amount

        sales_by_product_amount.append({
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "amount": amount,
        })

    # 2) Продажи по датам: sales_count / items_sold считаем в БД,
    #    суммы по дням считаем в Python из "сырых" аллокаций.
    sales_by_date_base = (
        sale_alloc_qs
        .annotate(day=TruncDate("sale__created_at"))
        .values("day")
        .annotate(
            sales_count=Count("sale_id", distinct=True),
            items_sold=Coalesce(Sum("qty"), V(0)),
        )
        .order_by("day")
    )

    # словарь: день -> сумма денег
    amounts_by_day = {}
    raw_rows = (
        sale_alloc_qs
        .annotate(day=TruncDate("sale__created_at"))
        .values("day", "qty", "product__price")
    )
    for r in raw_rows:
        d = r["day"]
        qty = int(r["qty"] or 0)
        price = r["product__price"] or Decimal("0.00")
        prev = amounts_by_day.get(d) or Decimal("0.00")
        amounts_by_day[d] = prev + (price * qty)

    sales_by_date = [
        {
            "date": row["day"],
            "sales_count": row["sales_count"],
            "sales_amount": float(
                amounts_by_day.get(row["day"], Decimal("0.00"))
            ),
        }
        for row in sales_by_date_base
    ]

    # 3) Распределение по товарам (проценты)
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

    # ---- Товары на руках (сейчас) ----
    on_hand = _compute_agent_on_hand(
        company=company,
        branch=branch,
        agent=agent,
    )

    on_hand_by_product_qty = on_hand["by_product_qty"]
    on_hand_by_product_amount = on_hand["by_product_amount"]

    # ---- Передачи по датам ----
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

    # ---- Топ товаров по передачам ----
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

    # ---- История передач ----
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

    # ---- Базовая инфа по агенту ----
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
