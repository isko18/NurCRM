from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal
from itertools import groupby
from operator import attrgetter

from django.conf import settings
from django.db.models import (
    Prefetch,
    Sum,
    Count,
    Value as V,
    F,
    DecimalField,
    Subquery,
    OuterRef,
    Q,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .models import (
    ManufactureSubreal,
    Acceptance,
    ReturnFromAgent,
    AgentSaleAllocation,
    Sale,
    SaleItem,
    Client,
    ClientDeal,
    DealInstallment,
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


# =========================
# Typed zeros (важно для FieldError: mixed types)
# =========================
MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)
ZERO_MONEY = V(Decimal("0.00"), output_field=MONEY_FIELD)

# quantity в SaleItem у тебя, судя по ошибке, DecimalField → нужен Decimal-ноль
QTY_FIELD = DecimalField(max_digits=14, decimal_places=3)
ZERO_QTY = V(Decimal("0.000"), output_field=QTY_FIELD)


def _parse_period(request):
    """
    ?period=day|week|month|custom

    Понимает оба варианта:
      - day:  ?date=YYYY-MM-DD ИЛИ ?date_from=YYYY-MM-DD
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
        d = raw_date or raw_from or raw_to or today
        return {"period": "day", "date_from": d, "date_to": d, "group_by": "day"}

    # ---------- WEEK ----------
    if period == "week":
        date_to = raw_to or raw_date or today
        date_from = raw_from or (date_to - timedelta(days=6))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "week", "date_from": date_from, "date_to": date_to, "group_by": "day"}

    # ---------- CUSTOM ----------
    if period == "custom":
        date_to = raw_to or today
        date_from = raw_from or (date_to - timedelta(days=29))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "custom", "date_from": date_from, "date_to": date_to, "group_by": "day"}

    # ---------- MONTH (по умолчанию) ----------
    date_to = raw_to or today
    date_from = raw_from or (date_to - timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return {"period": "month", "date_from": date_from, "date_to": date_to, "group_by": "day"}


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
            # "acceptances",  # не используется в расчёте — убрал, чтобы не грузить БД
            Prefetch("returns", queryset=accepted_returns_qs, to_attr="accepted_returns"),
            Prefetch("sale_allocations", queryset=alloc_qs, to_attr="prefetched_allocs"),
        )
        # ВАЖНО: sold_qty только по allocations этой компании (чтобы не схватить чужое)
        .annotate(
            sold_qty=Coalesce(
                Sum("sale_allocations__qty", filter=Q(sale_allocations__company=company)),
                V(0),
            )
        )
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

    for _product_id, subreals_iter in groupby(base, key=attrgetter("product_id")):
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
            # fallback: если sold_qty пустой, используем prefetched_allocs
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
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
    dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

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
    #              Б Р А К  (возвраты от агента, принятые)
    # ======================================================
    returns_qs = ReturnFromAgent.objects.filter(
        company=company,
        returned_by=agent,
        status=ReturnFromAgent.Status.ACCEPTED,
        returned_at__range=(dt_from, dt_to),
    )
    if branch is not None:
        returns_qs = returns_qs.filter(branch=branch)
    else:
        returns_qs = returns_qs.filter(branch__isnull=True)
    defective_items_qty = returns_qs.aggregate(s=Coalesce(Sum("qty"), V(0)))["s"] or 0

    # ======================================================
    #              П Р О Д А Ж И
    # ======================================================
    sales_qs = Sale.objects.filter(
        company=company,
        user=agent,
        created_at__range=(dt_from, dt_to),
        status=Sale.Status.PAID,  # только оплаченные
    )
    if branch is not None:
        sales_qs = sales_qs.filter(branch=branch)
    else:
        sales_qs = sales_qs.filter(branch__isnull=True)

    sales_count = sales_qs.count()

    # FIX: Decimal-ноль только с output_field
    sales_amount_dec = sales_qs.aggregate(
        s=Coalesce(Sum("total"), ZERO_MONEY)
    )["s"] or Decimal("0.00")
    sales_amount = float(sales_amount_dec)
    discounts_total_dec = sales_qs.aggregate(
        s=Coalesce(Sum("discount_total"), ZERO_MONEY)
    )["s"] or Decimal("0.00")
    discounts_total = float(discounts_total_dec)

    # ---------------- 0) методы оплаты ----------------
    payment_breakdown_qs = (
        sales_qs.values("payment_method")
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("-sales_amount")
    )
    payment_labels = {k: v for k, v in Sale.PaymentMethod.choices}
    payment_breakdown = [
        {
            "payment_method": row["payment_method"],
            "payment_method_label": payment_labels.get(row["payment_method"], row["payment_method"]),
            "sales_count": row["sales_count"],
            "sales_amount": float(row["sales_amount"] or Decimal("0.00")),
        }
        for row in payment_breakdown_qs
    ]

    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    # ---------------- 1) продажи по товарам ----------------
    sales_by_product_qs = (
        items_qs
        .values("product_id", "product__name")
        .annotate(
            # FIX: quantity может быть DecimalField → Sum(quantity)=Decimal
            qty=Coalesce(Sum("quantity", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(
                Sum(F("quantity") * F("unit_price"), output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
        )
        .order_by("-amount")
    )

    sales_by_product_amount = [
        {
            "product_id": str(row["product_id"]),
            "product_name": row["product__name"],
            "amount": float(row["amount"] or Decimal("0.00")),
        }
        for row in sales_by_product_qs
    ]

    # ---------------- 2) продажи по датам ----------------
    sales_by_date_qs = (
        items_qs
        .annotate(day=TruncDate("sale__created_at"))
        .values("day")
        .annotate(
            sales_count=Count("sale_id", distinct=True),
            # FIX: quantity может быть DecimalField → Sum(quantity)=Decimal
            items_sold=Coalesce(Sum("quantity", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(
                Sum(F("quantity") * F("unit_price"), output_field=MONEY_FIELD),
                ZERO_MONEY,
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
            amount = float(row["amount"] or 0.0)
            sales_distribution_by_product.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "amount": amount,
                "percent": round(amount * 100.0 / sales_amount, 2),
            })

    # ======================================================
    #        Т О В А Р Ы  Н А  Р У К А Х
    # ======================================================
    on_hand = _compute_agent_on_hand(company=company, branch=branch, agent=agent)

    # ======================================================
    #      Д О Л Г  К Л И Е Н Т О В  А Г Е Н Т А  (текущий)
    # ======================================================
    # Клиенты агента: Client.salesperson = agent
    clients_qs = Client.objects.filter(company=company, salesperson=agent)
    if branch is not None:
        clients_qs = clients_qs.filter(branch=branch)
    else:
        clients_qs = clients_qs.filter(branch__isnull=True)

    # Долги по рассрочкам/сделкам (ClientDeal.Kind.DEBT): остаток = (amount-prepayment) - sum(paid_installments)
    deals_qs = ClientDeal.objects.filter(company=company, kind=ClientDeal.Kind.DEBT, client__in=clients_qs)
    if branch is not None:
        deals_qs = deals_qs.filter(branch=branch)
    else:
        deals_qs = deals_qs.filter(branch__isnull=True)

    paid_subq = (
        DealInstallment.objects.filter(deal_id=OuterRef("pk"))
        .values("deal_id")
        .annotate(s=Sum("paid_amount"))
        .values("s")[:1]
    )
    deals_remaining_dec = (
        deals_qs
        .annotate(paid=Coalesce(Subquery(paid_subq), V(Decimal("0.00"), output_field=MONEY_FIELD)))
        .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
        .aggregate(t=Coalesce(Sum("remaining"), ZERO_MONEY))["t"]
        or Decimal("0.00")
    )

    # Долги по продажам POS агента со статусом DEBT (не оплачены)
    sales_debt_qs = Sale.objects.filter(company=company, user=agent, status=Sale.Status.DEBT)
    if branch is not None:
        sales_debt_qs = sales_debt_qs.filter(branch=branch)
    else:
        sales_debt_qs = sales_debt_qs.filter(branch__isnull=True)
    pos_sales_debt_dec = sales_debt_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    accounts_receivable_dec = (deals_remaining_dec or Decimal("0.00")) + (pos_sales_debt_dec or Decimal("0.00"))

    # ------------------------------------------------------
    # ДОЛГ ПО КЛИЕНТАМ (как в карточке клиента: сделки + POS-долги)
    # ------------------------------------------------------
    # 1) по сделкам: суммарный remaining по каждому client_id
    deals_by_client_qs = (
        deals_qs
        .annotate(paid=Coalesce(Subquery(paid_subq), V(Decimal("0.00"), output_field=MONEY_FIELD)))
        .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
        .values("client_id")
        .annotate(remaining_total=Coalesce(Sum("remaining"), ZERO_MONEY))
    )
    deals_by_client = {row["client_id"]: (row["remaining_total"] or Decimal("0.00")) for row in deals_by_client_qs}

    # 2) по продажам "в долг": sum(total) по каждому client_id
    sales_debt_by_client_qs = (
        sales_debt_qs
        .exclude(client_id__isnull=True)
        .values("client_id")
        .annotate(debt_total=Coalesce(Sum("total"), ZERO_MONEY))
    )
    sales_debt_by_client = {row["client_id"]: (row["debt_total"] or Decimal("0.00")) for row in sales_debt_by_client_qs}

    # 3) собираем список по всем клиентам агента (включая 0, чтобы фронт мог показать)
    clients_list = list(clients_qs.values("id", "full_name", "phone"))
    clients_debt = []
    clients_debt_total_dec = Decimal("0.00")
    for c in clients_list:
        cid = c["id"]
        deals_debt = deals_by_client.get(cid, Decimal("0.00")) or Decimal("0.00")
        pos_debt = sales_debt_by_client.get(cid, Decimal("0.00")) or Decimal("0.00")
        total_debt = (deals_debt + pos_debt).quantize(Decimal("0.01"))
        clients_debt_total_dec += total_debt
        clients_debt.append({
            "client_id": str(cid),
            "client_name": c.get("full_name") or "",
            "client_phone": c.get("phone") or "",
            "debt_total": float(total_debt),
            "debt_client_deals": float(deals_debt),
            "debt_pos_sales": float(pos_debt),
        })
    clients_debt.sort(key=lambda x: x["debt_total"], reverse=True)

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
    history_qs = sub_qs.select_related("product").order_by("-created_at")[:200]
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
            "items_transferred": items_transferred,
            "defective_items": defective_items_qty,
            "sales_count": sales_count,
            "sales_amount": sales_amount,
            "discounts_total": discounts_total,
            "items_on_hand_qty": on_hand["total_qty"],
            "items_on_hand_amount": on_hand["total_amount"],
            "accounts_receivable": float(accounts_receivable_dec),
            "accounts_receivable_client_deals": float(deals_remaining_dec or Decimal("0.00")),
            "accounts_receivable_pos_sales": float(pos_sales_debt_dec or Decimal("0.00")),
            "clients_debt_total": float(clients_debt_total_dec),
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "sales_by_product_amount": sales_by_product_amount,
            "sales_distribution_by_product": sales_distribution_by_product,
            "sales_by_payment_method": payment_breakdown,
            "on_hand_by_product_qty": on_hand["by_product_qty"],
            "on_hand_by_product_amount": on_hand["by_product_amount"],
            "transfers_by_date": transfers_by_date,
            "top_products_by_transfers": top_products_by_transfers,
            "clients_debt": clients_debt,
        },
        "transfers_history": transfers_history,
    }
