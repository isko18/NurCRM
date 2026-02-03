from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum, Count, Value as V, F, DecimalField
from django.db.models.functions import Coalesce, TruncDate, TruncWeek, TruncMonth
from django.utils import timezone

from apps.main.cache_utils import cached_result
from apps.users.models import User, Company, Branch
from apps.warehouse import models as wm


# typed zeros
MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
ZERO_MONEY = V(Decimal("0.00"), output_field=MONEY_FIELD)

QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)
ZERO_QTY = V(Decimal("0.000"), output_field=QTY_FIELD)


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


def _trunc_by_group(field_name: str, group_by: str):
    gb = (group_by or "day").strip().lower()
    if gb == "week":
        return TruncWeek(field_name)
    if gb == "month":
        return TruncMonth(field_name)
    return TruncDate(field_name)


def _dt_range(date_from: date, date_to: date):
    tz = timezone.get_current_timezone()
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()), tz)
    dt_to_excl = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
        tz,
    )
    return dt_from, dt_to_excl


def _money_str(x) -> str:
    if x is None:
        return "0.00"
    if isinstance(x, Decimal):
        return str(x)
    try:
        return str(Decimal(str(x)))
    except Exception:
        return str(x)


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_agent")
def build_agent_warehouse_analytics_payload(
    *,
    company_id: str,
    branch_id: str | None,
    agent_id: str,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",
):
    company = Company.objects.get(id=company_id)
    branch = Branch.objects.get(id=branch_id) if branch_id else None
    agent = User.objects.get(id=agent_id)
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    req_qs = wm.AgentRequestCart.objects.filter(company=company, agent=agent)
    if branch is not None:
        req_qs = req_qs.filter(branch=branch)
    else:
        req_qs = req_qs.filter(branch__isnull=True)

    submitted_qs = req_qs.filter(submitted_at__gte=dt_from, submitted_at__lt=dt_to_excl)
    approved_qs = req_qs.filter(
        approved_at__gte=dt_from,
        approved_at__lt=dt_to_excl,
        status=wm.AgentRequestCart.Status.APPROVED,
    )
    rejected_qs = req_qs.filter(
        approved_at__gte=dt_from,
        approved_at__lt=dt_to_excl,
        status=wm.AgentRequestCart.Status.REJECTED,
    )

    approved_items_qs = wm.AgentRequestItem.objects.filter(cart__in=approved_qs)
    items_approved_qty = approved_items_qs.aggregate(
        s=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    sales_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        agent=agent,
        status=wm.Document.Status.POSTED,
        doc_type=wm.Document.DocType.SALE,
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    if branch is not None:
        sales_qs = sales_qs.filter(warehouse_from__branch=branch)
    else:
        sales_qs = sales_qs.filter(warehouse_from__branch__isnull=True)
    sales_count = sales_qs.count()
    sales_amount = sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    sales_items_qs = wm.DocumentItem.objects.filter(document__in=sales_qs)
    sales_qty = sales_items_qs.aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    returns_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        agent=agent,
        status=wm.Document.Status.POSTED,
        doc_type=wm.Document.DocType.SALE_RETURN,
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    if branch is not None:
        returns_qs = returns_qs.filter(warehouse_from__branch=branch)
    else:
        returns_qs = returns_qs.filter(warehouse_from__branch__isnull=True)
    returns_count = returns_qs.count()
    returns_amount = returns_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    write_off_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        agent=agent,
        status=wm.Document.Status.POSTED,
        doc_type=wm.Document.DocType.WRITE_OFF,
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    if branch is not None:
        write_off_qs = write_off_qs.filter(warehouse_from__branch=branch)
    else:
        write_off_qs = write_off_qs.filter(warehouse_from__branch__isnull=True)
    write_off_count = write_off_qs.count()
    write_off_qty = wm.DocumentItem.objects.filter(document__in=write_off_qs).aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    on_hand_qs = wm.AgentStockBalance.objects.select_related("product").filter(
        company=company,
        agent=agent,
    )
    if branch is not None:
        on_hand_qs = on_hand_qs.filter(branch=branch)
    else:
        on_hand_qs = on_hand_qs.filter(branch__isnull=True)

    on_hand_qty = on_hand_qs.aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")
    on_hand_amount = on_hand_qs.aggregate(
        s=Coalesce(Sum(F("qty") * F("product__price"), output_field=MONEY_FIELD), ZERO_MONEY)
    )["s"] or Decimal("0.00")

    trunc_req = _trunc_by_group("cart__approved_at", group_by)
    requests_by_date_qs = (
        approved_items_qs
        .annotate(period=trunc_req)
        .values("period")
        .annotate(
            carts_approved=Count("cart_id", distinct=True),
            items_approved=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY),
        )
        .order_by("period")
    )
    requests_by_date = [
        {
            "date": row["period"],
            "carts_approved": row["carts_approved"],
            "items_approved": row["items_approved"],
        }
        for row in requests_by_date_qs
    ]

    trunc_sales = _trunc_by_group("date", group_by)
    sales_by_date_qs = (
        sales_qs
        .annotate(period=trunc_sales)
        .values("period")
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("period")
    )
    sales_by_date = [
        {
            "date": row["period"],
            "sales_count": row["sales_count"],
            "sales_amount": _money_str(row["sales_amount"]),
        }
        for row in sales_by_date_qs
    ]

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "summary": {
            "requests_submitted": submitted_qs.count(),
            "requests_approved": approved_qs.count(),
            "requests_rejected": rejected_qs.count(),
            "items_approved": str(items_approved_qty),
            "sales_count": sales_count,
            "sales_qty": str(sales_qty),
            "sales_amount": _money_str(sales_amount),
            "returns_count": returns_count,
            "returns_amount": _money_str(returns_amount),
            "write_off_count": write_off_count,
            "write_off_qty": str(write_off_qty),
            "on_hand_qty": str(on_hand_qty),
            "on_hand_amount": _money_str(on_hand_amount),
        },
        "charts": {
            "requests_by_date": requests_by_date,
            "sales_by_date": sales_by_date,
        },
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_owner")
def build_owner_warehouse_analytics_payload(
    *,
    company_id: str,
    branch_id: str | None,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",
):
    company = Company.objects.get(id=company_id)
    branch = Branch.objects.get(id=branch_id) if branch_id else None
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    req_qs = wm.AgentRequestCart.objects.filter(company=company)
    if branch is not None:
        req_qs = req_qs.filter(branch=branch)
    else:
        req_qs = req_qs.filter(branch__isnull=True)

    approved_qs = req_qs.filter(
        approved_at__gte=dt_from,
        approved_at__lt=dt_to_excl,
        status=wm.AgentRequestCart.Status.APPROVED,
    )
    approved_items_qs = wm.AgentRequestItem.objects.filter(cart__in=approved_qs)

    items_approved_qty = approved_items_qs.aggregate(
        s=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    sales_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        agent__isnull=False,
        status=wm.Document.Status.POSTED,
        doc_type=wm.Document.DocType.SALE,
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    if branch is not None:
        sales_qs = sales_qs.filter(warehouse_from__branch=branch)
    else:
        sales_qs = sales_qs.filter(warehouse_from__branch__isnull=True)
    sales_count = sales_qs.count()
    sales_amount = sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    on_hand_qs = wm.AgentStockBalance.objects.select_related("product", "agent").filter(company=company)
    if branch is not None:
        on_hand_qs = on_hand_qs.filter(branch=branch)
    else:
        on_hand_qs = on_hand_qs.filter(branch__isnull=True)

    on_hand_qty = on_hand_qs.aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")
    on_hand_amount = on_hand_qs.aggregate(
        s=Coalesce(Sum(F("qty") * F("product__price"), output_field=MONEY_FIELD), ZERO_MONEY)
    )["s"] or Decimal("0.00")

    trunc_sales = _trunc_by_group("date", group_by)
    sales_by_date_qs = (
        sales_qs
        .annotate(period=trunc_sales)
        .values("period")
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("period")
    )
    sales_by_date = [
        {
            "date": row["period"],
            "sales_count": row["sales_count"],
            "sales_amount": _money_str(row["sales_amount"]),
        }
        for row in sales_by_date_qs
    ]

    top_agents_by_sales_qs = (
        sales_qs
        .values("agent_id", "agent__first_name", "agent__last_name")
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("-sales_amount")[:10]
    )
    top_agents_by_sales = [
        {
            "agent_id": str(r["agent_id"]),
            "agent_name": (
                f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
                or "Агент"
            ),
            "sales_count": r["sales_count"],
            "sales_amount": _money_str(r["sales_amount"]),
        }
        for r in top_agents_by_sales_qs
    ]

    top_agents_by_received_qs = (
        approved_items_qs
        .values("cart__agent_id", "cart__agent__first_name", "cart__agent__last_name")
        .annotate(
            items_approved=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY),
        )
        .order_by("-items_approved")[:10]
    )
    top_agents_by_received = [
        {
            "agent_id": str(r["cart__agent_id"]),
            "agent_name": (
                f"{(r['cart__agent__first_name'] or '').strip()} {(r['cart__agent__last_name'] or '').strip()}".strip()
                or "Агент"
            ),
            "items_approved": str(r["items_approved"]),
        }
        for r in top_agents_by_received_qs
    ]

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "summary": {
            "requests_approved": approved_qs.count(),
            "items_approved": str(items_approved_qty),
            "sales_count": sales_count,
            "sales_amount": _money_str(sales_amount),
            "on_hand_qty": str(on_hand_qty),
            "on_hand_amount": _money_str(on_hand_amount),
        },
        "charts": {
            "sales_by_date": sales_by_date,
        },
        "top_agents": {
            "by_sales": top_agents_by_sales,
            "by_received": top_agents_by_received,
        },
    }
