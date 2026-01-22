from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import (
    Avg,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, ExtractWeekDay, TruncDate
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.barber.models import Appointment as BarberAppointment, Client as BarberClient, OnlineBooking, Service as BarberService
from apps.cafe.models import Purchase as CafePurchase
from apps.construction.models import CashFlow, Cashbox
from apps.main.analytics_agent import _parse_period
from apps.main.models import Sale, SaleItem
from apps.main.views import CompanyBranchRestrictedMixin


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)


def _money(x) -> Decimal:
    try:
        return (Decimal(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _money_str(x) -> str:
    return str(_money(x))


def _dt_range(date_from: date, date_to: date):
    """
    (inclusive) date_from 00:00  -> (exclusive) (date_to+1) 00:00
    """
    tz = timezone.get_current_timezone()
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()), tz)
    dt_to_excl = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), datetime.min.time()), tz)
    return dt_from, dt_to_excl


def _apply_branch(qs, branch, include_global: bool = True, field_name: str = "branch"):
    if branch is None:
        return qs
    if include_global:
        return qs.filter(Q(**{field_name: branch}) | Q(**{f"{field_name}__isnull": True}))
    return qs.filter(**{field_name: branch})


_WEEKDAY_RU = {
    1: "Вс",
    2: "Пн",
    3: "Вт",
    4: "Ср",
    5: "Чт",
    6: "Пт",
    7: "Сб",
}


def build_dashboard_payload(*, company, branch, period_params: dict, user=None, scope: str = "company") -> dict:
    """
    scope:
      - company: общая аналитика (для владельца/админа)
      - my: аналитика текущего мастера (barber)
    """
    date_from: date = period_params["date_from"]
    date_to: date = period_params["date_to"]
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    # ─────────────────────────────────────────────────────────
    # Barber: appointments / services / clients
    # ─────────────────────────────────────────────────────────
    appt_qs = BarberAppointment.objects.filter(company=company, start_at__gte=dt_from, start_at__lt=dt_to_excl)
    appt_qs = _apply_branch(appt_qs, branch, include_global=True, field_name="branch")
    if scope == "my" and user is not None:
        appt_qs = appt_qs.filter(barber=user)

    effective_price = ExpressionWrapper(
        F("price") * (Value(Decimal("1.00")) - (F("discount") / Value(Decimal("100.00")))),
        output_field=DecimalField(max_digits=14, decimal_places=6),
    )

    barber_totals = appt_qs.aggregate(
        appointments_total=Count("id"),
        appointments_completed=Count("id", filter=Q(status=BarberAppointment.Status.COMPLETED)),
        appointments_canceled=Count("id", filter=Q(status=BarberAppointment.Status.CANCELED)),
        appointments_no_show=Count("id", filter=Q(status=BarberAppointment.Status.NO_SHOW)),
        revenue=Coalesce(Sum(effective_price, filter=Q(status=BarberAppointment.Status.COMPLETED)), ZERO_MONEY),
        avg_ticket=Avg(effective_price, filter=Q(status=BarberAppointment.Status.COMPLETED)),
    )

    appt_total = int(barber_totals["appointments_total"] or 0)
    appt_completed = int(barber_totals["appointments_completed"] or 0)
    conversion = float((Decimal(appt_completed) / Decimal(appt_total) * 100).quantize(Decimal("0.1"))) if appt_total else 0.0

    # busy weekday
    by_wd = (
        appt_qs.annotate(wd=ExtractWeekDay("start_at"))
        .values("wd")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")
    )
    busy_day = None
    if by_wd:
        top = by_wd[0]
        busy_day = {"day": _WEEKDAY_RU.get(top["wd"], str(top["wd"])), "count": int(top["cnt"] or 0)}

    # statuses block (month, overall)
    completed_revenue = appt_qs.filter(status=BarberAppointment.Status.COMPLETED).aggregate(
        s=Coalesce(Sum(effective_price), ZERO_MONEY)
    )["s"]
    canceled_and_no_show = appt_qs.filter(status__in=[BarberAppointment.Status.CANCELED, BarberAppointment.Status.NO_SHOW]).count()

    # top masters
    top_masters = list(
        appt_qs.filter(status=BarberAppointment.Status.COMPLETED)
        .values("barber_id", "barber__first_name", "barber__last_name", "barber__email")
        .annotate(count=Count("id"), revenue=Coalesce(Sum(effective_price), ZERO_MONEY))
        .order_by("-revenue", "-count")[:10]
    )
    for r in top_masters:
        first = (r.pop("barber__first_name") or "").strip()
        last = (r.pop("barber__last_name") or "").strip()
        email = (r.pop("barber__email") or "").strip()
        r["master_id"] = str(r.pop("barber_id"))
        r["master_name"] = (f"{first} {last}".strip() or email or "—")
        r["revenue"] = _money_str(r["revenue"])

    # top barber clients
    top_clients = list(
        appt_qs.filter(status=BarberAppointment.Status.COMPLETED)
        .values("client_id", "client__full_name")
        .annotate(visits=Count("id"), revenue=Coalesce(Sum(effective_price), ZERO_MONEY))
        .order_by("-revenue", "-visits")[:10]
    )
    for r in top_clients:
        r["client_id"] = str(r["client_id"]) if r["client_id"] else None
        r["client_name"] = r.pop("client__full_name") or "Без имени"
        r["revenue"] = _money_str(r["revenue"])

    # services total + top services (month)
    services_qs = BarberService.objects.filter(company=company)
    services_qs = _apply_branch(services_qs, branch, include_global=True, field_name="branch")
    services_total = services_qs.count()

    top_services = list(
        BarberService.objects.filter(appointments__in=appt_qs.filter(status=BarberAppointment.Status.COMPLETED))
        .values("id", "name")
        .annotate(
            count=Count("appointments", distinct=True),
            revenue=Coalesce(Sum("price"), ZERO_MONEY),
        )
        .order_by("-count", "name")[:10]
    )
    for r in top_services:
        r["service_id"] = str(r.pop("id"))
        r["revenue"] = _money_str(r["revenue"])

    # appointments by weekday
    appt_by_weekday = [
        {"day": _WEEKDAY_RU.get(i, str(i)), "count": 0} for i in range(2, 8)
    ] + [{"day": _WEEKDAY_RU.get(1, "Вс"), "count": 0}]
    appt_wd_map = {row["day"]: row for row in appt_by_weekday}
    for row in (
        appt_qs.annotate(wd=ExtractWeekDay("start_at")).values("wd").annotate(cnt=Count("id"))
    ):
        day = _WEEKDAY_RU.get(row["wd"], str(row["wd"]))
        if day in appt_wd_map:
            appt_wd_map[day]["count"] = int(row["cnt"] or 0)

    barber_clients_count = BarberClient.objects.filter(company=company).count()

    # ─────────────────────────────────────────────────────────
    # Online booking: statuses + top services by bookings
    # ─────────────────────────────────────────────────────────
    ob_qs = OnlineBooking.objects.filter(company=company, created_at__gte=dt_from, created_at__lt=dt_to_excl)
    ob_qs = _apply_branch(ob_qs, branch, include_global=True, field_name="branch")

    booking_status_counts = {
        k: int(v or 0)
        for k, v in ob_qs.values_list("status").annotate(v=Count("id"))
    }

    service_title_counter = Counter()
    for services in ob_qs.values_list("services", flat=True):
        if not services:
            continue
        if isinstance(services, list):
            for s in services:
                title = (s or {}).get("title") if isinstance(s, dict) else None
                if title:
                    service_title_counter[str(title)] += 1

    top_services_by_bookings = [
        {"title": title, "count": cnt}
        for title, cnt in service_title_counter.most_common(5)
    ]

    # ─────────────────────────────────────────────────────────
    # Sales (products): revenue/tx/clients + top products + top clients
    # ─────────────────────────────────────────────────────────
    sale_qs = Sale.objects.filter(company=company, status=Sale.Status.PAID, paid_at__gte=dt_from, paid_at__lt=dt_to_excl)
    sale_qs = _apply_branch(sale_qs, branch, include_global=True, field_name="branch")

    sales_agg = sale_qs.aggregate(
        revenue=Coalesce(Sum("total"), ZERO_MONEY),
        tx=Count("id"),
        clients=Count("client_id", distinct=True),
    )
    sales_revenue = sales_agg["revenue"] or Decimal("0.00")
    sales_tx = int(sales_agg["tx"] or 0)
    sales_clients = int(sales_agg["clients"] or 0)

    items_qs = SaleItem.objects.filter(sale__in=sale_qs)
    top_products_sales = list(
        items_qs.values("name_snapshot")
        .annotate(
            qty=Coalesce(Sum("quantity"), Value(Decimal("0.000"), output_field=DecimalField(max_digits=14, decimal_places=3))),
            revenue=Coalesce(Sum(F("quantity") * F("unit_price"), output_field=MONEY_FIELD), ZERO_MONEY),
        )
        .order_by("-revenue")[:10]
    )
    for r in top_products_sales:
        r["product_name"] = r.pop("name_snapshot") or "—"
        r["qty"] = float(r["qty"] or Decimal("0.000"))
        r["revenue"] = _money_str(r["revenue"])

    top_sales_clients = list(
        sale_qs.values("client_id", "client__full_name")
        .annotate(orders=Count("id"), revenue=Coalesce(Sum("total"), ZERO_MONEY))
        .order_by("-revenue")[:10]
    )
    for r in top_sales_clients:
        r["client_id"] = str(r["client_id"]) if r["client_id"] else None
        r["client_name"] = r.pop("client__full_name") or "Без имени"
        r["revenue"] = _money_str(r["revenue"])

    # ─────────────────────────────────────────────────────────
    # Cashboxes: operations + income/expense (month)
    # ─────────────────────────────────────────────────────────
    flow_qs = CashFlow.objects.filter(
        company=company,
        status=CashFlow.Status.APPROVED,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    )
    flow_qs = _apply_branch(flow_qs, branch, include_global=True, field_name="branch")

    flow_agg = flow_qs.aggregate(
        income=Coalesce(Sum("amount", filter=Q(type=CashFlow.Type.INCOME)), ZERO_MONEY),
        expense=Coalesce(Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)), ZERO_MONEY),
    )
    flows_income = flow_agg["income"] or Decimal("0.00")
    flows_expense = flow_agg["expense"] or Decimal("0.00")

    cashboxes_qs = Cashbox.objects.filter(company=company)
    cashboxes_qs = _apply_branch(cashboxes_qs, branch, include_global=True, field_name="branch")

    flow_by_cb = {
        str(r["cashbox_id"]): r
        for r in flow_qs.values("cashbox_id").annotate(
            ops=Count("id"),
            income=Coalesce(Sum("amount", filter=Q(type=CashFlow.Type.INCOME)), ZERO_MONEY),
            expense=Coalesce(Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)), ZERO_MONEY),
        )
    }
    sale_by_cb = {
        str(r["cashbox_id"]): r
        for r in sale_qs.values("cashbox_id").annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
    }
    cashboxes_rows = []
    for cb in cashboxes_qs.only("id", "name"):
        cb_id = str(cb.id)
        frow = flow_by_cb.get(cb_id) or {}
        srow = sale_by_cb.get(cb_id) or {}
        income_cb = _money((srow.get("sales_amount") or 0) + (frow.get("income") or 0))
        expense_cb = _money(frow.get("expense") or 0)
        ops = int((frow.get("ops") or 0) + (srow.get("sales_count") or 0))
        cashboxes_rows.append(
            {
                "cashbox_id": cb_id,
                "cashbox_name": cb.name or cb_id,
                "operations": ops,
                "income": str(income_cb),
                "expense": str(expense_cb),
            }
        )

    # ─────────────────────────────────────────────────────────
    # Purchases by supplier (month)
    # ─────────────────────────────────────────────────────────
    purchases_qs = CafePurchase.objects.filter(company=company, created_at__gte=dt_from, created_at__lt=dt_to_excl)
    purchases_qs = _apply_branch(purchases_qs, branch, include_global=True, field_name="branch")
    suppliers_rows = list(
        purchases_qs.values("supplier").annotate(
            positions=Count("id"),
            amount=Coalesce(Sum("price"), ZERO_MONEY),
        ).order_by("-amount")[:10]
    )
    for r in suppliers_rows:
        r["supplier"] = r["supplier"] or "—"
        r["amount"] = _money_str(r["amount"])

    purchases_total = purchases_qs.aggregate(s=Coalesce(Sum("price"), ZERO_MONEY))["s"] or Decimal("0.00")

    # ─────────────────────────────────────────────────────────
    # Finance totals (month)
    # ─────────────────────────────────────────────────────────
    income_month = _money((barber_totals["revenue"] or 0) + (sales_revenue or 0) + (flows_income or 0))
    expense_month = _money(flows_expense or 0)
    profit_month = _money(income_month - expense_month)

    avg_ticket = barber_totals["avg_ticket"]
    avg_ticket_str = _money_str(avg_ticket) if avg_ticket is not None else None

    # ─────────────────────────────────────────────────────────
    # Income/expense dynamics (daily)
    # ─────────────────────────────────────────────────────────
    def _date_iter(a: date, b: date):
        cur = a
        while cur <= b:
            yield cur
            cur += timedelta(days=1)

    barber_daily = {
        r["d"]: _money(r["v"])
        for r in appt_qs.filter(status=BarberAppointment.Status.COMPLETED)
        .annotate(d=TruncDate("start_at"))
        .values("d")
        .annotate(v=Coalesce(Sum(effective_price), ZERO_MONEY))
    }
    sales_daily = {
        r["d"]: _money(r["v"])
        for r in sale_qs.annotate(d=TruncDate("paid_at")).values("d").annotate(v=Coalesce(Sum("total"), ZERO_MONEY))
    }
    flows_income_daily = {
        r["d"]: _money(r["v"])
        for r in flow_qs.filter(type=CashFlow.Type.INCOME).annotate(d=TruncDate("created_at")).values("d").annotate(
            v=Coalesce(Sum("amount"), ZERO_MONEY)
        )
    }
    flows_expense_daily = {
        r["d"]: _money(r["v"])
        for r in flow_qs.filter(type=CashFlow.Type.EXPENSE).annotate(d=TruncDate("created_at")).values("d").annotate(
            v=Coalesce(Sum("amount"), ZERO_MONEY)
        )
    }

    dynamics = []
    for d in _date_iter(date_from, date_to):
        income_d = _money(barber_daily.get(d) + sales_daily.get(d) + flows_income_daily.get(d))
        expense_d = _money(flows_expense_daily.get(d))
        dynamics.append(
            {
                "date": d.isoformat(),
                "income": str(income_d),
                "expense": str(expense_d),
                "profit": str(_money(income_d - expense_d)),
            }
        )

    payload = {
        "period": {
            "type": period_params["period"],
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
        "finance": {
            "income_month": str(income_month),
            "expense_month": str(expense_month),
            "profit_month": str(profit_month),
        },
        "barber": {
            "avg_ticket": avg_ticket_str,
            "conversion_percent": conversion,
            "appointments_total": appt_total,
            "appointments_completed": appt_completed,
            "services_total": services_total,
            "clients_barber": barber_clients_count,
            "busy_day": busy_day,
            "statuses": {
                "completed": {
                    "count": appt_completed,
                    "percent": float((Decimal(appt_completed) / Decimal(appt_total) * 100).quantize(Decimal("0.1"))) if appt_total else 0.0,
                    "amount": _money_str(completed_revenue),
                },
                "canceled_or_no_show": {
                    "count": int(canceled_and_no_show),
                    "percent": float((Decimal(canceled_and_no_show) / Decimal(appt_total) * 100).quantize(Decimal("0.1"))) if appt_total else 0.0,
                },
            },
            "top_masters": top_masters,
            "top_clients": top_clients,
            "top_services": top_services,
            "appointments_by_weekday": appt_by_weekday,
        },
        "online_bookings": {
            "statuses": booking_status_counts,
            "top_services": top_services_by_bookings,
        },
        "sales": {
            "clients_sales": sales_clients,
            "transactions": sales_tx,
            "revenue": _money_str(sales_revenue),
            "top_products": top_products_sales,
            "top_clients": top_sales_clients,
        },
        "cashboxes": {
            "income": _money_str(sales_revenue + flows_income),
            "expense": _money_str(flows_expense),
            "rows": cashboxes_rows,
        },
        "suppliers": {
            "purchases_total": _money_str(purchases_total),
            "rows": suppliers_rows,
        },
        "dynamics": dynamics,
    }
    return payload


class OwnerDashboardAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/analytics/dashboard/
    Полная "общая" аналитика (доступ: владелец/админ).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        role = str(getattr(user, "role", "") or "").strip().lower()
        if not (getattr(user, "is_superuser", False) or role in {"owner", "admin"}):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        company = self._company() or getattr(user, "owned_company", None) or getattr(user, "company", None)
        branch = self._auto_branch()
        period_params = _parse_period(request)

        data = build_dashboard_payload(company=company, branch=branch, period_params=period_params, scope="company")
        return Response(data, status=status.HTTP_200_OK)


class MyDashboardAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/analytics/dashboard/my/
    Аналитика мастера (barber) — строится только по его записям.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        company = self._company() or getattr(request.user, "owned_company", None) or getattr(request.user, "company", None)
        branch = self._auto_branch()
        period_params = _parse_period(request)

        data = build_dashboard_payload(company=company, branch=branch, period_params=period_params, user=request.user, scope="my")
        return Response(data, status=status.HTTP_200_OK)

