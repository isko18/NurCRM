from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum, Count, Value as V, F, DecimalField, Q, ExpressionWrapper
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
        try:
            return str(x.quantize(Decimal("0.01")))
        except Exception:
            return str(x)
    try:
        return str(Decimal(str(x)).quantize(Decimal("0.01")))
    except Exception:
        return str(x)


# Сальдо с контрагентом (как в сверке / views_reconciliation): дебет увеличивает долг контрагента перед компанией.
_CP_DOC_DEBIT_TYPES = frozenset(
    {
        wm.Document.DocType.SALE,
        wm.Document.DocType.PURCHASE_RETURN,
    }
)
_CP_DOC_CREDIT_TYPES = frozenset(
    {
        wm.Document.DocType.PURCHASE,
        wm.Document.DocType.SALE_RETURN,
    }
)


def _sum_by_counterparty_id(qs, *, doc_types, amount_field: str):
    rows = (
        qs.filter(doc_type__in=tuple(doc_types))
        .values("counterparty_id")
        .annotate(s=Coalesce(Sum(amount_field), ZERO_MONEY))
    )
    out = {}
    for r in rows:
        cid = r["counterparty_id"]
        if cid is None:
            continue
        out[cid] = (r["s"] or Decimal("0.00")).quantize(Decimal("0.01"))
    return out


def _company_display_name(company) -> str:
    return (getattr(company, "llc", None) or getattr(company, "name", None) or "").strip() or "Компания"


def _build_agent_counterparty_debts(*, company, branch, agent, limit: int = 200):
    """
    Текущие сальдо по контрагентам агента (проведённые товарные и денежные документы).
    balance > 0 — контрагент должен компании (дебиторка); balance < 0 — компания должна контрагенту.
    """
    company_name = _company_display_name(company)
    branch_name = (branch.name.strip() if branch and getattr(branch, "name", None) else "") or None

    cp_qs = wm.Counterparty.objects.filter(company=company, agent=agent)
    if branch is not None:
        cp_qs = cp_qs.filter(branch=branch)
    else:
        cp_qs = cp_qs.filter(branch__isnull=True)

    if not cp_qs.exists():
        return {
            "company_name": company_name,
            "branch_name": branch_name,
            "counterparties_debt_total": "0.00",
            "counterparties_payable_total": "0.00",
            "counterparties": [],
        }

    docs_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        agent=agent,
        status=wm.Document.Status.POSTED,
        doc_type__in=tuple(_CP_DOC_DEBIT_TYPES | _CP_DOC_CREDIT_TYPES),
        counterparty__in=cp_qs,
    )
    if branch is not None:
        docs_qs = docs_qs.filter(warehouse_from__branch=branch)
    else:
        docs_qs = docs_qs.filter(warehouse_from__branch__isnull=True)

    money_qs = wm.MoneyDocument.objects.filter(
        company=company,
        status=wm.MoneyDocument.Status.POSTED,
        counterparty__in=cp_qs,
        doc_type__in=(
            wm.MoneyDocument.DocType.MONEY_EXPENSE,
            wm.MoneyDocument.DocType.MONEY_RECEIPT,
        ),
    )
    if branch is not None:
        money_qs = money_qs.filter(branch=branch)
    else:
        money_qs = money_qs.filter(branch__isnull=True)

    deb = _sum_by_counterparty_id(docs_qs, doc_types=_CP_DOC_DEBIT_TYPES, amount_field="total")
    cred = _sum_by_counterparty_id(docs_qs, doc_types=_CP_DOC_CREDIT_TYPES, amount_field="total")
    m_exp = _sum_by_counterparty_id(
        money_qs, doc_types=frozenset({wm.MoneyDocument.DocType.MONEY_EXPENSE}), amount_field="amount"
    )
    m_rec = _sum_by_counterparty_id(
        money_qs, doc_types=frozenset({wm.MoneyDocument.DocType.MONEY_RECEIPT}), amount_field="amount"
    )

    cp_ids = set(cp_qs.values_list("id", flat=True))
    balances = []
    for cid in cp_ids:
        bal = (
            deb.get(cid, Decimal("0.00"))
            - cred.get(cid, Decimal("0.00"))
            + m_exp.get(cid, Decimal("0.00"))
            - m_rec.get(cid, Decimal("0.00"))
        ).quantize(Decimal("0.01"))
        balances.append((bal, cid))

    balances.sort(key=lambda x: x[0], reverse=True)

    id_to_cp = {cp.id: cp for cp in cp_qs}
    counterparties = []
    total_receivable = Decimal("0.00")
    total_payable = Decimal("0.00")
    for bal, cid in balances:
        if bal == Decimal("0.00"):
            continue
        if bal > 0:
            total_receivable += bal
        else:
            total_payable += abs(bal)
        cp = id_to_cp.get(cid)
        if cp is None:
            continue

        cp_nm = (cp.name or "").strip() or "Контрагент"
        d_sale_pr = deb.get(cid, Decimal("0.00"))
        d_pur_sr = cred.get(cid, Decimal("0.00"))
        d_mexp = m_exp.get(cid, Decimal("0.00"))
        d_mrec = m_rec.get(cid, Decimal("0.00"))
        abs_amt = abs(bal)

        if bal > 0:
            direction = "counterparty_owes_company"
            summary_ru = f"Контрагент «{cp_nm}» должен компании «{company_name}» {_money_str(abs_amt)}."
            debtor = {"role": "counterparty", "name": cp_nm, "counterparty_id": str(cp.id)}
            creditor = {"role": "company", "name": company_name}
        else:
            direction = "company_owes_counterparty"
            summary_ru = f"Компания «{company_name}» должна контрагенту «{cp_nm}» {_money_str(abs_amt)}."
            debtor = {"role": "company", "name": company_name}
            creditor = {"role": "counterparty", "name": cp_nm, "counterparty_id": str(cp.id)}

        counterparties.append(
            {
                "counterparty_id": str(cp.id),
                "name": cp.name,
                "phone": cp.phone or "",
                "balance": _money_str(bal),
                "abs_amount": _money_str(abs_amt),
                "direction": direction,
                "debtor": debtor,
                "creditor": creditor,
                "summary_ru": summary_ru,
                "breakdown": {
                    "sale_and_purchase_return": _money_str(d_sale_pr),
                    "purchase_and_sale_return": _money_str(d_pur_sr),
                    "money_expense": _money_str(d_mexp),
                    "money_receipt": _money_str(d_mrec),
                    "labels_ru": {
                        "sale_and_purchase_return": "Продажи и возвраты поставщику (увеличивают долг контрагента перед компанией)",
                        "purchase_and_sale_return": "Покупки и возвраты от покупателя (уменьшают этот долг)",
                        "money_expense": "Расход денег из кассы контрагенту",
                        "money_receipt": "Приход денег от контрагента в кассу",
                    },
                },
            }
        )
        if limit and len(counterparties) >= limit:
            break

    return {
        "company_name": company_name,
        "branch_name": branch_name,
        "counterparties_debt_total": _money_str(total_receivable),
        "counterparties_payable_total": _money_str(total_payable),
        "counterparties": counterparties,
    }


def _build_sales_by_group(*, sales_items_qs, limit: int = 100):
    """
    Сводка продаж по "группам товаров внутри склада" (WarehouseProductGroup).
    amount считаем по line_total (учитывает скидку строки), qty — по qty.
    """
    qs = (
        sales_items_qs.values("product__product_group_id", "product__product_group__name")
        .annotate(
            docs_count=Count("document_id", distinct=True),
            qty_sum=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(Sum("line_total", output_field=MONEY_FIELD), ZERO_MONEY),
        )
        .order_by("-amount", "-qty_sum")[:limit]
    )
    rows = []
    for r in qs:
        gid = r["product__product_group_id"]
        name = r["product__product_group__name"] or "Без группы"
        rows.append(
            {
                "group_id": str(gid) if gid else None,
                "group_name": name,
                "docs_count": r["docs_count"],
                "qty": str(r["qty_sum"]),
                "amount": _money_str(r["amount"]),
            }
        )
    top = rows[0] if rows else None
    return rows, top


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
    sales_by_product_qs = (
        sales_items_qs
        .values("product_id", "product__name")
        .annotate(
            qty_sum=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(
                Sum(
                    ExpressionWrapper(F("qty") * F("price"), output_field=MONEY_FIELD),
                ),
                ZERO_MONEY,
            ),
        )
        .order_by("-amount", "-qty_sum")[:100]
    )
    sales_by_product = [
        {
            "product_id": str(r["product_id"]),
            "product_name": r["product__name"],
            "qty": str(r["qty_sum"]),
            "amount": _money_str(r["amount"]),
        }
        for r in sales_by_product_qs
    ]

    sales_by_group, top_sales_group = _build_sales_by_group(sales_items_qs=sales_items_qs, limit=100)

    sales_by_warehouse_qs = (
        sales_qs
        .values("warehouse_from_id", "warehouse_from__name")
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("-sales_amount", "-sales_count")
    )
    sales_by_warehouse = [
        {
            "warehouse_id": str(r["warehouse_from_id"]),
            "warehouse_name": r["warehouse_from__name"],
            "sales_count": r["sales_count"],
            "sales_amount": _money_str(r["sales_amount"]),
        }
        for r in sales_by_warehouse_qs
    ]

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

    cp_debts = _build_agent_counterparty_debts(company=company, branch=branch, agent=agent)

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
            "counterparties_debt_total": cp_debts["counterparties_debt_total"],
            "counterparties_payable_total": cp_debts["counterparties_payable_total"],
            "counterparty_debts_company_name": cp_debts["company_name"],
            "counterparty_debts_branch_name": cp_debts["branch_name"],
        },
        "charts": {
            "requests_by_date": requests_by_date,
            "sales_by_date": sales_by_date,
        },
        "details": {
            "sales_by_product": sales_by_product,
            "sales_by_warehouse": sales_by_warehouse,
            "sales_by_group": sales_by_group,
            "top_sales_group": top_sales_group,
            "counterparties_debt": cp_debts["counterparties"],
            "counterparties_debt_notes": {
                "formula_ru": (
                    "Сальдо = (продажи + возврат поставщику) − (покупки + возврат от покупателя) "
                    "+ расход денег из кассы контрагенту − приход денег от контрагента в кассу. "
                    "Положительное сальдо: контрагент должен компании. Отрицательное: компания должна контрагенту."
                ),
            },
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

    sales_items_qs = wm.DocumentItem.objects.filter(document__in=sales_qs)
    sales_by_product_qs = (
        sales_items_qs
        .values("product_id", "product__name")
        .annotate(
            qty_sum=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(
                Sum(
                    ExpressionWrapper(F("qty") * F("price"), output_field=MONEY_FIELD),
                ),
                ZERO_MONEY,
            ),
        )
        .order_by("-amount", "-qty_sum")[:100]
    )
    sales_by_product = [
        {
            "product_id": str(r["product_id"]),
            "product_name": r["product__name"],
            "qty": str(r["qty_sum"]),
            "amount": _money_str(r["amount"]),
        }
        for r in sales_by_product_qs
    ]

    sales_by_group, top_sales_group = _build_sales_by_group(sales_items_qs=sales_items_qs, limit=100)

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

    # per-warehouse details
    approved_by_wh = {
        r["cart__warehouse_id"]: {
            "items_approved": r["items_approved"],
            "carts_approved": r["carts_approved"],
        }
        for r in (
            approved_items_qs
            .values("cart__warehouse_id")
            .annotate(
                items_approved=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY),
                carts_approved=Count("cart_id", distinct=True),
            )
        )
    }

    sales_by_wh = {
        r["warehouse_from_id"]: {
            "sales_count": r["sales_count"],
            "sales_amount": r["sales_amount"],
        }
        for r in (
            sales_qs
            .values("warehouse_from_id")
            .annotate(
                sales_count=Count("id"),
                sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
            )
        )
    }

    on_hand_by_wh = {
        r["warehouse_id"]: {
            "on_hand_qty": r["on_hand_qty"],
            "on_hand_amount": r["on_hand_amount"],
        }
        for r in (
            on_hand_qs
            .values("warehouse_id")
            .annotate(
                on_hand_qty=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY),
                on_hand_amount=Coalesce(
                    Sum(F("qty") * F("product__price"), output_field=MONEY_FIELD),
                    ZERO_MONEY,
                ),
            )
        )
    }

    warehouses_qs = wm.Warehouse.objects.filter(company=company)
    if branch is not None:
        warehouses_qs = warehouses_qs.filter(branch=branch)
    else:
        warehouses_qs = warehouses_qs.filter(branch__isnull=True)

    warehouses = []
    for wh in warehouses_qs:
        approved = approved_by_wh.get(wh.id, {})
        sales = sales_by_wh.get(wh.id, {})
        on_hand = on_hand_by_wh.get(wh.id, {})
        warehouses.append({
            "warehouse_id": str(wh.id),
            "warehouse_name": wh.name,
            "carts_approved": approved.get("carts_approved", 0),
            "items_approved": str(approved.get("items_approved", Decimal("0.000"))),
            "sales_count": sales.get("sales_count", 0),
            "sales_amount": _money_str(sales.get("sales_amount", Decimal("0.00"))),
            "on_hand_qty": str(on_hand.get("on_hand_qty", Decimal("0.000"))),
            "on_hand_amount": _money_str(on_hand.get("on_hand_amount", Decimal("0.00"))),
        })

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
        "details": {
            "warehouses": warehouses,
            "sales_by_product": sales_by_product,
            "sales_by_group": sales_by_group,
            "top_sales_group": top_sales_group,
        },
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_owner_agents_sales")
def build_owner_agents_sales_analytics_payload(
    *,
    company_id: str,
    branch_id: str | None,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",  # kept for signature consistency; not used in this payload
    limit: int = 200,
    offset: int = 0,
    order_by: str = "sales_amount",
):
    """
    Агентская аналитика для владельца: список агентов с продажами за период.
    Считаем только проведённые продажи (Document.POSTED, doc_type=SALE) где agent != null.
    """
    company = Company.objects.get(id=company_id)
    branch = Branch.objects.get(id=branch_id) if branch_id else None
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

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

    summary_sales_count = sales_qs.count()
    summary_sales_amount = sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    summary_sales_qty = wm.DocumentItem.objects.filter(document__in=sales_qs).aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    agents_qs = (
        sales_qs.values(
            "agent_id",
            "agent__first_name",
            "agent__last_name",
            "agent__username",
            "agent__email",
        )
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
            sales_qty=Coalesce(Sum("items__qty", output_field=QTY_FIELD), ZERO_QTY),
        )
    )

    order_key = (order_by or "sales_amount").strip().lower()
    if order_key == "sales_count":
        agents_qs = agents_qs.order_by("-sales_count", "-sales_amount")
    elif order_key == "sales_qty":
        agents_qs = agents_qs.order_by("-sales_qty", "-sales_amount")
    else:
        agents_qs = agents_qs.order_by("-sales_amount", "-sales_count")

    total_agents = agents_qs.count()
    if offset and offset > 0:
        agents_qs = agents_qs[offset:]
    if limit and limit > 0:
        agents_qs = agents_qs[:limit]

    agents = []
    for r in agents_qs:
        name = (
            f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
            or (r.get("agent__username") or "").strip()
            or (r.get("agent__email") or "").strip()
            or "Агент"
        )
        agents.append(
            {
                "agent_id": str(r["agent_id"]),
                "agent_name": name,
                "sales_count": r["sales_count"],
                "sales_qty": str(r["sales_qty"]),
                "sales_amount": _money_str(r["sales_amount"]),
            }
        )

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "summary": {
            "sales_count": summary_sales_count,
            "sales_qty": str(summary_sales_qty),
            "sales_amount": _money_str(summary_sales_amount),
            "agents_with_sales": total_agents,
        },
        "pagination": {
            "limit": int(limit),
            "offset": int(offset),
            "total": int(total_agents),
            "order_by": order_key,
        },
        "agents": agents,
    }
