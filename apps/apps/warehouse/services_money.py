from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from django.utils import timezone

from . import models


def empty_counterparty_mini_analytics() -> dict:
    z = "0.00"
    return {
        "sales": {
            "total": z,
            "count": 0,
            "cash_total": z,
            "credit_total": z,
            "pending_cash": {"count": 0, "total": z},
        },
        "cash": {"received": z, "paid": z, "net": z},
        "debts": {
            "balance": z,
            "counterparty_owes_company": z,
            "company_owes_counterparty": z,
        },
    }


def _dec_q2(x) -> Decimal:
    v = x if x is not None else Decimal("0")
    return Decimal(v).quantize(Decimal("0.01"))


def norm_counterparty_id(pk):
    if pk is None:
        return None
    return pk if isinstance(pk, UUID) else UUID(str(pk))


def get_requested_date_range(mixin):
    request = getattr(mixin, "request", None)
    qp = getattr(request, "query_params", None)
    if qp is None:
        return None, None

    raw_from = qp.get("date_from") or qp.get("period_start")
    raw_to = qp.get("date_to") or qp.get("period_end")

    date_from = parse_date(raw_from) if raw_from else None
    date_to = parse_date(raw_to) if raw_to else None

    if date_from and date_to and date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def apply_requested_date_range(qs, field_name: str, mixin):
    date_from, date_to = get_requested_date_range(mixin)
    if date_from:
        qs = qs.filter(**{f"{field_name}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field_name}__date__lte": date_to})
    return qs


# Дебет/кредит — единая трактовка с актом сверки (views_reconciliation):
#   Дебет  — задолженность контрагента перед компанией (отгрузки в долг, расход денег контрагенту).
#   Кредит — задолженность компании перед контрагентом (приходы товара, поступления денег).
DOC_DEBIT_TYPES = (models.Document.DocType.SALE, models.Document.DocType.PURCHASE_RETURN)
DOC_CREDIT_TYPES = (models.Document.DocType.PURCHASE, models.Document.DocType.SALE_RETURN)


def _empty_period_balance() -> dict:
    z = Decimal("0.00")
    return {
        "opening_debit": z, "opening_credit": z,
        "turnover_debit": z, "turnover_credit": z,
        "closing_debit": z, "closing_credit": z,
    }


def _period_sums(qs, amount_field, debit_types, credit_types, date_from, date_to, *, group):
    """
    Условные суммы по стороне (дебет/кредит) и по периоду (до date_from / внутри [date_from, date_to]).
    group=True → словарь {counterparty_id: row}; group=False → один aggregate-row.
    """
    _dec_field = DecimalField(max_digits=18, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=_dec_field)
    ann = dict(
        opening_debit=Coalesce(Sum(amount_field, filter=Q(doc_type__in=debit_types, date__date__lt=date_from)), zero),
        opening_credit=Coalesce(Sum(amount_field, filter=Q(doc_type__in=credit_types, date__date__lt=date_from)), zero),
        turnover_debit=Coalesce(Sum(amount_field, filter=Q(doc_type__in=debit_types, date__date__gte=date_from, date__date__lte=date_to)), zero),
        turnover_credit=Coalesce(Sum(amount_field, filter=Q(doc_type__in=credit_types, date__date__gte=date_from, date__date__lte=date_to)), zero),
    )
    if group:
        return {r["counterparty_id"]: r for r in qs.values("counterparty_id").annotate(**ann)}
    return qs.aggregate(**ann)


def _combine_period_rows(doc_row, money_row) -> dict:
    doc_row = doc_row or {}
    money_row = money_row or {}
    od = _dec_q2(doc_row.get("opening_debit")) + _dec_q2(money_row.get("opening_debit"))
    oc = _dec_q2(doc_row.get("opening_credit")) + _dec_q2(money_row.get("opening_credit"))
    td = _dec_q2(doc_row.get("turnover_debit")) + _dec_q2(money_row.get("turnover_debit"))
    tc = _dec_q2(doc_row.get("turnover_credit")) + _dec_q2(money_row.get("turnover_credit"))
    return {
        "opening_debit": _dec_q2(od), "opening_credit": _dec_q2(oc),
        "turnover_debit": _dec_q2(td), "turnover_credit": _dec_q2(tc),
        "closing_debit": _dec_q2(od + td), "closing_credit": _dec_q2(oc + tc),
    }


def counterparty_period_balances(
    mixin, *, date_from, date_to,
    counterparty_type=None, counterparty_ids=None, per_counterparty=False,
):
    """
    Сальдо на начало / оборот / сальдо на конец по дебету и кредиту за период.

    Трактовка дебета/кредита — как в акте сверки (см. DOC_DEBIT_TYPES/CREDIT_TYPES,
    деньги: MONEY_EXPENSE → дебет, MONEY_RECEIPT → кредит), чтобы цифры сходились.

    - opening_* — накоплено строго до date_from;
    - turnover_* — внутри [date_from, date_to] включительно;
    - closing_* = opening_* + turnover_*.

    per_counterparty=False → один словарь-итог по всем подходящим контрагентам;
    per_counterparty=True  → {counterparty_id: словарь}.
    """
    Doc = models.Document
    MD = models.MoneyDocument
    f = mixin._filter_qs_company_branch

    docs = Doc.objects.filter(
        status=Doc.Status.POSTED,
        doc_type__in=DOC_DEBIT_TYPES + DOC_CREDIT_TYPES,
        counterparty_id__isnull=False,
    )
    docs = f(docs, company_field="warehouse_from__company_id", branch_field="warehouse_from__branch")

    money = MD.objects.filter(
        status=MD.Status.POSTED,
        doc_type__in=(MD.DocType.MONEY_RECEIPT, MD.DocType.MONEY_EXPENSE),
        counterparty_id__isnull=False,
    )
    money = f(money)

    if counterparty_type:
        docs = docs.filter(counterparty__type=counterparty_type)
        money = money.filter(counterparty__type=counterparty_type)
    if counterparty_ids is not None:
        docs = docs.filter(counterparty_id__in=counterparty_ids)
        money = money.filter(counterparty_id__in=counterparty_ids)

    money_debit = (MD.DocType.MONEY_EXPENSE,)
    money_credit = (MD.DocType.MONEY_RECEIPT,)

    if per_counterparty:
        doc_map = _period_sums(docs, "total", DOC_DEBIT_TYPES, DOC_CREDIT_TYPES, date_from, date_to, group=True)
        money_map = _period_sums(money, "amount", money_debit, money_credit, date_from, date_to, group=True)
        out = {}
        for cid in set(doc_map) | set(money_map):
            out[cid] = _combine_period_rows(doc_map.get(cid), money_map.get(cid))
        return out

    doc_row = _period_sums(docs, "total", DOC_DEBIT_TYPES, DOC_CREDIT_TYPES, date_from, date_to, group=False)
    money_row = _period_sums(money, "amount", money_debit, money_credit, date_from, date_to, group=False)
    return _combine_period_rows(doc_row, money_row)


def bulk_counterparty_mini_analytics(mixin, counterparty_ids) -> dict:
    """
    Та же сводка, что CounterpartyMoneyOperationsView._counterparty_mini_analytics,
    для набора контрагентов (3 агрегирующих запроса).
    """
    ids = list(dict.fromkeys(norm_counterparty_id(pk) for pk in counterparty_ids if pk))
    if not ids:
        return {}

    Doc = models.Document
    MD = models.MoneyDocument
    f = mixin._filter_qs_company_branch
    _dec_field = DecimalField(max_digits=18, decimal_places=2)
    zero_money = Value(Decimal("0.00"), output_field=_dec_field)

    trade_doc_types = (
        Doc.DocType.SALE,
        Doc.DocType.PURCHASE,
        Doc.DocType.SALE_RETURN,
        Doc.DocType.PURCHASE_RETURN,
    )
    doc_debit_types = (Doc.DocType.SALE, Doc.DocType.PURCHASE_RETURN)
    doc_credit_types = (Doc.DocType.PURCHASE, Doc.DocType.SALE_RETURN)
    credit = Doc.PaymentKind.CREDIT
    credit_kinds = (credit, "debt")

    out = {cid: empty_counterparty_mini_analytics() for cid in ids}

    sales_qs = Doc.objects.filter(
        counterparty_id__in=ids,
        doc_type=Doc.DocType.SALE,
        status__in=(Doc.Status.POSTED, Doc.Status.CASH_PENDING),
    )
    sales_qs = f(sales_qs, company_field="warehouse_from__company_id", branch_field="warehouse_from__branch")
    sales_qs = apply_requested_date_range(sales_qs, "date", mixin)

    for row in sales_qs.values("counterparty_id").annotate(
        sales_total=Coalesce(Sum("total"), zero_money),
        sales_count=Count("id"),
        sales_cash_total=Coalesce(Sum("total", filter=~Q(payment_kind__in=credit_kinds)), zero_money),
        sales_credit_total=Coalesce(Sum("total", filter=Q(payment_kind__in=credit_kinds)), zero_money),
        pending_cash_count=Count("id", filter=Q(status=Doc.Status.CASH_PENDING)),
        pending_cash_total=Coalesce(Sum("total", filter=Q(status=Doc.Status.CASH_PENDING)), zero_money),
    ):
        cid = row["counterparty_id"]
        if cid not in out:
            continue
        out[cid]["sales"] = {
            "total": str(_dec_q2(row["sales_total"])),
            "count": row["sales_count"],
            "cash_total": str(_dec_q2(row["sales_cash_total"])),
            "credit_total": str(_dec_q2(row["sales_credit_total"])),
            "pending_cash": {
                "count": row["pending_cash_count"],
                "total": str(_dec_q2(row["pending_cash_total"])),
            },
        }

    docs_qs = Doc.objects.filter(
        counterparty_id__in=ids,
        status=Doc.Status.POSTED,
        doc_type__in=trade_doc_types,
    )
    docs_qs = f(docs_qs, company_field="warehouse_from__company_id", branch_field="warehouse_from__branch")
    docs_qs = apply_requested_date_range(docs_qs, "date", mixin)

    doc_map = {
        r["counterparty_id"]: r
        for r in docs_qs.values("counterparty_id").annotate(
            doc_debit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_debit_types)), zero_money),
            doc_credit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_credit_types)), zero_money),
        )
    }

    money_qs = MD.objects.filter(
        counterparty_id__in=ids,
        status=MD.Status.POSTED,
        doc_type__in=(MD.DocType.MONEY_RECEIPT, MD.DocType.MONEY_EXPENSE),
    )
    money_qs = f(money_qs)
    money_qs = apply_requested_date_range(money_qs, "date", mixin)

    money_map = {
        r["counterparty_id"]: r
        for r in money_qs.values("counterparty_id").annotate(
            m_rec=Coalesce(Sum("amount", filter=Q(doc_type=MD.DocType.MONEY_RECEIPT)), zero_money),
            m_paid=Coalesce(Sum("amount", filter=Q(doc_type=MD.DocType.MONEY_EXPENSE)), zero_money),
        )
    }

    for cid in ids:
        dr = doc_map.get(cid, {})
        mr = money_map.get(cid, {})
        d_deb = _dec_q2(dr.get("doc_debit"))
        d_cre = _dec_q2(dr.get("doc_credit"))
        m_rec = _dec_q2(mr.get("m_rec"))
        m_paid = _dec_q2(mr.get("m_paid"))
        balance = _dec_q2((d_deb + m_paid) - (d_cre + m_rec))
        out[cid]["cash"] = {
            "received": str(m_rec),
            "paid": str(m_paid),
            "net": str(_dec_q2(m_rec - m_paid)),
        }
        out[cid]["debts"] = {
            "balance": str(balance),
            "counterparty_owes_company": str(_dec_q2(balance if balance > 0 else 0)),
            "company_owes_counterparty": str(_dec_q2((-balance) if balance < 0 else 0)),
        }

    # При выбранном периоде добавляем сальдо на начало/оборот/сальдо на конец
    # (явные opening_*/turnover_*/closing_*), которые предпочитает фронт.
    date_from, date_to = get_requested_date_range(mixin)
    if date_from and date_to:
        period_map = counterparty_period_balances(
            mixin, date_from=date_from, date_to=date_to,
            counterparty_ids=ids, per_counterparty=True,
        )
        for cid in ids:
            row = period_map.get(cid) or _empty_period_balance()
            out[cid]["debts"].update({k: str(v) for k, v in row.items()})

    return out


def _ensure_number_money(doc: models.MoneyDocument):
    """
    Генерация номера для денежных документов: TYPE-YYYYMMDD-0001 (как в складских).
    Используем существующую таблицу DocumentSequence.
    """
    today = timezone.now().date()
    with transaction.atomic():
        seq, _created = models.DocumentSequence.objects.select_for_update().get_or_create(
            doc_type=doc.doc_type, date=today, defaults={"seq": 0}
        )
        seq.seq += 1
        seq.save(update_fields=["seq"])
        doc.number = f"{doc.doc_type}-{today.strftime('%Y%m%d')}-{seq.seq:04d}"
        doc.save(update_fields=["number"])


def post_money_document(doc: models.MoneyDocument) -> models.MoneyDocument:
    if doc.status == doc.Status.POSTED:
        raise ValueError("Document already posted")

    # validate
    doc.clean()

    if not doc.number:
        _ensure_number_money(doc)

    with transaction.atomic():
        doc.status = doc.Status.POSTED
        doc.save(update_fields=["status"])

    return doc


def unpost_money_document(doc: models.MoneyDocument) -> models.MoneyDocument:
    if doc.status != doc.Status.POSTED:
        raise ValueError("Document is not posted")

    with transaction.atomic():
        doc.status = doc.Status.DRAFT
        doc.save(update_fields=["status"])

    return doc


def reject_money_document(doc: models.MoneyDocument) -> models.MoneyDocument:
    """
    Отказ проведённого денежного документа: POSTED → REJECTED.
    Движение по кассе откатывается так же, как при unpost (баланс кассы считается
    только по POSTED-документам), но документ остаётся в системе как «Отказан».
    """
    if doc.status != doc.Status.POSTED:
        raise ValueError("Отказать можно только проведённый документ.")

    with transaction.atomic():
        doc.status = doc.Status.REJECTED
        doc.save(update_fields=["status"])

    return doc


def cash_register_balance(cash_register) -> Decimal:
    """Сальдо кассы по проведённым приходам и расходам."""
    if cash_register is None:
        return Decimal("0.00")
    agg = models.MoneyDocument.objects.filter(
        cash_register=cash_register,
        status=models.MoneyDocument.Status.POSTED,
    ).aggregate(
        receipts=Coalesce(
            Sum("amount", filter=Q(doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT)),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        expenses=Coalesce(
            Sum("amount", filter=Q(doc_type=models.MoneyDocument.DocType.MONEY_EXPENSE)),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
    )
    return _dec_q2(agg["receipts"]) - _dec_q2(agg["expenses"])


def _payment_category_incassation(company, branch):
    from .utils import ensure_system_payment_categories

    ensure_system_payment_categories(company, branch)
    cat = models.PaymentCategory.objects.filter(
        company=company,
        branch=branch,
        system_code=models.PaymentCategory.SystemCode.INCASSATION,
    ).first()
    if not cat:
        raise ValueError("Не найдена категория «Инкассация». Обратитесь к администратору.")
    return cat


def post_partner_cash_incassation(
    *,
    cash_register_from,
    cash_register_to,
    amount: Decimal,
    comment: str = "",
    created_by=None,
) -> models.CompanyCashIncassation:
    """
    Инкассация между кассами партнёрских компаний: расход с кассы-источника, приход на кассу-приёмник.
    """
    amount = _dec_q2(amount)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше 0.")

    if cash_register_from.id == cash_register_to.id:
        raise ValueError("Касса-источник и касса-приёмник должны быть разными.")

    cfrom = cash_register_from.company_id
    cto = cash_register_to.company_id
    if cfrom == cto:
        raise ValueError("Инкассация между компаниями: кассы должны принадлежать разным компаниям.")

    if not models.has_active_stock_partnership_between_ids(cfrom, cto):
        raise ValueError("Между компаниями этих касс нет принятого партнёрства.")

    balance = cash_register_balance(cash_register_from)
    if balance < amount:
        raise ValueError(
            f"Недостаточно средств в кассе «{cash_register_from.name}». Доступно: {balance}, требуется: {amount}."
        )

    from_co = cash_register_from.company
    to_co = cash_register_to.company
    base_comment = (comment or "").strip()
    out_note = base_comment or f"Инкассация в «{to_co.name}», касса «{cash_register_to.name}»"
    in_note = base_comment or f"Инкассация из «{from_co.name}», касса «{cash_register_from.name}»"

    with transaction.atomic():
        cat_out = _payment_category_incassation(cash_register_from.company, cash_register_from.branch)
        cat_in = _payment_category_incassation(cash_register_to.company, cash_register_to.branch)

        expense = models.MoneyDocument.objects.create(
            doc_type=models.MoneyDocument.DocType.MONEY_EXPENSE,
            status=models.MoneyDocument.Status.DRAFT,
            cash_register=cash_register_from,
            company=cash_register_from.company,
            branch=cash_register_from.branch,
            payment_category=cat_out,
            amount=amount,
            comment=out_note,
        )
        receipt = models.MoneyDocument.objects.create(
            doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT,
            status=models.MoneyDocument.Status.DRAFT,
            cash_register=cash_register_to,
            company=cash_register_to.company,
            branch=cash_register_to.branch,
            payment_category=cat_in,
            amount=amount,
            comment=in_note,
        )
        post_money_document(expense)
        post_money_document(receipt)

        inc = models.CompanyCashIncassation.objects.create(
            from_company=from_co,
            to_company=to_co,
            cash_register_from=cash_register_from,
            cash_register_to=cash_register_to,
            expense_document=expense,
            receipt_document=receipt,
            amount=amount,
            comment=base_comment,
            created_by=created_by,
        )
    return inc

