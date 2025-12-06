from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from operator import attrgetter

from django.db.models import Sum, Count, F, Value as V, DecimalField
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.main.models import (
    ManufactureSubreal,
    Acceptance,
    ReturnFromAgent,
    AgentSaleAllocation,
    Sale,
    SaleItem,
    Product,
)


def _parse_period(request):
    """
    ?period=day|week|month|custom
    ?date_from=YYYY-MM-DD
    ?date_to=YYYY-MM-DD
    """
    q = request.query_params
    period = (q.get("period") or "month").lower()
    today = timezone.localdate()

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
        return {"period": "day", "date_from": d, "date_to": d}

    if period == "week":
        date_to = _parse_date("date_to", today)
        date_from = _parse_date("date_from", date_to - timedelta(days=6))
        return {"period": "week", "date_from": date_from, "date_to": date_to}

    if period == "custom":
        date_to = _parse_date("date_to", today)
        date_from = _parse_date("date_from", date_to - timedelta(days=29))
        return {"period": "custom", "date_from": date_from, "date_to": date_to}

    # по умолчанию: месяц = последние 30 дней
    date_to = _parse_date("date_to", today)
    date_from = _parse_date("date_from", date_to - timedelta(days=29))
    return {"period": "month", "date_from": date_from, "date_to": date_to}


def _branch_filter(qs, branch):
    """
    У тебя в моделях паттерн один и тот же:
    branch == NULL → глобально, иначе конкретный филиал.
    """
    if branch is not None:
        return qs.filter(branch=branch)
    return qs.filter(branch__isnull=True)


def _compute_on_hand(company, branch, agent):
    """
    Что сейчас на руках у агента:
    из ManufactureSubreal с учётом принятых / возвращённых / проданных.
    """
    sub_qs = (
        ManufactureSubreal.objects
        .filter(company=company, agent=agent)
    )
    sub_qs = _branch_filter(sub_qs, branch)

    # заранее подтянем product и аллокации
    sub_qs = (
        sub_qs
        .select_related("product")
        .prefetch_related("sale_allocations")
        .order_by("product_id", "-created_at")
    )

    total_qty = 0
    total_amount = Decimal("0.00")
    by_product_qty = []
    by_product_amount = []

    for product_id, sub_list in groupby(sub_qs, key=attrgetter("product_id")):
        sub_list = list(sub_list)
        product = sub_list[0].product
        if not product:
            continue

        price = product.price or Decimal("0.00")
        qty_on_hand = 0

        for s in sub_list:
            accepted = int(s.qty_accepted or 0)
            returned = int(s.qty_returned or 0)
            # уже продали через AgentSaleAllocation
            sold = sum(int(a.qty or 0) for a in s.sale_allocations.all())
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


def build_agent_analytics(company, branch, agent, *, date_from, date_to, period: str):
    """
    Основной payload для "Моя аналитика" агента.
    company/branch/agent — это текущая компания/филиал/пользователь.
    """
    dt_from = datetime.combine(date_from, datetime.min.time())
    dt_to = datetime.combine(date_to, datetime.max.time())
    dt_from = timezone.make_aware(dt_from)
    dt_to = timezone.make_aware(dt_to)

    # ===== ПЕРЕДАЧИ =====
    sub_qs = ManufactureSubreal.objects.filter(
        company=company,
        agent=agent,
        created_at__range=(dt_from, dt_to),
    )
    sub_qs = _branch_filter(sub_qs, branch)

    transfers_count = sub_qs.count()
    items_transferred = sub_qs.aggregate(
        s=Coalesce(Sum("qty_transferred"), V(0))
    )["s"] or 0

    # ===== ПРИЁМКИ =====
    acc_qs = Acceptance.objects.filter(
        company=company,
        subreal__agent=agent,
        accepted_at__range=(dt_from, dt_to),
    )
    acc_qs = _branch_filter(acc_qs, branch)
    acceptances_count = acc_qs.count()

    # ===== ПРОДАЖИ (только PAID) =====
    sales_qs = Sale.objects.filter(
        company=company,
        user=agent,
        created_at__range=(dt_from, dt_to),
        status=Sale.Status.PAID,
    )
    sales_qs = _branch_filter(sales_qs, branch)

    sales_count = sales_qs.count()
    sales_amount_dec = sales_qs.aggregate(
        s=Coalesce(Sum("total"), V(Decimal("0.00")))
    )["s"] or Decimal("0.00")
    sales_amount = float(sales_amount_dec)

    # подробности по позициям
    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    # продажи по товарам (сумма)
    by_product_qs = (
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
    sales_by_product_amount = [
        {
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "amount": float(row["amount"] or Decimal("0.00")),
            "qty": row["qty"],
        }
        for row in by_product_qs
    ]

    # продажи по датам
    by_date_qs = (
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
            "items_sold": row["items_sold"],
        }
        for row in by_date_qs
    ]

    # распределение по товарам (проценты)
    distribution = []
    if sales_amount > 0:
        for item in sales_by_product_amount:
            amt = item["amount"]
            distribution.append({
                **item,
                "percent": round(amt * 100.0 / sales_amount, 2),
            })

    # товары на руках
    on_hand = _compute_on_hand(company, branch, agent)

    # передачи по датам (для графика)
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

    return {
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
            "sales_distribution_by_product": distribution,
            "on_hand_by_product_qty": on_hand["by_product_qty"],
            "on_hand_by_product_amount": on_hand["by_product_amount"],
            "transfers_by_date": transfers_by_date,
        },
    }
