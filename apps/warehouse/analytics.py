from __future__ import annotations

from datetime import date, timedelta, datetime
from functools import wraps
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum, Count, Value as V, F, DecimalField, Q, ExpressionWrapper, OuterRef, Subquery
from django.db.models.functions import Coalesce, TruncDate, TruncWeek, TruncMonth
from django.utils import timezone

from apps.main.cache_utils import cached_result
from apps.users.models import User, Company, Branch
from apps.warehouse import models as wm
from apps.warehouse.analytics_cache import analytics_version


# typed zeros
MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)
ZERO_MONEY = V(Decimal("0.00"), output_field=MONEY_FIELD)

QTY_FIELD = DecimalField(max_digits=18, decimal_places=3)
ZERO_QTY = V(Decimal("0.000"), output_field=QTY_FIELD)

# Себестоимость единицы в строке документа (C5): зафиксированная при проведении,
# для строк, проведённых до появления поля, — текущая закупочная цена (оценка).
ITEM_UNIT_COST = Coalesce(F("cost_price"), F("product__purchase_price"), ZERO_MONEY, output_field=MONEY_FIELD)
ITEM_LINE_COST = ExpressionWrapper(F("qty") * ITEM_UNIT_COST, output_field=MONEY_FIELD)


def _parse_period(request):
    q = getattr(request, "query_params", getattr(request, "GET", {}))
    today = timezone.localdate()

    def _parse(name) -> date | None:
        v = q.get(name)
        if not v:
            return None
        try:
            s = str(v).strip()
            if "T" in s:
                s = s.split("T")[0]
            elif " " in s:
                s = s.split(" ")[0]
            return date.fromisoformat(s)
        except Exception:
            return None

    raw_period = (q.get("period") or "").strip().lower()
    group_by = (q.get("group_by") or "").strip().lower() or "day"

    raw_date = _parse("date")
    raw_from = _parse("date_from")
    raw_to = _parse("date_to")

    if not raw_period:
        if raw_from or raw_to:
            period = "custom"
        else:
            period = "month"
    else:
        period = raw_period

    if period == "day":
        d = raw_date or raw_from or raw_to or today
        return {"period": "day", "date_from": d, "date_to": d, "group_by": group_by}

    if period == "week":
        date_to = raw_to or raw_date or today
        date_from = raw_from or (date_to - timedelta(days=6))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "week", "date_from": date_from, "date_to": date_to, "group_by": group_by}

    if period == "custom":
        date_to = raw_to or today
        date_from = raw_from or (date_to - timedelta(days=29))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {"period": "custom", "date_from": date_from, "date_to": date_to, "group_by": group_by}

    date_to = raw_to or today
    date_from = raw_from or (date_to - timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return {"period": "month", "date_from": date_from, "date_to": date_to, "group_by": group_by}


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


def _period_iso(p) -> str:
    """
    Normalize TruncDate/TruncWeek/TruncMonth result to ISO date string.
    """
    if p is None:
        return ""
    try:
        # TruncWeek/TruncMonth usually return datetime
        if hasattr(p, "date"):
            d = p.date()
            if hasattr(d, "isoformat"):
                return d.isoformat()
    except Exception:
        pass
    try:
        if hasattr(p, "isoformat"):
            return p.isoformat()
    except Exception:
        pass
    return str(p)


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


def _apply_branch_scope(qs, branch, *, path: str = "branch", all_branches: bool = False):
    """Фильтр по филиалу: один филиал, только глобальные (branch IS NULL) или вся компания."""
    if all_branches:
        return qs
    if branch is not None:
        return qs.filter(**{path: branch})
    return qs.filter(**{f"{path}__isnull": True})


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


SOLD_STATUSES = (wm.Document.Status.POSTED, wm.Document.Status.CASH_PENDING)
_CENT = Decimal("0.01")


def _company_docs_qs(*, company, branch, all_branches: bool, doc_types, statuses, dt_from, dt_to_excl):
    """
    Документы компании за период (A13).
    Обычные — по warehouse_from (компания/филиал склада). Мультискладские продажи/возвраты
    без warehouse_from — по компании/филиалу товаров в строках (иначе они не попадали никуда).
    Документы без warehouse_from и без строк компании приписать нельзя — их разбирает
    команда cleanup_empty_posted_documents.
    """
    item_qs = _apply_branch_scope(
        wm.DocumentItem.objects.filter(product__company=company),
        branch,
        path="product__branch",
        all_branches=all_branches,
    )
    wh_q = Q(warehouse_from__company=company)
    if not all_branches:
        wh_q &= Q(warehouse_from__branch=branch) if branch is not None else Q(warehouse_from__branch__isnull=True)
    no_wh_q = Q(warehouse_from__isnull=True, id__in=item_qs.values("document_id"))
    if all_branches:
        # Document.company (A13): мультискладской документ без строк своей компании,
        # но с компанией в документе, тоже учитывается.
        no_wh_q |= Q(warehouse_from__isnull=True, company=company)
    return wm.Document.objects.filter(
        wh_q | no_wh_q,
        status__in=tuple(statuses),
        doc_type__in=tuple(doc_types),
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )


def _line_amount(net_amount, line_total) -> Decimal:
    """Чистая сумма строки (A8); для строк без backfill net_amount — line_total."""
    net = Decimal(str(net_amount or "0"))
    if net != 0:
        return net
    return Decimal(str(line_total or "0"))


def _amounts_by_line_warehouse(docs_qs) -> dict:
    """
    Сумма документов по складам (A13): {warehouse_id: {"count": int, "amount": Decimal}}.

    Если все строки документа со склада warehouse_from — весь Document.total идёт на него
    (агрегация в БД). Если warehouse_from пуст или строки с других складов (мультисклад) —
    Document.total разносится по складу строки (product.warehouse, иначе warehouse_from)
    пропорционально net_amount строк. Σ по складам == Σ Document.total, т.е. сходится с KPI.
    """
    from django.db.models import Exists, OuterRef

    out: dict = {}

    def _bucket(wid):
        return out.setdefault(wid, {"count": 0, "amount": Decimal("0.00")})

    foreign_line = wm.DocumentItem.objects.filter(
        document_id=OuterRef("pk"), product__warehouse_id__isnull=False
    ).exclude(product__warehouse_id=OuterRef("warehouse_from_id"))
    qs = docs_qs.annotate(_split=Exists(foreign_line))

    simple = qs.filter(_split=False, warehouse_from__isnull=False)
    for r in simple.values("warehouse_from_id").annotate(n=Count("id"), s=Coalesce(Sum("total"), ZERO_MONEY)):
        b = _bucket(r["warehouse_from_id"])
        b["count"] += r["n"]
        b["amount"] += Decimal(r["s"] or 0)

    split = qs.filter(Q(_split=True) | Q(warehouse_from__isnull=True))
    docs = {pk: (Decimal(total or 0), wh_from) for pk, total, wh_from in split.values_list("id", "total", "warehouse_from_id")}
    if not docs:
        return out

    weights: dict = {}
    for doc_id, prod_wh, net, line_total in wm.DocumentItem.objects.filter(
        document__in=split.values("id")
    ).values_list("document_id", "product__warehouse_id", "net_amount", "line_total"):
        wid = prod_wh or docs[doc_id][1]
        if wid is None:
            continue
        per_doc = weights.setdefault(doc_id, {})
        per_doc[wid] = per_doc.get(wid, Decimal("0.00")) + _line_amount(net, line_total)

    for doc_id, (total, wh_from) in docs.items():
        per_doc = weights.get(doc_id) or {}
        weight_sum = sum(per_doc.values(), Decimal("0.00"))
        if weight_sum <= 0:
            if wh_from is None and per_doc:
                # строки есть, но суммы нулевые — делим поровну между складами строк
                per_doc = {wid: Decimal("1") for wid in per_doc}
                weight_sum = Decimal(len(per_doc))
            elif wh_from is not None:
                per_doc, weight_sum = {wh_from: Decimal("1")}, Decimal("1")
            else:
                continue
        shares = {wid: (total * w / weight_sum).quantize(_CENT) for wid, w in per_doc.items()}
        diff = total - sum(shares.values(), Decimal("0.00"))
        if diff:
            biggest = max(per_doc, key=lambda k: per_doc[k])
            shares[biggest] += diff
        for wid, amount in shares.items():
            b = _bucket(wid)
            b["count"] += 1
            b["amount"] += amount
    return out


def _net_by_warehouse(sales_qs, returns_qs) -> dict:
    """{warehouse_id: {"sales_count", "sales_amount" (нетто), "gross_sales_amount", "returns_amount"}} (A9, A13)."""
    sold = _amounts_by_line_warehouse(sales_qs)
    returned = _amounts_by_line_warehouse(returns_qs)
    out = {}
    for wid in set(sold) | set(returned):
        gross = (sold.get(wid) or {}).get("amount", Decimal("0.00"))
        ret = (returned.get(wid) or {}).get("amount", Decimal("0.00"))
        out[wid] = {
            "sales_count": (sold.get(wid) or {}).get("count", 0),
            "sales_amount": gross - ret,
            "gross_sales_amount": gross,
            "returns_amount": ret,
        }
    return out


def _sales_by_date_net(sales_qs, returns_qs, group_by: str) -> list:
    """График продаж: нетто (продажи − возвраты по дате документа возврата) + gross/returns (A9)."""
    trunc = _trunc_by_group("date", group_by)

    def _by_date(qs):
        return {
            row["period"]: row
            for row in qs.annotate(period=trunc).values("period").annotate(
                n=Count("id"), amount=Coalesce(Sum("total"), ZERO_MONEY)
            )
        }

    sold, returned = _by_date(sales_qs), _by_date(returns_qs)
    rows = []
    for key in sorted(set(sold) | set(returned), key=lambda k: (k is None, k)):
        gross = Decimal((sold.get(key) or {}).get("amount") or 0)
        ret = Decimal((returned.get(key) or {}).get("amount") or 0)
        rows.append(
            {
                "date": _period_iso(key),
                "sales_count": (sold.get(key) or {}).get("n", 0),
                "sales_amount": _money_str(gross - ret),
                "gross_sales_amount": _money_str(gross),
                "returns_amount": _money_str(ret),
            }
        )
    return rows


def _lines_by_product_with_cost(items_qs) -> dict:
    """{product_id: {"name", "qty", "revenue" (Σ net_amount), "cogs" (qty × себестоимость строки)}}."""
    out = {}
    for pid, name, qty, net, line_total, pp in items_qs.annotate(_unit_cost=ITEM_UNIT_COST).values_list(
        "product_id", "product__name", "qty", "net_amount", "line_total", "_unit_cost"
    ):
        b = out.setdefault(pid, {"name": name, "qty": Decimal("0.000"), "revenue": Decimal("0.00"), "cogs": Decimal("0.00")})
        q = Decimal(str(qty or "0"))
        b["qty"] += q
        b["revenue"] += _line_amount(net, line_total)
        b["cogs"] += q * Decimal(str(pp or "0"))
    return out


def _build_stock_movement(*, company, branch, all_branches: bool, dt_from, dt_to_excl) -> dict:
    """
    «Движение товара» за период (C2, analytics-coverage §4.2), агрегатами по StockMove.

    Движения проведённых документов — по дате документа (у распроведённых движения
    отвязаны от документа и сюда не попадают); выдача/возврат агента — по created_at.
    cost = qty × себестоимость строки документа (cost_price, иначе закупочная цена товара).
    Ключи с «_» — для расчёта прибыли, в ответ не отдаются.
    """
    moves = _apply_branch_scope(
        wm.StockMove.objects.filter(warehouse__company=company),
        branch,
        path="warehouse__branch",
        all_branches=all_branches,
    )
    DT = wm.Document.DocType
    item_cost_sq = (
        wm.DocumentItem.objects.filter(document_id=OuterRef("document_id"), product_id=OuterRef("product_id"))
        .annotate(_c=ITEM_UNIT_COST)
        .values("_c")[:1]
    )
    item_cost_known_sq = (
        wm.DocumentItem.objects.filter(
            document_id=OuterRef("document_id"), product_id=OuterRef("product_id"), cost_price__isnull=False,
        ).values("cost_price")[:1]
    )
    doc_moves = moves.filter(
        document__isnull=False,
        document__status__in=SOLD_STATUSES,
        document__date__gte=dt_from,
        document__date__lt=dt_to_excl,
    ).annotate(
        _unit_cost=Coalesce(
            Subquery(item_cost_sq, output_field=MONEY_FIELD),
            F("product__purchase_price"),
            ZERO_MONEY,
            output_field=MONEY_FIELD,
        ),
    )
    abs_qty = ExpressionWrapper(F("qty_delta") * V(Decimal("-1")), output_field=QTY_FIELD)
    abs_cost = ExpressionWrapper(F("qty_delta") * V(Decimal("-1")) * F("_unit_cost"), output_field=MONEY_FIELD)
    pos_cost = ExpressionWrapper(F("qty_delta") * F("_unit_cost"), output_field=MONEY_FIELD)
    plus = Q(qty_delta__gt=0)
    minus = Q(qty_delta__lt=0)
    agg = doc_moves.aggregate(
        received_qty=Coalesce(Sum("qty_delta", filter=plus & Q(document__doc_type__in=(DT.PURCHASE, DT.RECEIPT))), ZERO_QTY),
        received_cost=Coalesce(Sum(pos_cost, filter=plus & Q(document__doc_type__in=(DT.PURCHASE, DT.RECEIPT))), ZERO_MONEY),
        shipped_qty=Coalesce(Sum(abs_qty, filter=minus & Q(document__doc_type=DT.SALE)), ZERO_QTY),
        written_off_qty=Coalesce(Sum(abs_qty, filter=minus & Q(document__doc_type=DT.WRITE_OFF)), ZERO_QTY),
        written_off_cost=Coalesce(Sum(abs_cost, filter=minus & Q(document__doc_type=DT.WRITE_OFF)), ZERO_MONEY),
        inventory_surplus_qty=Coalesce(Sum("qty_delta", filter=plus & Q(document__doc_type=DT.INVENTORY)), ZERO_QTY),
        inventory_surplus_cost=Coalesce(Sum(pos_cost, filter=plus & Q(document__doc_type=DT.INVENTORY)), ZERO_MONEY),
        inventory_shortage_qty=Coalesce(Sum(abs_qty, filter=minus & Q(document__doc_type=DT.INVENTORY)), ZERO_QTY),
        inventory_shortage_cost=Coalesce(Sum(abs_cost, filter=minus & Q(document__doc_type=DT.INVENTORY)), ZERO_MONEY),
        transferred_qty=Coalesce(Sum(abs_qty, filter=minus & Q(document__doc_type=DT.TRANSFER)), ZERO_QTY),
    )
    estimated = doc_moves.filter(
        document__doc_type__in=(DT.WRITE_OFF, DT.INVENTORY),
    ).annotate(
        _known=Subquery(item_cost_known_sq, output_field=MONEY_FIELD),
    ).filter(_known__isnull=True).exists()

    agent_moves = moves.filter(created_at__gte=dt_from, created_at__lt=dt_to_excl)
    agent_agg = agent_moves.aggregate(
        issued=Coalesce(
            Sum(abs_qty, filter=Q(source_kind=wm.StockMove.SourceKind.AGENT_ISSUE)), ZERO_QTY
        ),
        returned=Coalesce(
            Sum("qty_delta", filter=Q(source_kind=wm.StockMove.SourceKind.AGENT_RETURN)), ZERO_QTY
        ),
    )

    def _q(x):
        return str(Decimal(x or 0).quantize(Decimal("0.001")))

    return {
        "received_qty": _q(agg["received_qty"]),
        "received_cost": _money_str(agg["received_cost"]),
        "shipped_qty": _q(agg["shipped_qty"]),
        "written_off_qty": _q(agg["written_off_qty"]),
        "written_off_cost": _money_str(agg["written_off_cost"]),
        "inventory_surplus_qty": _q(agg["inventory_surplus_qty"]),
        "inventory_surplus_cost": _money_str(agg["inventory_surplus_cost"]),
        "inventory_shortage_qty": _q(agg["inventory_shortage_qty"]),
        "inventory_shortage_cost": _money_str(agg["inventory_shortage_cost"]),
        "transferred_qty": _q(agg["transferred_qty"]),
        "issued_to_agents_qty": _q(agent_agg["issued"]),
        "returned_from_agents_qty": _q(agent_agg["returned"]),
        "_written_off_cost": Decimal(agg["written_off_cost"] or 0),
        "_inventory_surplus_cost": Decimal(agg["inventory_surplus_cost"] or 0),
        "_inventory_shortage_cost": Decimal(agg["inventory_shortage_cost"] or 0),
        "_cost_is_estimated": estimated,
    }


def _build_sales_by_product(*, sales_items_qs, returns_items_qs=None, limit: int = 100):
    """amount = Σ net_amount продаж − Σ net_amount возвратов (по товару); qty — нетто."""
    def _amount_for(row):
        raw = row.get("net_amount")
        if raw is not None and Decimal(str(raw)) != Decimal("0.00"):
            return Decimal(str(raw))
        line_total = row.get("line_total")
        return Decimal(str(line_total or "0")) if line_total is not None else Decimal("0.00")

    def _agg(qs):
        result = {}
        for row in qs.values("product_id", "product__name", "qty", "net_amount", "line_total"):
            pid = row["product_id"]
            bucket = result.setdefault(pid, {
                "product_id": pid,
                "product__name": row["product__name"],
                "qty_sum": Decimal("0.000"),
                "amount": Decimal("0.00"),
            })
            qty = Decimal(str(row["qty"] or "0"))
            bucket["qty_sum"] += qty
            bucket["amount"] += _amount_for(row)
        return result

    sold = _agg(sales_items_qs)
    returned = _agg(returns_items_qs) if returns_items_qs is not None else {}
    rows = []
    for pid in set(sold) | set(returned):
        sr, rr = sold.get(pid), returned.get(pid)
        amount = Decimal((sr or {}).get("amount") or 0) - Decimal((rr or {}).get("amount") or 0)
        qty = Decimal((sr or {}).get("qty_sum") or 0) - Decimal((rr or {}).get("qty_sum") or 0)
        rows.append((amount, qty, {
            "product_id": str(pid),
            "product_name": (sr or rr)["product__name"],
            "qty": str(qty),
            "amount": _money_str(amount),
        }))
    rows.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r[2] for r in rows[:limit]]


def _build_sales_by_group(*, sales_items_qs, returns_items_qs=None, limit: int = 100):
    """
    Сводка продаж по "группам товаров внутри склада" (WarehouseProductGroup).
    amount = Σ net_amount строк продаж (скидка строки и доля скидки документа)
    минус Σ net_amount строк возвратов; qty — аналогично нетто.
    """
    def _amount_for(row):
        raw = row.get("net_amount")
        if raw is not None and Decimal(str(raw)) != Decimal("0.00"):
            return Decimal(str(raw))
        line_total = row.get("line_total")
        return Decimal(str(line_total or "0")) if line_total is not None else Decimal("0.00")

    def _agg(qs):
        result = {}
        for row in qs.values(
            "product__product_group_id",
            "product__product_group__name",
            "document_id",
            "qty",
            "net_amount",
            "line_total",
        ):
            gid = row["product__product_group_id"]
            bucket = result.setdefault(gid, {
                "product__product_group__name": row["product__product_group__name"],
                "docs_count": set(),
                "qty_sum": Decimal("0.000"),
                "amount": Decimal("0.00"),
            })
            bucket["docs_count"].add(row["document_id"])
            bucket["qty_sum"] += Decimal(str(row["qty"] or "0"))
            bucket["amount"] += _amount_for(row)
        for gid, bucket in result.items():
            bucket["docs_count"] = len(bucket["docs_count"])
        return result

    sold = _agg(sales_items_qs)
    returned = _agg(returns_items_qs) if returns_items_qs is not None else {}
    rows = []
    for gid in set(sold) | set(returned):
        sr = sold.get(gid)
        rr = returned.get(gid)
        src = sr or rr
        amount = Decimal((sr or {}).get("amount") or 0) - Decimal((rr or {}).get("amount") or 0)
        qty = Decimal((sr or {}).get("qty_sum") or 0) - Decimal((rr or {}).get("qty_sum") or 0)
        rows.append(
            {
                "group_id": str(gid) if gid else None,
                "group_name": (src["product__product_group__name"] if src and src.get("product__product_group__name") else "Без группы"),
                "docs_count": (sr or {}).get("docs_count", 0),
                "qty": str(qty),
                "amount": _money_str(amount),
                "_sort": amount,
            }
        )
    rows.sort(
        key=lambda r: (r["_sort"], 1 if r["group_name"] == "Без группы" else 0),
        reverse=True,
    )
    rows = rows[:limit]
    for r in rows:
        r.pop("_sort")
    top = rows[0] if rows else None
    return rows, top


def _build_owner_cash_analytics(*, company, branch, dt_from, dt_to_excl, group_by: str, all_branches: bool = False):
    """
    Аналитика по кассе (MoneyDocument) для owner/admin.
    Считаем только проведённые документы за период: приход/расход, сальдо,
    разбивка по кассам и по категориям (отдельно приход/расход).
    """
    money_qs = wm.MoneyDocument.objects.filter(
        company=company,
        status=wm.MoneyDocument.Status.POSTED,
        # Миграционные приходы (B05) закрывают старые долги, но деньги кассы не двигают.
        is_migration=False,
        doc_type__in=(wm.MoneyDocument.DocType.MONEY_RECEIPT, wm.MoneyDocument.DocType.MONEY_EXPENSE),
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    money_qs = _apply_branch_scope(money_qs, branch, all_branches=all_branches)

    # Долги — все документы с системной категорией «Долги» (system_code=debt), с контрагентом
    # или без. Они НЕ должны попадать ни в обычный приход/расход, ни в графу «операции с
    # контрагентами» — считаются отдельной графой «долг» (money_debt_*).
    is_debt = Q(payment_category__system_code=wm.PaymentCategory.SystemCode.DEBT)
    # QA B11 (A3): операция относится к блоку «контрагенты», если у денежного документа есть
    # контрагент ИЛИ он создан из товарного документа с контрагентом (оплата продажи/закупа,
    # возвраты, предоплата, погашение долга). Раньше авто-оплаты товарных документов и
    # погашения долга попадали в «кассу без контрагентов».
    # Операции по категории «Долги» по-прежнему — отдельная графа money_debt_* (не задваиваем).
    is_cp = (Q(counterparty_id__isnull=False) | Q(source_document__counterparty_id__isnull=False)) & ~is_debt
    # «Касса без контрагентов»: только документы без контрагента и без основания с
    # контрагентом — ручные приходы/расходы, инкассация, зарплата.
    is_regular = ~is_cp & ~is_debt
    RECEIPT = wm.MoneyDocument.DocType.MONEY_RECEIPT
    EXPENSE = wm.MoneyDocument.DocType.MONEY_EXPENSE

    totals = money_qs.aggregate(
        receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_regular), ZERO_MONEY),
        expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_regular), ZERO_MONEY),
        debt_receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_debt), ZERO_MONEY),
        debt_expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_debt), ZERO_MONEY),
        cp_receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_cp), ZERO_MONEY),
        cp_expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_cp), ZERO_MONEY),
        docs_count=Count("id"),
    )
    receipt_total = (totals.get("receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
    expense_total = (totals.get("expense") or Decimal("0.00")).quantize(Decimal("0.01"))
    net_total = (receipt_total - expense_total).quantize(Decimal("0.01"))
    debt_receipt_total = (totals.get("debt_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
    debt_expense_total = (totals.get("debt_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
    debt_net_total = (debt_receipt_total - debt_expense_total).quantize(Decimal("0.01"))
    cp_receipt_total = (totals.get("cp_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
    cp_expense_total = (totals.get("cp_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
    cp_net_total = (cp_receipt_total - cp_expense_total).quantize(Decimal("0.01"))

    # by cash register (and legacy warehouse account if cash_register is null)
    by_cash_register_qs = (
        money_qs.values(
            "cash_register_id",
            "cash_register__name",
            "warehouse_id",
            "warehouse__name",
        )
        .annotate(
            docs_count=Count("id"),
            receipt=Coalesce(
                Sum("amount", filter=Q(doc_type=RECEIPT) & is_regular, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            expense=Coalesce(
                Sum("amount", filter=Q(doc_type=EXPENSE) & is_regular, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            debt_receipt=Coalesce(
                Sum("amount", filter=Q(doc_type=RECEIPT) & is_debt, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            debt_expense=Coalesce(
                Sum("amount", filter=Q(doc_type=EXPENSE) & is_debt, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            cp_receipt=Coalesce(
                Sum("amount", filter=Q(doc_type=RECEIPT) & is_cp, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            cp_expense=Coalesce(
                Sum("amount", filter=Q(doc_type=EXPENSE) & is_cp, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
        )
        .order_by("-receipt", "-expense", "-docs_count")
    )
    cash_by_register = []
    for r in by_cash_register_qs:
        cash_id = r.get("cash_register_id")
        wh_id = r.get("warehouse_id")
        if cash_id:
            kind = "cash_register"
            account_id = str(cash_id)
            account_name = (r.get("cash_register__name") or "").strip() or "Касса"
        else:
            kind = "warehouse_legacy"
            account_id = str(wh_id) if wh_id else None
            account_name = (r.get("warehouse__name") or "").strip() or "Счёт"

        rec = (r.get("receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        exp = (r.get("expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        d_rec = (r.get("debt_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        d_exp = (r.get("debt_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        cp_rec = (r.get("cp_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        cp_exp = (r.get("cp_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        cash_by_register.append(
            {
                "kind": kind,
                "account_id": account_id,
                "account_name": account_name,
                "docs_count": r["docs_count"],
                "money_receipt_amount": _money_str(rec),
                "money_expense_amount": _money_str(exp),
                "money_net_amount": _money_str((rec - exp).quantize(Decimal("0.01"))),
                "money_debt_receipt_amount": _money_str(d_rec),
                "money_debt_expense_amount": _money_str(d_exp),
                "money_debt_net_amount": _money_str((d_rec - d_exp).quantize(Decimal("0.01"))),
                "money_counterparty_receipt_amount": _money_str(cp_rec),
                "money_counterparty_expense_amount": _money_str(cp_exp),
                "money_counterparty_net_amount": _money_str((cp_rec - cp_exp).quantize(Decimal("0.01"))),
            }
        )

    def _by_category(doc_type):
        # Разбивка по категориям — по всем операциям кассы, кроме «Долгов» (в т.ч. с контрагентами, B11):
        # иначе после переноса оплат документов в блок «контрагенты» из отчёта по
        # категориям пропали бы «Продажа» и «Закупка».
        qs = (
            money_qs.filter(Q(doc_type=doc_type) & ~is_debt)
            .values("payment_category_id", "payment_category__title")
            .annotate(
                docs_count=Count("id"),
                amount=Coalesce(Sum("amount", output_field=MONEY_FIELD), ZERO_MONEY),
            )
            .order_by("-amount", "-docs_count")
        )
        out = []
        for row in qs:
            cid = row.get("payment_category_id")
            title = (row.get("payment_category__title") or "").strip() or "Без категории"
            out.append(
                {
                    "category_id": str(cid) if cid else None,
                    "category_title": title,
                    "docs_count": row["docs_count"],
                    "amount": _money_str(row["amount"]),
                }
            )
        return out

    money_receipts_by_category = _by_category(wm.MoneyDocument.DocType.MONEY_RECEIPT)
    money_expenses_by_category = _by_category(wm.MoneyDocument.DocType.MONEY_EXPENSE)

    trunc_money = _trunc_by_group("date", group_by)
    money_by_date_qs = (
        money_qs.annotate(period=trunc_money)
        .values("period")
        .annotate(
            receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_regular), ZERO_MONEY),
            expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_regular), ZERO_MONEY),
            debt_receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_debt), ZERO_MONEY),
            debt_expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_debt), ZERO_MONEY),
            cp_receipt=Coalesce(Sum("amount", filter=Q(doc_type=RECEIPT) & is_cp), ZERO_MONEY),
            cp_expense=Coalesce(Sum("amount", filter=Q(doc_type=EXPENSE) & is_cp), ZERO_MONEY),
            docs_count=Count("id"),
        )
        .order_by("period")
    )
    money_by_date = []
    for row in money_by_date_qs:
        rec = (row.get("receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        exp = (row.get("expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        d_rec = (row.get("debt_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        d_exp = (row.get("debt_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        cp_rec = (row.get("cp_receipt") or Decimal("0.00")).quantize(Decimal("0.01"))
        cp_exp = (row.get("cp_expense") or Decimal("0.00")).quantize(Decimal("0.01"))
        money_by_date.append(
            {
                "date": _period_iso(row["period"]),
                "docs_count": row["docs_count"],
                "money_receipt_amount": _money_str(rec),
                "money_expense_amount": _money_str(exp),
                "money_net_amount": _money_str((rec - exp).quantize(Decimal("0.01"))),
                "money_debt_receipt_amount": _money_str(d_rec),
                "money_debt_expense_amount": _money_str(d_exp),
                "money_debt_net_amount": _money_str((d_rec - d_exp).quantize(Decimal("0.01"))),
                "money_counterparty_receipt_amount": _money_str(cp_rec),
                "money_counterparty_expense_amount": _money_str(cp_exp),
                "money_counterparty_net_amount": _money_str((cp_rec - cp_exp).quantize(Decimal("0.01"))),
            }
        )

    return {
        "summary": {
            "money_docs_count": int(totals.get("docs_count") or 0),
            "money_receipt_amount": _money_str(receipt_total),
            "money_expense_amount": _money_str(expense_total),
            "money_net_amount": _money_str(net_total),
            # Графа «долг»: все операции по системной категории «Долги» (с контрагентом и без),
            # вне обычного прихода/расхода и вне графы «операции с контрагентами».
            "money_debt_receipt_amount": _money_str(debt_receipt_total),
            "money_debt_expense_amount": _money_str(debt_expense_total),
            "money_debt_net_amount": _money_str(debt_net_total),
            # Графа «операции с контрагентами»: денежные взаиморасчёты по контрагентам,
            # вне обычного прихода/расхода (не влияют на сальдо кассы).
            "money_counterparty_receipt_amount": _money_str(cp_receipt_total),
            "money_counterparty_expense_amount": _money_str(cp_expense_total),
            "money_counterparty_net_amount": _money_str(cp_net_total),
        },
        "charts": {
            "money_by_date": money_by_date,
        },
        "details": {
            "cash_by_register": cash_by_register,
            "money_receipts_by_category": money_receipts_by_category,
            "money_expenses_by_category": money_expenses_by_category,
        },
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_agent", version="v4")
def build_agent_warehouse_analytics_payload(
    *,
    company_id: str,
    branch_id: str | None,
    agent_id: str,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",
    cache_ver: int = 0,  # часть ключа кэша; см. analytics_cache
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

    def _agent_docs(doc_type, statuses):
        return _company_docs_qs(
            company=company,
            branch=branch,
            all_branches=False,
            doc_types=(doc_type,),
            statuses=statuses,
            dt_from=dt_from,
            dt_to_excl=dt_to_excl,
        ).filter(agent=agent)

    # Продажи агента: проведённые и «ожидают кассы» (A7), в т.ч. мультискладские без warehouse_from (A13).
    sales_qs = _agent_docs(wm.Document.DocType.SALE, SOLD_STATUSES)
    summary_sales_qs = sales_qs.filter(total__gt=0)
    sales_count = summary_sales_qs.count()
    sales_amount = summary_sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    sales_items_qs = wm.DocumentItem.objects.filter(document__in=sales_qs)
    sales_qty = sales_items_qs.aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")
    returns_qs = _agent_docs(wm.Document.DocType.SALE_RETURN, (wm.Document.Status.POSTED,))

    # По складам: сумма по складу строки, нетто возвратов (A9, A13).
    by_wh = _net_by_warehouse(sales_qs, returns_qs)
    wh_names = dict(wm.Warehouse.objects.filter(id__in=[w for w in by_wh if w]).values_list("id", "name"))
    sales_by_warehouse = sorted(
        (
            {
                "warehouse_id": str(wid),
                "warehouse_name": wh_names.get(wid),
                "sales_count": r["sales_count"],
                "sales_amount": _money_str(r["sales_amount"]),
                "gross_sales_amount": _money_str(r["gross_sales_amount"]),
                "returns_amount": _money_str(r["returns_amount"]),
            }
            for wid, r in by_wh.items()
        ),
        key=lambda row: (Decimal(row["sales_amount"]), row["sales_count"]),
        reverse=True,
    )

    summary_returns_qs = returns_qs.filter(total__gt=0)
    returns_items_qs = wm.DocumentItem.objects.filter(document__in=returns_qs)
    sales_by_product = _build_sales_by_product(
        sales_items_qs=sales_items_qs, returns_items_qs=returns_items_qs, limit=100
    )
    sales_by_group, top_sales_group = _build_sales_by_group(
        sales_items_qs=sales_items_qs, returns_items_qs=returns_items_qs, limit=100
    )
    returns_count = summary_returns_qs.count()
    returns_amount = summary_returns_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    returns_qty = wm.DocumentItem.objects.filter(document__in=summary_returns_qs).aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")
    net_sales_amount = (sales_amount - returns_amount).quantize(Decimal("0.01"))
    net_sales_qty = (sales_qty - returns_qty).quantize(Decimal("0.000"))

    write_off_qs = _agent_docs(wm.Document.DocType.WRITE_OFF, (wm.Document.Status.POSTED,))
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
            "date": _period_iso(row["period"]),
            "carts_approved": row["carts_approved"],
            "items_approved": row["items_approved"],
        }
        for row in requests_by_date_qs
    ]

    sales_by_date = _sales_by_date_net(sales_qs, returns_qs, group_by)

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
            "sales_qty": str(net_sales_qty),
            "sales_amount": _money_str(net_sales_amount),
            "gross_sales_qty": str(sales_qty),
            "gross_sales_amount": _money_str(sales_amount),
            "returns_count": returns_count,
            "returns_amount": _money_str(returns_amount),
            "returns_qty": str(returns_qty),
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


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_owner", version="v6")
def build_owner_warehouse_analytics_payload(
    *,
    company_id: str,
    branch_id: str | None,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",
    all_branches: bool = False,
    cache_ver: int = 0,  # часть ключа кэша; см. analytics_cache
):
    company = Company.objects.get(id=company_id)
    branch = Branch.objects.get(id=branch_id) if branch_id else None
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    req_qs = wm.AgentRequestCart.objects.filter(company=company)
    req_qs = _apply_branch_scope(req_qs, branch, all_branches=all_branches)

    approved_qs = req_qs.filter(
        approved_at__gte=dt_from,
        approved_at__lt=dt_to_excl,
        status=wm.AgentRequestCart.Status.APPROVED,
    )
    approved_items_qs = wm.AgentRequestItem.objects.filter(cart__in=approved_qs)

    items_approved_qty = approved_items_qs.aggregate(
        s=Coalesce(Sum("quantity_requested", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    def _company_docs(doc_type, statuses):
        """Документы компании. Мультискладские без warehouse_from — по компании/филиалу товара."""
        return _company_docs_qs(
            company=company,
            branch=branch,
            all_branches=all_branches,
            doc_types=(doc_type,),
            statuses=statuses,
            dt_from=dt_from,
            dt_to_excl=dt_to_excl,
        )

    # Продажи — ВСЕ продажи компании (с агентом и без), отгруженные: проведённые и «ожидают кассы».
    sales_qs = _company_docs(wm.Document.DocType.SALE, SOLD_STATUSES)
    summary_sales_qs = sales_qs.filter(total__gt=0)
    sales_count = summary_sales_qs.count()
    sales_amount = summary_sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")

    agent_sales_qs = summary_sales_qs.filter(agent__isnull=False)
    agent_sales = agent_sales_qs.aggregate(c=Count("id"), s=Coalesce(Sum("total"), ZERO_MONEY))
    own_sales = summary_sales_qs.filter(agent__isnull=True).aggregate(c=Count("id"), s=Coalesce(Sum("total"), ZERO_MONEY))
    pending_cash = summary_sales_qs.filter(status=wm.Document.Status.CASH_PENDING).aggregate(
        c=Count("id"), s=Coalesce(Sum("total"), ZERO_MONEY)
    )

    kind_totals = {
        r["payment_kind"]: r["s"]
        for r in summary_sales_qs.values("payment_kind").annotate(s=Coalesce(Sum("total"), ZERO_MONEY))
    }
    revenue_by_payment_kind = {
        "cash": _money_str(kind_totals.get("cash", 0) + kind_totals.get(None, 0) + kind_totals.get("", 0)),
        "credit": _money_str(kind_totals.get("credit", 0)),
        "external": _money_str(kind_totals.get("external", 0)),
    }

    returns_qs = _company_docs(wm.Document.DocType.SALE_RETURN, (wm.Document.Status.POSTED,))
    summary_returns_qs = returns_qs.filter(total__gt=0)
    returns_count = summary_returns_qs.count()
    returns_amount = summary_returns_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    net_sales_amount = (sales_amount - returns_amount).quantize(Decimal("0.01"))

    # Остатки: на складах (StockBalance) и у агентов (AgentStockBalance) — раздельно.
    wh_stock_qs = wm.StockBalance.objects.filter(warehouse__company=company)
    if not all_branches:
        if branch is not None:
            wh_stock_qs = wh_stock_qs.filter(warehouse__branch=branch)
        else:
            wh_stock_qs = wh_stock_qs.filter(warehouse__branch__isnull=True)
    wh_stock_qs = wh_stock_qs.filter(qty__gt=0)
    wh_stock_totals = {
        "qty": Decimal("0.000"),
        "amount": Decimal("0.00"),
        "purchase_amount": Decimal("0.00"),
    }
    for row in wh_stock_qs.values_list("qty", "product__price", "product__purchase_price"):
        qty = Decimal(str(row[0] or "0"))
        price = Decimal(str(row[1] or "0"))
        purchase_price = Decimal(str(row[2] or "0"))
        wh_stock_totals["qty"] += qty
        wh_stock_totals["amount"] += qty * price
        wh_stock_totals["purchase_amount"] += qty * purchase_price
    warehouse_on_hand_qty = wh_stock_totals["qty"]
    warehouse_on_hand_amount = wh_stock_totals["amount"]
    warehouse_on_hand_purchase_amount = wh_stock_totals["purchase_amount"]

    on_hand_qs = wm.AgentStockBalance.objects.select_related("product", "agent").filter(company=company)
    on_hand_qs = _apply_branch_scope(on_hand_qs, branch, all_branches=all_branches)

    on_hand_totals = {
        "qty": Decimal("0.000"),
        "amount": Decimal("0.00"),
        "purchase_amount": Decimal("0.00"),
    }
    for row in on_hand_qs.values_list("qty", "product__price", "product__purchase_price"):
        qty = Decimal(str(row[0] or "0"))
        price = Decimal(str(row[1] or "0"))
        purchase_price = Decimal(str(row[2] or "0"))
        on_hand_totals["qty"] += qty
        on_hand_totals["amount"] += qty * price
        on_hand_totals["purchase_amount"] += qty * purchase_price
    on_hand_qty = on_hand_totals["qty"]
    on_hand_amount = on_hand_totals["amount"]
    on_hand_purchase_amount = on_hand_totals["purchase_amount"]

    # График: продажи минус возвраты по датам.
    trunc_sales = _trunc_by_group("date", group_by)
    sales_by_date = _sales_by_date_net(sales_qs, returns_qs, group_by)

    sales_items_qs = wm.DocumentItem.objects.filter(document__in=sales_qs)
    returns_items_qs = wm.DocumentItem.objects.filter(document__in=returns_qs)
    sales_by_product = _build_sales_by_product(
        sales_items_qs=sales_items_qs, returns_items_qs=returns_items_qs, limit=100
    )
    sales_by_group, top_sales_group = _build_sales_by_group(
        sales_items_qs=sales_items_qs, returns_items_qs=returns_items_qs, limit=100
    )

    # Топ агентов: нетто по агенту, доля — от ВСЕХ продаж агентов за период.
    agent_returns_qs = returns_qs.filter(agent__isnull=False)
    agent_returned = {
        r["agent_id"]: Decimal(r["s"] or 0)
        for r in agent_returns_qs.values("agent_id").annotate(s=Coalesce(Sum("total"), ZERO_MONEY))
    }
    agent_rows = []
    for r in agent_sales_qs.values("agent_id", "agent__first_name", "agent__last_name").annotate(
        sales_count=Count("id"), sales_amount=Coalesce(Sum("total"), ZERO_MONEY)
    ):
        net = Decimal(r["sales_amount"] or 0) - agent_returned.get(r["agent_id"], Decimal("0"))
        agent_rows.append((net, r))
    agent_rows.sort(key=lambda t: t[0], reverse=True)
    agents_total = sum((t[0] for t in agent_rows), Decimal("0.00"))
    top_agents_by_sales = [
        {
            "agent_id": str(r["agent_id"]),
            "agent_name": (
                f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
                or "Агент"
            ),
            "sales_count": r["sales_count"],
            "sales_amount": _money_str(net),
            "share_percent": (
                str((net * 100 / agents_total).quantize(Decimal("0.01"))) if agents_total > 0 else "0.00"
            ),
        }
        for net, r in agent_rows[:10]
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

    # Продажи по складам: по складу строки (мультисклад, без warehouse_from), нетто возвратов.
    sales_by_wh = _net_by_warehouse(sales_qs, returns_qs)

    wh_on_hand_by_wh = {}
    for row in wh_stock_qs.values_list("warehouse_id", "qty", "product__price", "product__purchase_price"):
        warehouse_id = row[0]
        qty = Decimal(str(row[1] or "0"))
        price = Decimal(str(row[2] or "0"))
        purchase_price = Decimal(str(row[3] or "0"))
        bucket = wh_on_hand_by_wh.setdefault(
            warehouse_id,
            {"qty": Decimal("0.000"), "amount": Decimal("0.00"), "purchase_amount": Decimal("0.00")},
        )
        bucket["qty"] += qty
        bucket["amount"] += qty * price
        bucket["purchase_amount"] += qty * purchase_price

    on_hand_by_wh = {}
    for row in on_hand_qs.values_list("warehouse_id", "qty", "product__price", "product__purchase_price"):
        warehouse_id = row[0]
        qty = Decimal(str(row[1] or "0"))
        price = Decimal(str(row[2] or "0"))
        purchase_price = Decimal(str(row[3] or "0"))
        bucket = on_hand_by_wh.setdefault(
            warehouse_id,
            {"on_hand_qty": Decimal("0.000"), "on_hand_amount": Decimal("0.00"), "on_hand_purchase_amount": Decimal("0.00")},
        )
        bucket["on_hand_qty"] += qty
        bucket["on_hand_amount"] += qty * price
        bucket["on_hand_purchase_amount"] += qty * purchase_price

    warehouses_qs = wm.Warehouse.objects.filter(company=company)
    warehouses_qs = _apply_branch_scope(warehouses_qs, branch, all_branches=all_branches)

    warehouses = []
    for wh in warehouses_qs:
        approved = approved_by_wh.get(wh.id, {})
        sales = sales_by_wh.get(wh.id, {})
        on_hand = on_hand_by_wh.get(wh.id, {})
        stock = wh_on_hand_by_wh.get(wh.id, {})
        agent_qty = str(on_hand.get("on_hand_qty", Decimal("0.000")))
        agent_amount = _money_str(on_hand.get("on_hand_amount", Decimal("0.00")))
        stock_purchase = _money_str(stock.get("purchase_amount", Decimal("0.00")))
        warehouses.append({
            "warehouse_id": str(wh.id),
            "warehouse_name": wh.name,
            "carts_approved": approved.get("carts_approved", 0),
            "items_approved": str(approved.get("items_approved", Decimal("0.000"))),
            "sales_count": sales.get("sales_count", 0),
            "sales_amount": _money_str(sales.get("sales_amount", Decimal("0.00"))),
            "gross_sales_amount": _money_str(sales.get("gross_sales_amount", Decimal("0.00"))),
            "returns_amount": _money_str(sales.get("returns_amount", Decimal("0.00"))),
            "warehouse_on_hand_qty": str(stock.get("qty", Decimal("0.000"))),
            "warehouse_on_hand_amount": _money_str(stock.get("amount", Decimal("0.00"))),
            "warehouse_on_hand_purchase_amount": stock_purchase,
            "on_hand_purchase_amount": stock_purchase,
            "agent_on_hand_qty": agent_qty,
            "agent_on_hand_amount": agent_amount,
            # DEPRECATED: алиасы agent_on_hand_*
            "on_hand_qty": agent_qty,
            "on_hand_amount": agent_amount,
        })

    purchases_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        status=wm.Document.Status.POSTED,
        doc_type__in=(wm.Document.DocType.PURCHASE, wm.Document.DocType.RECEIPT),
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    purchases_qs = _apply_branch_scope(
        purchases_qs, branch, path="warehouse_from__branch", all_branches=all_branches
    ).filter(total__gt=0)
    purchase_returns_qs = wm.Document.objects.filter(
        warehouse_from__company=company,
        status=wm.Document.Status.POSTED,
        doc_type=wm.Document.DocType.PURCHASE_RETURN,
        date__gte=dt_from,
        date__lt=dt_to_excl,
    )
    purchase_returns_qs = _apply_branch_scope(
        purchase_returns_qs, branch, path="warehouse_from__branch", all_branches=all_branches
    ).filter(total__gt=0)

    purchases_count = purchases_qs.count()
    purchases_amount = purchases_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    purchase_returns_amount = purchase_returns_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    net_purchases_amount = (purchases_amount - purchase_returns_amount).quantize(Decimal("0.01"))

    purchases_by_payment_kind = {
        "cash": "0.00",
        "credit": "0.00",
        "external": "0.00",
    }
    for r in purchases_qs.values("payment_kind").annotate(total=Coalesce(Sum("total"), ZERO_MONEY)):
        kind = (r["payment_kind"] or "cash").lower()
        if kind not in purchases_by_payment_kind:
            continue
        purchases_by_payment_kind[kind] = _money_str(r["total"] or Decimal("0.00"))

    purchases_by_supplier = [
        {
            "counterparty_id": str(r["counterparty_id"] or ""),
            "name": r["counterparty__name"] or "Поставщик",
            "docs_count": r["docs_count"],
            "amount": _money_str(r["amount"] or Decimal("0.00")),
        }
        for r in (
            purchases_qs
            .values("counterparty_id", "counterparty__name")
            .annotate(
                docs_count=Count("id"),
                amount=Coalesce(Sum("total"), ZERO_MONEY),
            )
            .order_by("-amount")[:100]
        )
    ]

    purchases_by_date_qs = (
        purchases_qs
        .annotate(period=trunc_sales)
        .values("period")
        .annotate(amount=Coalesce(Sum("total"), ZERO_MONEY))
        .order_by("period")
    )
    purchases_by_date = [
        {"date": _period_iso(row["period"]), "amount": _money_str(row["amount"])}
        for row in purchases_by_date_qs
    ]

    salary_accrual_qs = wm.AgentSalaryAccrual.objects.filter(
        company=company,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    )
    salary_accrual_qs = _apply_branch_scope(salary_accrual_qs, branch, path="warehouse__branch", all_branches=all_branches)
    salary_accrued_amount = salary_accrual_qs.filter(
        status__in=(wm.AgentSalaryAccrual.Status.ACCRUED, wm.AgentSalaryAccrual.Status.PAID)
    ).aggregate(s=Coalesce(Sum("amount"), ZERO_MONEY))["s"] or Decimal("0.00")
    salary_paid_amount = wm.AgentSalaryPayout.objects.filter(
        company=company,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    ).aggregate(s=Coalesce(Sum("amount"), ZERO_MONEY))["s"] or Decimal("0.00")
    salary_payable_amount = salary_accrual_qs.filter(status=wm.AgentSalaryAccrual.Status.ACCRUED).aggregate(
        s=Coalesce(Sum("amount"), ZERO_MONEY)
    )["s"] or Decimal("0.00")

    salary_by_agent = [
        {
            "agent_id": str(r["agent_id"]),
            "agent_name": (
                f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
                or "Агент"
            ),
            "accrued": _money_str(r["accrued"]),
            "paid": "0.00",
            "payable": "0.00",
        }
        for r in (
            salary_accrual_qs.values("agent_id", "agent__first_name", "agent__last_name")
            .annotate(
                accrued=Coalesce(Sum("amount"), ZERO_MONEY),
            )
            .order_by("-accrued")
        )
    ]
    if salary_by_agent:
        salary_paid_by_agent = {
            str(r["agent_id"]): r["paid"]
            for r in wm.AgentSalaryPayout.objects.filter(
                company=company,
                created_at__gte=dt_from,
                created_at__lt=dt_to_excl,
            ).values("agent_id").annotate(paid=Coalesce(Sum("amount"), ZERO_MONEY))
        }
        for row in salary_by_agent:
            paid = salary_paid_by_agent.get(row["agent_id"], Decimal("0.00"))
            row["paid"] = _money_str(paid)
            row["payable"] = _money_str((Decimal(row["accrued"]) - paid).quantize(Decimal("0.01")))

    sales_items_cost_agg = wm.DocumentItem.objects.filter(document__in=summary_sales_qs).aggregate(
        cogs=Coalesce(
            Sum(
                ITEM_LINE_COST
            ),
            ZERO_MONEY,
        )
    )
    sales_cogs_amount = sales_items_cost_agg["cogs"] or Decimal("0.00")
    return_items_cost_agg = wm.DocumentItem.objects.filter(document__in=summary_returns_qs).aggregate(
        cogs=Coalesce(
            Sum(
                ITEM_LINE_COST
            ),
            ZERO_MONEY,
        )
    )
    return_cogs_amount = return_items_cost_agg["cogs"] or Decimal("0.00")
    cogs_amount = (sales_cogs_amount - return_cogs_amount).quantize(Decimal("0.01"))
    revenue_amount = net_sales_amount
    gross_profit_amount = (revenue_amount - cogs_amount).quantize(Decimal("0.01"))
    gross_margin_percent = (
        (gross_profit_amount * Decimal("100") / revenue_amount).quantize(Decimal("0.01"))
        if revenue_amount > 0
        else Decimal("0.00")
    )
    salary_expense_amount = salary_accrued_amount

    stock_movement = _build_stock_movement(
        company=company, branch=branch, all_branches=all_branches, dt_from=dt_from, dt_to_excl=dt_to_excl,
    )
    # D4: списание — убыток по себестоимости, не деньги. Излишки инвентаризации его уменьшают.
    writeoff_loss_amount = (
        stock_movement["_written_off_cost"]
        + stock_movement["_inventory_shortage_cost"]
        - stock_movement["_inventory_surplus_cost"]
    ).quantize(Decimal("0.01"))
    operating_profit_amount = (
        gross_profit_amount - writeoff_loss_amount - salary_expense_amount
    ).quantize(Decimal("0.01"))
    cost_is_estimated = (
        wm.DocumentItem.objects.filter(
            Q(document__in=summary_sales_qs) | Q(document__in=summary_returns_qs),
            cost_price__isnull=True,
        ).exists()
        or stock_movement["_cost_is_estimated"]
    )

    # Прибыль по товарам: выручка = Σ net_amount строк (как «по товарам», A8) минус возвраты (A9).
    sold_lines = _lines_by_product_with_cost(sales_items_qs.filter(document__in=summary_sales_qs))
    returned_lines = _lines_by_product_with_cost(returns_items_qs.filter(document__in=summary_returns_qs))
    profit_rows = []
    for pid in set(sold_lines) | set(returned_lines):
        s_row = sold_lines.get(pid) or {}
        r_row = returned_lines.get(pid) or {}
        qty = s_row.get("qty", Decimal("0.000")) - r_row.get("qty", Decimal("0.000"))
        revenue = s_row.get("revenue", Decimal("0.00")) - r_row.get("revenue", Decimal("0.00"))
        cogs = s_row.get("cogs", Decimal("0.00")) - r_row.get("cogs", Decimal("0.00"))
        profit = revenue - cogs
        profit_rows.append((revenue, {
            "product_id": str(pid),
            "product_name": s_row.get("name") or r_row.get("name"),
            "qty": str(qty),
            "revenue": _money_str(revenue),
            "cogs": _money_str(cogs),
            "profit": _money_str(profit),
            "margin_percent": _money_str((profit * 100 / revenue).quantize(_CENT) if revenue > 0 else Decimal("0.00")),
        }))
    profit_rows.sort(key=lambda t: t[0], reverse=True)
    profit_by_product = [row for _, row in profit_rows[:100]]

    # Прибыль по агентам: выручка — Σ Document.total (нетто возвратов), себестоимость — отдельно
    # по строкам (раньше Sum(total) шёл через JOIN со строками и умножался на число строк).
    def _cogs_by(items_qs, key):
        return {
            r[key]: Decimal(r["cogs"] or 0)
            for r in items_qs.values(key).annotate(
                cogs=Coalesce(
                    Sum(
                        ITEM_LINE_COST
                    ),
                    ZERO_MONEY,
                )
            )
        }

    summary_sales_items_qs = wm.DocumentItem.objects.filter(document__in=summary_sales_qs)
    summary_return_items_qs = wm.DocumentItem.objects.filter(document__in=summary_returns_qs)
    agent_cogs = _cogs_by(summary_sales_items_qs, "document__agent_id")
    agent_ret_cogs = _cogs_by(summary_return_items_qs, "document__agent_id")
    agent_ret_rev = {
        r["agent_id"]: Decimal(r["s"] or 0)
        for r in summary_returns_qs.values("agent_id").annotate(s=Coalesce(Sum("total"), ZERO_MONEY))
    }
    profit_agent_rows = []
    for r in summary_sales_qs.values("agent_id", "agent__first_name", "agent__last_name").annotate(
        revenue=Coalesce(Sum("total"), ZERO_MONEY)
    ):
        aid = r["agent_id"]
        revenue = Decimal(r["revenue"] or 0) - agent_ret_rev.get(aid, Decimal("0.00"))
        cogs = agent_cogs.get(aid, Decimal("0.00")) - agent_ret_cogs.get(aid, Decimal("0.00"))
        if aid is None:
            name = "Без агента"
        else:
            name = (
                f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
                or "Агент"
            )
        profit_agent_rows.append((revenue, {
            "agent_id": str(aid) if aid else None,
            "agent_name": name,
            "revenue": _money_str(revenue),
            "cogs": _money_str(cogs),
            "profit": _money_str(revenue - cogs),
        }))
    profit_agent_rows.sort(key=lambda t: t[0], reverse=True)
    profit_by_agent = [row for _, row in profit_agent_rows[:100]]

    # Прибыль по датам: нетто-выручка и себестоимость по дате документа.
    date_cogs = _cogs_by(summary_sales_items_qs.annotate(period=_trunc_by_group("document__date", group_by)), "period")
    date_ret_cogs = _cogs_by(summary_return_items_qs.annotate(period=_trunc_by_group("document__date", group_by)), "period")
    date_key_cogs = {}
    for k, v in date_cogs.items():
        date_key_cogs[_period_iso(k)] = date_key_cogs.get(_period_iso(k), Decimal("0.00")) + v
    for k, v in date_ret_cogs.items():
        date_key_cogs[_period_iso(k)] = date_key_cogs.get(_period_iso(k), Decimal("0.00")) - v
    profit_by_date = [
        {
            "date": row["date"],
            "revenue": row["sales_amount"],
            "cogs": _money_str(date_key_cogs.get(row["date"], Decimal("0.00"))),
            "gross_profit": _money_str(Decimal(row["sales_amount"]) - date_key_cogs.get(row["date"], Decimal("0.00"))),
        }
        for row in _sales_by_date_net(summary_sales_qs, summary_returns_qs, group_by)
    ]

    cash = _build_owner_cash_analytics(
        company=company,
        branch=branch,
        dt_from=dt_from,
        dt_to_excl=dt_to_excl,
        group_by=group_by,
        all_branches=all_branches,
    )

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "all_branches": all_branches,
        "branch_id": str(branch.id) if branch else None,
        "summary": {
            "requests_approved": approved_qs.count(),
            "items_approved": str(items_approved_qty),
            "sales_count": sales_count,
            "sales_amount": _money_str(net_sales_amount),
            "gross_sales_amount": _money_str(sales_amount),
            "returns_count": returns_count,
            "returns_amount": _money_str(returns_amount),
            "agent_sales_count": agent_sales["c"],
            "agent_sales_amount": _money_str(agent_sales["s"]),
            "own_sales_count": own_sales["c"],
            "own_sales_amount": _money_str(own_sales["s"]),
            "pending_cash_sales_count": pending_cash["c"],
            "pending_cash_sales_amount": _money_str(pending_cash["s"]),
            "revenue_by_payment_kind": revenue_by_payment_kind,
            "purchases_count": purchases_count,
            "purchases_amount": _money_str(purchases_amount),
            "purchase_returns_amount": _money_str(purchase_returns_amount),
            "net_purchases_amount": _money_str(net_purchases_amount),
            "purchases_by_payment_kind": purchases_by_payment_kind,
            "salary_accrued_amount": _money_str(salary_accrued_amount),
            "salary_paid_amount": _money_str(salary_paid_amount),
            "salary_payable_amount": _money_str(salary_payable_amount),
            "revenue_amount": _money_str(revenue_amount),
            "cogs_amount": _money_str(cogs_amount),
            "gross_profit_amount": _money_str(gross_profit_amount),
            "gross_margin_percent": _money_str(gross_margin_percent),
            "writeoff_loss_amount": _money_str(writeoff_loss_amount),
            "salary_expense_amount": _money_str(salary_expense_amount),
            "operating_profit_amount": _money_str(operating_profit_amount),
            "warehouse_on_hand_qty": str(warehouse_on_hand_qty),
            "warehouse_on_hand_amount": _money_str(warehouse_on_hand_amount),
            "warehouse_on_hand_purchase_amount": _money_str(warehouse_on_hand_purchase_amount),
            "agent_on_hand_qty": str(on_hand_qty),
            "agent_on_hand_amount": _money_str(on_hand_amount),
            "agent_on_hand_purchase_amount": _money_str(on_hand_purchase_amount),
            "cost_is_estimated": cost_is_estimated,
            **{k: v for k, v in stock_movement.items() if not k.startswith("_")},
            # DEPRECATED: алиасы agent_on_hand_*
            "on_hand_qty": str(on_hand_qty),
            "on_hand_amount": _money_str(on_hand_amount),
            # = warehouse_on_hand_purchase_amount, как и в details.warehouses[] (поле новое,
            # раньше не отдавалось — поэтому не алиас агентского остатка).
            "on_hand_purchase_amount": _money_str(warehouse_on_hand_purchase_amount),
            **cash["summary"],
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "purchases_by_date": purchases_by_date,
            "profit_by_date": profit_by_date,
            **cash["charts"],
        },
        "top_agents": {
            "total_sales_amount": _money_str(agents_total),
            "by_sales": top_agents_by_sales,
            "by_received": top_agents_by_received,
        },
        "details": {
            "warehouses": warehouses,
            "sales_by_product": sales_by_product,
            "sales_by_group": sales_by_group,
            "top_sales_group": top_sales_group,
            "purchases_by_supplier": purchases_by_supplier,
            "salary_by_agent": salary_by_agent,
            "profit_by_product": profit_by_product,
            "profit_by_agent": profit_by_agent,
            **cash["details"],
        },
    }


@cached_result(timeout=settings.CACHE_TIMEOUT_ANALYTICS, key_prefix="warehouse_analytics_owner_agents_sales", version="v4")
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
    all_branches: bool = False,
    cache_ver: int = 0,  # часть ключа кэша; см. analytics_cache
):
    """
    Агентская аналитика для владельца: список агентов с продажами за период.
    Продажи агентов (agent != null): проведённые и «ожидают кассы», нетто возвратов.
    """
    company = Company.objects.get(id=company_id)
    branch = Branch.objects.get(id=branch_id) if branch_id else None
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    def _agents_docs(doc_type, statuses):
        return _company_docs_qs(
            company=company,
            branch=branch,
            all_branches=all_branches,
            doc_types=(doc_type,),
            statuses=statuses,
            dt_from=dt_from,
            dt_to_excl=dt_to_excl,
        ).filter(agent__isnull=False)

    sales_qs = _agents_docs(wm.Document.DocType.SALE, SOLD_STATUSES)

    summary_sales_count = sales_qs.count()
    summary_sales_amount = sales_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    summary_sales_qty = wm.DocumentItem.objects.filter(document__in=sales_qs).aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    returns_qs = _agents_docs(wm.Document.DocType.SALE_RETURN, (wm.Document.Status.POSTED,))
    summary_returns_count = returns_qs.count()
    summary_returns_amount = returns_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal("0.00")
    summary_returns_qty = wm.DocumentItem.objects.filter(document__in=returns_qs).aggregate(
        s=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY)
    )["s"] or Decimal("0.000")

    agents_qs = (
        sales_qs.values(
            "agent_id",
            "agent__first_name",
            "agent__last_name",
            "agent__email",
        )
        .annotate(
            sales_count=Count("id"),
            sales_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
    )

    sales_qty_by_agent = {
        row["document__agent_id"]: row["qty"] or Decimal("0.000")
        for row in wm.DocumentItem.objects.filter(document__in=sales_qs)
        .values("document__agent_id")
        .annotate(qty=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY))
    }
    returns_by_agent = {
        row["agent_id"]: row
        for row in returns_qs.values("agent_id").annotate(
            returns_count=Count("id"),
            returns_amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
    }
    returns_qty_by_agent = {
        row["document__agent_id"]: row["qty"] or Decimal("0.000")
        for row in wm.DocumentItem.objects.filter(document__in=returns_qs)
        .values("document__agent_id")
        .annotate(qty=Coalesce(Sum("qty", output_field=QTY_FIELD), ZERO_QTY))
    }

    order_key = (order_by or "sales_amount").strip().lower()
    agents = []
    for r in agents_qs:
        name = (
            f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
            or (r.get("agent__email") or "").strip()
            or "Агент"
        )
        returns = returns_by_agent.get(r["agent_id"], {})
        sales_qty = sales_qty_by_agent.get(r["agent_id"], Decimal("0.000"))
        returns_qty = returns_qty_by_agent.get(r["agent_id"], Decimal("0.000"))
        returns_amount = returns.get("returns_amount", Decimal("0.00"))
        agents.append({
            "agent_id": str(r["agent_id"]),
            "agent_name": name,
            "sales_count": r["sales_count"],
            "sales_qty": str((sales_qty - returns_qty).quantize(Decimal("0.000"))),
            "sales_amount": _money_str((r["sales_amount"] - returns_amount).quantize(Decimal("0.01"))),
            "gross_sales_qty": str(sales_qty),
            "gross_sales_amount": _money_str(r["sales_amount"]),
            "returns_count": returns.get("returns_count", 0),
            "returns_qty": str(returns_qty),
            "returns_amount": _money_str(returns_amount),
        })

    if order_key == "sales_count":
        agents.sort(key=lambda row: (row["sales_count"], Decimal(row["sales_amount"])), reverse=True)
    elif order_key == "sales_qty":
        agents.sort(key=lambda row: (Decimal(row["sales_qty"]), Decimal(row["sales_amount"])), reverse=True)
    else:
        agents.sort(key=lambda row: (Decimal(row["sales_amount"]), row["sales_count"]), reverse=True)
    total_agents = len(agents)
    agents = agents[offset:]
    if limit:
        agents = agents[:limit]

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "summary": {
            "sales_count": summary_sales_count,
            "sales_qty": str((summary_sales_qty - summary_returns_qty).quantize(Decimal("0.000"))),
            "sales_amount": _money_str((summary_sales_amount - summary_returns_amount).quantize(Decimal("0.01"))),
            "gross_sales_qty": str(summary_sales_qty),
            "gross_sales_amount": _money_str(summary_sales_amount),
            "returns_count": summary_returns_count,
            "returns_qty": str(summary_returns_qty),
            "returns_amount": _money_str(summary_returns_amount),
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


# Без собственного кэша: внутри вызывается build_owner_warehouse_analytics_payload, который
# кэшируется с версией КАЖДОЙ компании-партнёра (A11). Отдельный кэш здесь держал бы
# устаревшие цифры до 10 минут после продажи у партнёра.
def build_owner_partners_warehouse_analytics_list_payload(
    *,
    owner_company_id: str,
    period: str,
    date_from: date,
    date_to: date,
):
    """
    Сводная аналитика по всем компаниям-партнёрам (складское партнёрство) за период.
    По умолчанию агрегирует данные партнёра по всем филиалам.
    """
    owner = Company.objects.get(id=owner_company_id)
    partners = []
    for company in wm.list_active_stock_partner_companies(owner):
        partner_id = str(company.id)
        analytics = build_owner_warehouse_analytics_payload(
            company_id=partner_id,
            branch_id=None,
            period=period,
            date_from=date_from,
            date_to=date_to,
            group_by="day",
            all_branches=True,
        )
        partners.append(
            {
                "partner_company_id": partner_id,
                "partner_company_name": company.name or _company_display_name(company),
                "summary": analytics.get("summary") or {},
            }
        )

    return {
        "period": period,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "partners_count": len(partners),
        "partners": partners,
    }


def build_owner_partner_warehouse_analytics_payload(
    *,
    owner_company_id: str,
    partner_company_id: str,
    branch_id: str | None,
    period: str,
    date_from: date,
    date_to: date,
    group_by: str = "day",
    all_branches: bool = True,
):
    """Полная аналитика компании-партнёра (проверка партнёрства — в API view)."""
    partner = Company.objects.get(id=partner_company_id)
    analytics = build_owner_warehouse_analytics_payload(
        company_id=partner_company_id,
        branch_id=branch_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
        all_branches=all_branches,
    )
    return {
        "partner_company": {
            "id": str(partner.id),
            "name": partner.name or _company_display_name(partner),
        },
        **analytics,
    }



def _versioned(cached_fn):
    """Подмешивает версию кэша компании в ключ: после изменений данных кэш не используется."""
    @wraps(cached_fn)
    def wrapper(**kwargs):
        kwargs["cache_ver"] = analytics_version(kwargs["company_id"])
        return cached_fn(**kwargs)

    return wrapper


build_agent_warehouse_analytics_payload = _versioned(build_agent_warehouse_analytics_payload)
build_owner_warehouse_analytics_payload = _versioned(build_owner_warehouse_analytics_payload)
build_owner_agents_sales_analytics_payload = _versioned(build_owner_agents_sales_analytics_payload)
