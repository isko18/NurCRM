"""
Единый агрегирующий эндпоинт аналитики для сферы услуг (barber / services / dentistry).

GET /api/barbershop/analytics/dashboard/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD

Заменяет десятки клиентских запросов (appointments, bookings, employees, services,
clients, cashflows, POS-продажи, sale-payouts) одним ответом за выбранный период.

Скоуп:
  - модели barber — строго как в CompanyQuerysetMixin (company + активный филиал);
  - модели main/construction — company + (branch == активный ИЛИ branch IS NULL),
    потому что записи без филиала являются общекомпанейскими (например, касса компании).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.construction.models import Cashbox, CashFlow
from apps.main.models import (
    Client as MarketClient,
    ObjectSale,
    ObjectSaleItem,
    Product,
    Sale,
    SaleItem,
    SupplierReceipt,
    SupplierReceiptItem,
)

from apps.barber.models import (
    Appointment,
    AppointmentService,
    Client as BarberClient,
    OnlineBooking,
    PayoutSale,
    Service,
)
from apps.barber.views import CompanyQuerysetMixin, _can_view_barber_analytics


# ─────────────────────────────────────────────────────────────
# Константы / утилиты
# ─────────────────────────────────────────────────────────────

Z_MONEY = Decimal("0.00")
Z_QTY = Decimal("0.000")

MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)

# Автоматическая запись кассы «Выплаты мастерам YYYY-MM» учитывается отдельно
# через totals.sale_fund — из cashflows её исключаем, иначе расход задвоится.
MASTER_PAYOUT_FLOW_PREFIX = "Выплаты мастерам"

# Статусы записей, которые считаются «состоявшимися» для рейтингов (count).
RANKING_STATUSES = (
    Appointment.Status.BOOKED,
    Appointment.Status.CONFIRMED,
    Appointment.Status.COMPLETED,
    Appointment.Status.NO_SHOW,
)

TOP_RANKING_ROWS = 10
TOP_BOOKING_SERVICES = 5
TOP_PRODUCT_ROWS = 50
MAX_DETAIL_ROWS = 500

APPT_EFFECTIVE_PRICE = ExpressionWrapper(
    F("price") * (Value(Decimal("1")) - (F("discount") / Value(Decimal("100")))),
    output_field=DecimalField(max_digits=14, decimal_places=6),
)


def _money(x) -> Decimal:
    try:
        return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Z_MONEY


def _fmoney(x) -> float:
    return float(_money(x))


def _fqty(x) -> float:
    try:
        return float(Decimal(str(x or 0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _sum_money(expr, **kwargs):
    return Coalesce(Sum(expr, **kwargs), Value(Z_MONEY, output_field=MONEY_FIELD), output_field=MONEY_FIELD)


def _day_label(d) -> str:
    return d.strftime("%d.%m.%Y")


def _person_name(first: str, last: str, email: str = "") -> str:
    full = f"{(first or '').strip()} {(last or '').strip()}".strip()
    return full or (email or "").strip() or "—"


def _branch_scope(qs, branch, *, include_global: bool):
    """
    include_global=True  -> branch == активный ИЛИ branch IS NULL (main/construction)
    include_global=False -> строго branch == активный (barber, как в CompanyQuerysetMixin)
    """
    if branch is None:
        return qs
    if include_global:
        return qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    return qs.filter(branch=branch)


# ─────────────────────────────────────────────────────────────
# Период
# ─────────────────────────────────────────────────────────────

class Period:
    def __init__(self, date_from, date_to):
        tz = timezone.get_current_timezone()
        self.date_from = date_from
        self.date_to = date_to
        self.start = timezone.make_aware(datetime.combine(date_from, dtime.min), tz)
        # правая граница исключающая: [start, end)
        self.end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), dtime.min), tz)
        self.days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]

    @property
    def label(self) -> str:
        return self.date_from.strftime("%Y-%m")

    def as_dict(self) -> dict:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "label": self.label,
        }


def _parse_period(request) -> Period:
    today = timezone.localdate()

    raw_from = request.query_params.get("date_from")
    raw_to = request.query_params.get("date_to")

    date_from = parse_date(raw_from) if raw_from else today.replace(day=1)
    date_to = parse_date(raw_to) if raw_to else today

    if not date_from:
        raise ValidationError({"date_from": "Неверный формат даты. Используйте YYYY-MM-DD."})
    if not date_to:
        raise ValidationError({"date_to": "Неверный формат даты. Используйте YYYY-MM-DD."})
    if date_to < date_from:
        raise ValidationError({"date_to": "date_to должен быть >= date_from."})
    if (date_to - date_from).days > 366:
        raise ValidationError({"date_to": "Период не может превышать 366 дней."})

    return Period(date_from, date_to)


# ─────────────────────────────────────────────────────────────
# Записи (appointments)
# ─────────────────────────────────────────────────────────────

def _appointments_qs(company, branch, period: Period):
    qs = Appointment.objects.filter(
        company=company,
        start_at__gte=period.start,
        start_at__lt=period.end,
    )
    return _branch_scope(qs, branch, include_global=False)


def _appointments_block(appt_qs):
    totals = appt_qs.aggregate(
        appointments_total=Count("id"),
        appointments_completed=Count("id", filter=Q(status=Appointment.Status.COMPLETED)),
        appointments_canceled=Count("id", filter=Q(status=Appointment.Status.CANCELED)),
        appointments_no_show=Count("id", filter=Q(status=Appointment.Status.NO_SHOW)),
        revenue_completed=_sum_money(APPT_EFFECTIVE_PRICE, filter=Q(status=Appointment.Status.COMPLETED)),
    )
    return totals


def _appointments_by_day(appt_qs):
    """
    (счётчик записей по дням — любой статус, выручка завершённых по дням)
    """
    counts = {}
    for row in appt_qs.annotate(d=TruncDate("start_at")).values("d").annotate(c=Count("id")):
        counts[row["d"]] = row["c"]

    revenue = {}
    rows = (
        appt_qs.filter(status=Appointment.Status.COMPLETED)
        .annotate(d=TruncDate("start_at"))
        .values("d")
        .annotate(s=_sum_money(APPT_EFFECTIVE_PRICE))
    )
    for row in rows:
        revenue[row["d"]] = _money(row["s"])

    return counts, revenue


def _masters_ranking(appt_qs):
    rows = (
        appt_qs.filter(status__in=RANKING_STATUSES)
        .values("barber_id", "barber__first_name", "barber__last_name", "barber__email")
        .annotate(
            count=Count("id"),
            revenue=_sum_money(APPT_EFFECTIVE_PRICE, filter=Q(status=Appointment.Status.COMPLETED)),
        )
        .order_by("-revenue", "-count")[:TOP_RANKING_ROWS]
    )
    return [
        {
            "master_id": str(r["barber_id"]) if r["barber_id"] else None,
            "master_name": _person_name(r["barber__first_name"], r["barber__last_name"], r["barber__email"]),
            "count": r["count"] or 0,
            "revenue": _fmoney(r["revenue"]),
        }
        for r in rows
    ]


def _services_ranking(appt_qs):
    rows = (
        AppointmentService.objects.filter(appointment__in=appt_qs.filter(status__in=RANKING_STATUSES))
        .values("service_id", "service__name")
        .annotate(
            count=Count("appointment", distinct=True),
            revenue=_sum_money("service__price", filter=Q(appointment__status=Appointment.Status.COMPLETED)),
        )
        .order_by("-revenue", "-count", "service__name")[:TOP_RANKING_ROWS]
    )
    return [
        {
            "service_id": str(r["service_id"]) if r["service_id"] else None,
            "name": r["service__name"] or "—",
            "count": r["count"] or 0,
            "revenue": _fmoney(r["revenue"]),
        }
        for r in rows
    ]


def _clients_visits_ranking(appt_qs):
    rows = (
        appt_qs.filter(status=Appointment.Status.COMPLETED, client__isnull=False)
        .values("client_id", "client__full_name")
        .annotate(count=Count("id"), revenue=_sum_money(APPT_EFFECTIVE_PRICE))
        .order_by("-revenue", "-count")[:TOP_RANKING_ROWS]
    )
    return [
        {
            "client_id": str(r["client_id"]),
            "name": r["client__full_name"] or "—",
            "count": r["count"] or 0,
            "revenue": _fmoney(r["revenue"]),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────
# Касса (construction)
# ─────────────────────────────────────────────────────────────

def _cashflow_qs(company, branch, period: Period):
    qs = CashFlow.objects.filter(
        company=company,
        status=CashFlow.Status.APPROVED,
        created_at__gte=period.start,
        created_at__lt=period.end,
    ).exclude(name__istartswith=MASTER_PAYOUT_FLOW_PREFIX)
    return _branch_scope(qs, branch, include_global=True)


def _cash_block(flow_qs):
    totals = flow_qs.aggregate(
        income=_sum_money("amount", filter=Q(type=CashFlow.Type.INCOME)),
        expense=_sum_money("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
    )
    income = _money(totals["income"])
    expense = _money(totals["expense"])

    by_cashbox = []
    rows = (
        flow_qs.values("cashbox_id", "cashbox__name")
        .annotate(
            ops=Count("id"),
            income=_sum_money("amount", filter=Q(type=CashFlow.Type.INCOME)),
            expense=_sum_money("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
        )
        .order_by("-income", "-ops")
    )
    for r in rows:
        by_cashbox.append({
            "cashbox_id": str(r["cashbox_id"]) if r["cashbox_id"] else None,
            "name": (r["cashbox__name"] or "").strip() or "Касса",
            "ops": r["ops"] or 0,
            "income": _fmoney(r["income"]),
            "expense": _fmoney(r["expense"]),
        })

    block = {
        "totals": {
            "income": _fmoney(income),
            "expense": _fmoney(expense),
            "net": _fmoney(income - expense),
        },
        "by_cashbox": by_cashbox,
    }
    return block, income, expense


def _cashflow_by_day(flow_qs):
    income, expense = {}, {}
    rows = (
        flow_qs.annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(
            inc=_sum_money("amount", filter=Q(type=CashFlow.Type.INCOME)),
            exp=_sum_money("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
        )
    )
    for r in rows:
        income[r["d"]] = _money(r["inc"])
        expense[r["d"]] = _money(r["exp"])
    return income, expense


def _sale_fund(company, branch, period: Period) -> Decimal:
    """
    Фонд выплат мастерам за period.label (PayoutSale.period — datetime месяца).
    """
    qs = PayoutSale.objects.filter(
        company=company,
        period__year=period.date_from.year,
        period__month=period.date_from.month,
    )
    qs = _branch_scope(qs, branch, include_global=True)
    return _money(qs.aggregate(s=_sum_money("total"))["s"])


# ─────────────────────────────────────────────────────────────
# Онлайн-заявки (bookings)
# ─────────────────────────────────────────────────────────────

def _bookings_block(company, branch, period: Period):
    qs = OnlineBooking.objects.filter(
        company=company,
        date__gte=period.date_from,
        date__lte=period.date_to,
    )
    qs = _branch_scope(qs, branch, include_global=False)

    labels = dict(OnlineBooking.Status.choices)
    statuses = [
        {
            "status": r["status"],
            "label": labels.get(r["status"], r["status"]),
            "count": r["c"],
        }
        for r in qs.exclude(status=OnlineBooking.Status.NEW)
        .values("status")
        .annotate(c=Count("id"))
        .order_by("-c")
    ]

    # services хранится JSON-массивом — агрегируем в питоне (строк за месяц немного)
    counter = defaultdict(int)
    names = {}
    for services in qs.values_list("services", flat=True):
        if not isinstance(services, (list, tuple)):
            continue
        for item in services:
            if not isinstance(item, dict):
                continue
            key = str(item.get("service_id") or item.get("id") or item.get("title") or "")
            if not key:
                continue
            counter[key] += 1
            names.setdefault(key, (item.get("title") or item.get("name") or "—"))

    top_services = [
        {"service_id": key, "name": names.get(key, "—"), "count": cnt}
        for key, cnt in sorted(counter.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))[:TOP_BOOKING_SERVICES]
    ]

    return {"statuses": statuses, "top_services": top_services}


# ─────────────────────────────────────────────────────────────
# Товары / продажи (main)
# ─────────────────────────────────────────────────────────────

def _pos_sales_qs(company, branch, period: Period):
    # Дата продажи = paid_at, а если его нет (долг) — created_at.
    # Пишем условие через Q, а не через annotate(Coalesce), чтобы queryset
    # оставался пригодным для values()/__in подзапросов.
    sold_in_period = (
        Q(paid_at__isnull=False, paid_at__gte=period.start, paid_at__lt=period.end)
        | Q(paid_at__isnull=True, created_at__gte=period.start, created_at__lt=period.end)
    )
    qs = Sale.objects.filter(
        sold_in_period,
        company=company,
        status__in=[Sale.Status.PAID, Sale.Status.DEBT],
    )
    return _branch_scope(qs, branch, include_global=True)


def _object_sales_qs(company, branch, period: Period):
    qs = ObjectSale.objects.filter(
        company=company,
        status=ObjectSale.Status.PAID,
        sold_at__gte=period.date_from,
        sold_at__lte=period.date_to,
    )
    return _branch_scope(qs, branch, include_global=True)


def _products_block(company, branch, period: Period, pos_qs, obj_qs):
    line_revenue = ExpressionWrapper(
        F("unit_price") * F("quantity") - Coalesce(F("line_discount"), Value(Z_MONEY, output_field=MONEY_FIELD)),
        output_field=MONEY_FIELD,
    )
    obj_line_revenue = ExpressionWrapper(F("unit_price") * F("quantity"), output_field=MONEY_FIELD)

    bucket = defaultdict(lambda: {"qty": Z_QTY, "revenue": Z_MONEY})

    pos_rows = (
        SaleItem.objects.filter(sale__in=pos_qs)
        .values("name_snapshot")
        .annotate(
            qty=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
            revenue=_sum_money(line_revenue),
        )
    )
    for r in pos_rows:
        name = (r["name_snapshot"] or "").strip() or "Товар"
        bucket[name]["qty"] += Decimal(str(r["qty"] or 0))
        bucket[name]["revenue"] += _money(r["revenue"])

    obj_rows = (
        ObjectSaleItem.objects.filter(sale__in=obj_qs)
        .values("name_snapshot")
        .annotate(
            qty=Coalesce(Sum("quantity"), Value(0)),
            revenue=_sum_money(obj_line_revenue),
        )
    )
    for r in obj_rows:
        name = (r["name_snapshot"] or "").strip() or "Товар"
        bucket[name]["qty"] += Decimal(str(r["qty"] or 0))
        bucket[name]["revenue"] += _money(r["revenue"])

    total_qty = sum((v["qty"] for v in bucket.values()), Z_QTY)
    total_revenue = sum((v["revenue"] for v in bucket.values()), Z_MONEY)

    sales_rows = [
        {"name": name, "qty": _fqty(v["qty"]), "revenue": _fmoney(v["revenue"])}
        for name, v in sorted(bucket.items(), key=lambda kv: (-kv[1]["revenue"], kv[0]))[:TOP_PRODUCT_ROWS]
    ]

    # ---- Приходы от поставщиков за период ----
    receipts = SupplierReceipt.objects.filter(
        company=company,
        created_at__gte=period.start,
        created_at__lt=period.end,
    )
    receipts = _branch_scope(receipts, branch, include_global=True)

    receipt_amount = ExpressionWrapper(
        F("qty") * Coalesce(
            F("purchase_price"),
            F("product__purchase_price"),
            Value(Decimal("0"), output_field=MONEY_FIELD),
        ),
        output_field=MONEY_FIELD,
    )
    suppliers_rows = [
        {
            "supplier_id": str(r["receipt__supplier_id"]) if r["receipt__supplier_id"] else None,
            "name": (r["receipt__supplier__full_name"] or "").strip() or "Поставщик",
            "items": r["items"] or 0,
            "amount": _fmoney(r["amount"]),
        }
        for r in (
            SupplierReceiptItem.objects.filter(receipt__in=receipts)
            .values("receipt__supplier_id", "receipt__supplier__full_name")
            .annotate(items=Count("id"), amount=_sum_money(receipt_amount))
            .order_by("-amount", "-items")[:TOP_PRODUCT_ROWS]
        )
    ]

    # ---- Текущий склад (без фильтра по периоду) ----
    stock_qs = _branch_scope(Product.objects.filter(company=company), branch, include_global=True)
    stock = stock_qs.aggregate(
        positions=Count("id"),
        total_qty=Coalesce(Sum("quantity"), Value(Z_QTY, output_field=QTY_FIELD), output_field=QTY_FIELD),
        stock_value_retail=_sum_money(
            ExpressionWrapper(
                Coalesce(F("quantity"), Value(Z_QTY, output_field=QTY_FIELD)) * F("price"),
                output_field=MONEY_FIELD,
            )
        ),
    )

    return {
        "sales_rows": sales_rows,
        "suppliers_rows": suppliers_rows,
        "stock": {
            "positions": stock["positions"] or 0,
            "total_qty": _fqty(stock["total_qty"]),
            "stock_value_retail": _fmoney(stock["stock_value_retail"]),
        },
        "summary": {
            "total_qty": _fqty(total_qty),
            "total_revenue": _fmoney(total_revenue),
        },
    }


def _clients_sales_ranking(pos_qs, obj_qs):
    bucket = defaultdict(lambda: {"name": "—", "orders": 0, "revenue": Z_MONEY})

    pos_rows = (
        pos_qs.filter(client__isnull=False)
        .values("client_id", "client__full_name")
        .annotate(orders=Count("id"), revenue=_sum_money("total"))
    )
    for r in pos_rows:
        key = str(r["client_id"])
        bucket[key]["name"] = (r["client__full_name"] or "").strip() or "—"
        bucket[key]["orders"] += r["orders"] or 0
        bucket[key]["revenue"] += _money(r["revenue"])

    obj_rows = (
        obj_qs.filter(client__isnull=False)
        .values("client_id", "client__full_name")
        .annotate(orders=Count("id"), revenue=_sum_money("subtotal"))
    )
    for r in obj_rows:
        key = str(r["client_id"])
        bucket[key]["name"] = (r["client__full_name"] or "").strip() or "—"
        bucket[key]["orders"] += r["orders"] or 0
        bucket[key]["revenue"] += _money(r["revenue"])

    rows = sorted(bucket.items(), key=lambda kv: (-kv[1]["revenue"], -kv[1]["orders"]))[:TOP_RANKING_ROWS]
    return [
        {
            "client_id": key,
            "name": v["name"],
            "orders": v["orders"],
            "revenue": _fmoney(v["revenue"]),
        }
        for key, v in rows
    ]


# ─────────────────────────────────────────────────────────────
# Детализация «Приход» / «Расход»
# ─────────────────────────────────────────────────────────────

def _details_block(appt_qs, flow_qs, period: Period, sale_fund: Decimal):
    income_rows = []
    expense_rows = []

    completed = (
        appt_qs.filter(status=Appointment.Status.COMPLETED)
        .select_related("client", "barber")
        .prefetch_related("appointment_services__service")
        .order_by("-start_at")[:MAX_DETAIL_ROWS]
    )
    for appt in completed:
        service_names = [
            a.service.name
            for a in appt.appointment_services.all()
            if getattr(a, "service", None) and a.service.name
        ]
        parts = [", ".join(service_names) or "Услуга"]
        client_name = getattr(appt.client, "full_name", None)
        if client_name:
            parts.append(f"Клиент: {client_name}")
        master = appt.barber
        if master:
            parts.append(f"Мастер: {_person_name(master.first_name, master.last_name, master.email)}")

        amount = _money(appt.price) * (Decimal("1") - _money(appt.discount) / Decimal("100"))
        income_rows.append({
            "source": "Запись",
            "title": " • ".join(parts),
            "amount": _fmoney(amount),
            "date": _day_label(timezone.localtime(appt.start_at)),
            "sort_key": appt.start_at.isoformat(),
        })

    flows = flow_qs.order_by("-created_at")[:MAX_DETAIL_ROWS]
    for flow in flows:
        row = {
            "source": "Касса",
            "title": (flow.name or "").strip() or "Операция по кассе",
            "amount": _fmoney(flow.amount),
            "date": _day_label(timezone.localtime(flow.created_at)),
            "sort_key": flow.created_at.isoformat(),
        }
        if flow.type == CashFlow.Type.INCOME:
            income_rows.append(row)
        else:
            expense_rows.append(row)

    if sale_fund:
        expense_rows.append({
            "source": "Выплаты мастерам",
            "title": f"Период {period.label}",
            "amount": _fmoney(sale_fund),
            "date": period.label,
            "sort_key": period.date_to.isoformat(),
        })

    income_rows.sort(key=lambda r: r["sort_key"], reverse=True)
    expense_rows.sort(key=lambda r: r["sort_key"], reverse=True)
    for row in income_rows + expense_rows:
        row.pop("sort_key", None)

    return {"income": income_rows, "expense": expense_rows}


# ─────────────────────────────────────────────────────────────
# Сборка ответа
# ─────────────────────────────────────────────────────────────

def build_dashboard(company, branch, period: Period) -> dict:
    appt_qs = _appointments_qs(company, branch, period)
    flow_qs = _cashflow_qs(company, branch, period)
    pos_qs = _pos_sales_qs(company, branch, period)
    obj_qs = _object_sales_qs(company, branch, period)

    appt_totals = _appointments_block(appt_qs)
    revenue_completed = _money(appt_totals["revenue_completed"])

    cash_block, cash_income, cash_expense = _cash_block(flow_qs)
    fund = _sale_fund(company, branch, period)

    income_unified = revenue_completed + cash_income
    expense_unified = fund + cash_expense

    # ---- Каталог и клиенты (не зависят от периода) ----
    services_total = _branch_scope(
        Service.objects.filter(company=company), branch, include_global=False
    ).count()
    clients_barber_total = _branch_scope(
        BarberClient.objects.filter(company=company), branch, include_global=False
    ).count()

    market_clients = _branch_scope(
        MarketClient.objects.filter(company=company), branch, include_global=True
    ).exclude(type=MarketClient.StatusClient.SUPPLIERS)
    clients_market_total = market_clients.count()
    active_client_ids = set(pos_qs.filter(client__isnull=False).values_list("client_id", flat=True))
    active_client_ids |= set(obj_qs.filter(client__isnull=False).values_list("client_id", flat=True))
    clients_market_active = len(active_client_ids)

    # ---- Графики ----
    appt_counts_by_day, appt_revenue_by_day = _appointments_by_day(appt_qs)
    cash_income_by_day, cash_expense_by_day = _cashflow_by_day(flow_qs)

    weekday = [0] * 7
    for day, count in appt_counts_by_day.items():
        weekday[day.weekday()] += count

    labels, daily_income, daily_expense = [], [], []
    for day in period.days:
        labels.append(str(day.day))
        daily_income.append(_fmoney(appt_revenue_by_day.get(day, Z_MONEY) + cash_income_by_day.get(day, Z_MONEY)))
        daily_expense.append(_fmoney(cash_expense_by_day.get(day, Z_MONEY)))

    # ---- Навигация ----
    cashbox = _branch_scope(
        Cashbox.objects.filter(company=company), branch, include_global=True
    ).order_by("created_at", "name").values_list("id", flat=True).first()

    return {
        "period": period.as_dict(),
        "totals": {
            "appointments_total": appt_totals["appointments_total"] or 0,
            "appointments_completed": appt_totals["appointments_completed"] or 0,
            "appointments_canceled": appt_totals["appointments_canceled"] or 0,
            "appointments_no_show": appt_totals["appointments_no_show"] or 0,
            "revenue_completed": _fmoney(revenue_completed),
            "services_total": services_total,
            "clients_barber_total": clients_barber_total,
            "clients_market_total": clients_market_total,
            "clients_market_active": clients_market_active,
            "income_unified": _fmoney(income_unified),
            "expense_unified": _fmoney(expense_unified),
            "sale_fund": _fmoney(fund),
        },
        "cash": cash_block,
        "charts": {
            "weekday_appointments": weekday,
            "daily_cashflow": {
                "labels": labels,
                "income": daily_income,
                "expense": daily_expense,
            },
        },
        "rankings": {
            "masters": _masters_ranking(appt_qs),
            "services": _services_ranking(appt_qs),
            "clients_visits": _clients_visits_ranking(appt_qs),
            "clients_sales": _clients_sales_ranking(pos_qs, obj_qs),
        },
        "bookings": _bookings_block(company, branch, period),
        "products": _products_block(company, branch, period, pos_qs, obj_qs),
        "details": _details_block(appt_qs, flow_qs, period, fund),
        "navigation": {
            "default_cashbox_id": str(cashbox) if cashbox else None,
        },
    }


def _cache_key(company_id, branch_id, period: Period) -> str:
    return (
        f"nurcrm:analytics:barber:dashboard:{company_id}:"
        f"{branch_id or 'global'}:{period.date_from.isoformat()}:{period.date_to.isoformat()}"
    )


class BarberAnalyticsDashboardView(CompanyQuerysetMixin, APIView):
    """
    GET /api/barbershop/analytics/dashboard/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD

    Один агрегированный ответ для страницы «Аналитика» сферы услуг.
    Без date_from/date_to берётся текущий месяц.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        if not _can_view_barber_analytics(getattr(request, "user", None)):
            raise PermissionDenied("Нет доступа к аналитике барбершопа.")

        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        branch = self._active_branch()
        period = _parse_period(request)

        ck = _cache_key(
            str(getattr(company, "id", "")),
            str(getattr(branch, "id", "")) if branch else None,
            period,
        )
        cached = cache.get(ck)
        if cached is not None:
            return Response(cached)

        data = build_dashboard(company, branch, period)

        ttl = getattr(settings, "CACHE_TIMEOUT_SHORT", 60)
        cache.set(ck, data, ttl)
        return Response(data)
