from django.db import transaction
from django.utils import timezone

from . import models


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

