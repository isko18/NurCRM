from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
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

    out = {cid: empty_counterparty_mini_analytics() for cid in ids}

    sales_qs = Doc.objects.filter(
        counterparty_id__in=ids,
        doc_type=Doc.DocType.SALE,
        status__in=(Doc.Status.POSTED, Doc.Status.CASH_PENDING),
    )
    sales_qs = f(sales_qs, company_field="warehouse_from__company_id", branch_field="warehouse_from__branch")

    for row in sales_qs.values("counterparty_id").annotate(
        sales_total=Coalesce(Sum("total"), zero_money),
        sales_count=Count("id"),
        sales_cash_total=Coalesce(Sum("total", filter=~Q(payment_kind=credit)), zero_money),
        sales_credit_total=Coalesce(Sum("total", filter=Q(payment_kind=credit)), zero_money),
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

