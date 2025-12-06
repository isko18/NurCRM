from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Sum, F, Count
from django.db.models.functions import Coalesce, TruncDate

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.models import User  # путь поправь, если другой
# from apps.main.api.mixins import CompanyBranchRestrictedMixin  # ← поправь путь к миксину

from apps.main.models import (  # если файл в другом app — поменяй импорт
    ManufactureSubreal,
    Acceptance,
    AgentSaleAllocation,
    Product,
    Sale,
)


# -------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# -------------------------------------------------

def _is_owner_like(user) -> bool:
    """
    Проверка, что это владелец/админ.
    Если у тебя свои флаги (is_owner, role и т.п.) — добавь сюда.
    """
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "is_staff", False)
        or getattr(user, "is_owner", False)
        or getattr(user, "is_company_owner", False)
    )


def _parse_period(request):
    """
    Разбираем ?period=day|week|month|custom + date_from/date_to.
    Возвращаем dict: {period, date_from, date_to}
    Все даты — date, не datetime.
    """
    period = (request.query_params.get("period") or "month").lower()
    today = timezone.localdate()

    if period == "day":
        date_from = date_to = today

    elif period == "week":
        date_to = today
        date_from = today - timedelta(days=6)

    elif period == "month":
        # текущий месяц: с 1-го числа до сегодня
        date_to = today
        date_from = today.replace(day=1)

    elif period == "custom":
        df_raw = request.query_params.get("date_from")
        dt_raw = request.query_params.get("date_to")

        date_from = parse_date(df_raw) if df_raw else None
        date_to = parse_date(dt_raw) if dt_raw else None

        # если ничего не передали — по умолчанию неделя
        if not date_from and not date_to:
            date_to = today
            date_from = today - timedelta(days=6)
        elif date_from and not date_to:
            date_to = today
        elif date_to and not date_from:
            date_from = date_to - timedelta(days=6)

    else:
        # неизвестный period -> месяц
        period = "month"
        date_to = today
        date_from = today.replace(day=1)

    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    return {
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
    }


def build_agent_analytics_payload(
    *,
    company,
    branch,
    agent,
    period,
    date_from,
    date_to,
):
    """
    Строит payload под UI “Моя аналитика” агента.

    company / branch — из миксина, agent — User.
    date_from / date_to — date (не datetime), включительно.
    """

    # ---------- фильтры по компании/филиалу ----------
    subreal_filter = {"company": company, "agent": agent}
    if branch:
        subreal_filter["branch"] = branch

    period_filter_created = {}
    if date_from:
        period_filter_created["created_at__date__gte"] = date_from
    if date_to:
        period_filter_created["created_at__date__lte"] = date_to

    # ---------- 1. Передачи за период ----------
    subreals_period_qs = ManufactureSubreal.objects.filter(
        **subreal_filter,
        **period_filter_created,
    )

    transfers_count = subreals_period_qs.count()
    transferred_qty = (
        subreals_period_qs.aggregate(
            s=Coalesce(Sum("qty_transferred"), 0)
        )["s"] or 0
    )

    # ---------- 2. Приёмки за период ----------
    acc_filter = {
        "company": company,
        "subreal__agent": agent,
    }
    if branch:
        acc_filter["branch"] = branch
    if date_from:
        acc_filter["accepted_at__date__gte"] = date_from
    if date_to:
        acc_filter["accepted_at__date__lte"] = date_to

    acceptances_count = Acceptance.objects.filter(**acc_filter).count()

    # ---------- 3. Продажи агента за период ----------
    alloc_filter = {
        "company": company,
        "agent": agent,
        "sale__status": Sale.Status.PAID,
    }
    if branch:
        alloc_filter["sale__branch"] = branch
    if date_from:
        alloc_filter["sale__created_at__date__gte"] = date_from
    if date_to:
        alloc_filter["sale__created_at__date__lte"] = date_to

    alloc_qs = (
        AgentSaleAllocation.objects
        .filter(**alloc_filter)
        .select_related("sale", "sale_item", "product")
    )

    # сколько разных продаж (документов)
    sales_count = (
        alloc_qs.values("sale_id")
        .distinct()
        .count()
    )

    # сумма продаж агента = Σ(qty * unit_price)
    sales_amount = (
        alloc_qs.aggregate(
            s=Coalesce(
                Sum(F("qty") * F("sale_item__unit_price")),
                Decimal("0.00"),
            )
        )["s"] or Decimal("0.00")
    ).quantize(Decimal("0.01"))

    # ---------- 4. График: продажи по датам ----------
    sales_by_date = []
    if date_from and date_to:
        by_date_qs = (
            alloc_qs
            .annotate(day=TruncDate("sale__created_at"))
            .values("day")
            .annotate(
                amount=Coalesce(
                    Sum(F("qty") * F("sale_item__unit_price")),
                    Decimal("0.00"),
                ),
                sales_count=Count("sale_id", distinct=True),
            )
            .order_by("day")
        )

        for row in by_date_qs:
            sales_by_date.append(
                {
                    "date": row["day"].isoformat(),
                    "amount": str(row["amount"].quantize(Decimal("0.01"))),
                    "sales_count": row["sales_count"],
                }
            )

    # ---------- 5. График: продажи по товарам ----------
    sales_by_product = []
    by_product_qs = (
        alloc_qs
        .values("product_id", "product__name")
        .annotate(
            qty=Coalesce(Sum("qty"), 0),
            amount=Coalesce(
                Sum(F("qty") * F("sale_item__unit_price")),
                Decimal("0.00"),
            ),
        )
        .order_by("-amount")
    )[:50]

    for row in by_product_qs:
        sales_by_product.append(
            {
                "product_id": row["product_id"],
                "name": row["product__name"],
                "qty": row["qty"],
                "amount": str(row["amount"].quantize(Decimal("0.01"))),
            }
        )

    # ---------- 6. Товары на руках (вне периода, “текущее состояние”) ----------
    # Все передачи агента (все времена)
    subreals_all = list(
        ManufactureSubreal.objects.filter(**subreal_filter).values(
            "id",
            "qty_accepted",
            "qty_returned",
            "product_id",
        )
    )

    # сколько по каждой передаче уже продано (через AgentSaleAllocation)
    alloc_all_filter = {
        "company": company,
        "agent": agent,
    }
    if branch:
        alloc_all_filter["subreal__branch"] = branch

    sold_by_subreal_qs = (
        AgentSaleAllocation.objects
        .filter(**alloc_all_filter)
        .values("subreal_id")
        .annotate(qty=Coalesce(Sum("qty"), 0))
    )
    sold_map = {row["subreal_id"]: row["qty"] for row in sold_by_subreal_qs}

    # цены товаров
    product_ids = {row["product_id"] for row in subreals_all if row["product_id"]}
    prices_map = {
        p["id"]: p["price"]
        for p in Product.objects.filter(id__in=product_ids).values("id", "price")
    }

    on_hand_qty = 0
    on_hand_value = Decimal("0.00")

    for row in subreals_all:
        accepted = row["qty_accepted"] or 0
        returned = row["qty_returned"] or 0
        sold = sold_map.get(row["id"], 0)

        qty_on_hand = max(accepted - returned - sold, 0)
        if qty_on_hand <= 0:
            continue

        on_hand_qty += qty_on_hand
        price = prices_map.get(row["product_id"], Decimal("0.00"))
        on_hand_value += price * Decimal(qty_on_hand)

    on_hand_value = on_hand_value.quantize(Decimal("0.01"))

    # ---------- финальный payload ----------
    return {
        "meta": {
            "period": period,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "summary": {
            "transfers_count": transfers_count,              # Передач
            "acceptances_count": acceptances_count,          # Приёмок
            "transferred_qty": int(transferred_qty or 0),    # Товаров передано (за период)
            "sales_count": sales_count,                      # Продаж
            "sales_amount": str(sales_amount),               # Сумма продаж
            "on_hand_qty": int(on_hand_qty),                 # Товаров на руках (всего)
            "on_hand_value": str(on_hand_value),             # Стоимость товаров на руках
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "sales_by_product": sales_by_product,
        },
    }

