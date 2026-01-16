from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings

from . import models


def _ensure_number(document: models.Document):
    # generate number like TYPE-YYYYMMDD-0001 per day+type
    today = timezone.now().date()
    with transaction.atomic():
        seq, created = models.DocumentSequence.objects.select_for_update().get_or_create(
            doc_type=document.doc_type, date=today, defaults={"seq": 0}
        )
        seq.seq += 1
        seq.save()
        document.number = f"{document.doc_type}-{today.strftime('%Y%m%d')}-{seq.seq:04d}"
        document.save()


def recalc_document_totals(document: models.Document) -> models.Document:
    total = Decimal("0.00")
    for item in document.items.all():
        total += (item.line_total or Decimal("0.00"))
    document.total = total.quantize(Decimal("0.01"))
    document.save()
    return document


def _apply_move(move: models.StockMove):
    # apply qty_delta to StockBalance
    bal, _ = models.StockBalance.objects.select_for_update().get_or_create(
        warehouse=move.warehouse, product=move.product, defaults={"qty": Decimal("0.000")}
    )
    bal.qty = Decimal(bal.qty or 0) + Decimal(move.qty_delta or 0)
    bal.save()


def post_document(document: models.Document) -> models.Document:
    if document.status == document.Status.POSTED:
        raise ValueError("Document already posted")

    if not document.items.exists():
        raise ValueError("Cannot post empty document")

    if not document.number:
        _ensure_number(document)

    recalc_document_totals(document)

    allow_negative = getattr(settings, "ALLOW_NEGATIVE_STOCK", False)

    with transaction.atomic():
        # create moves according to type
        if document.doc_type == document.DocType.TRANSFER:
            for item in document.items.all():
                # from
                mv1 = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=-(item.qty),
                )
                # to
                mv2 = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_to,
                    product=item.product,
                    qty_delta=(item.qty),
                )
                # apply with checks
                if not allow_negative:
                    bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                    cur = Decimal(bal.qty) if bal else Decimal("0")
                    if cur + mv1.qty_delta < 0:
                        raise ValueError("Negative stock not allowed")
                _apply_move(mv1)
                _apply_move(mv2)

        elif document.doc_type == document.DocType.INVENTORY:
            for item in document.items.all():
                # fact = item.qty, compare with current
                bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                cur = Decimal(bal.qty) if bal else Decimal("0")
                delta = Decimal(item.qty) - cur
                if delta == 0:
                    continue
                if not allow_negative and cur + delta < 0:
                    raise ValueError("Negative stock not allowed")
                mv = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=delta,
                )
                _apply_move(mv)

        else:
            # other single-warehouse operations
            sign_map = {
                document.DocType.SALE: Decimal("-1"),
                document.DocType.PURCHASE: Decimal("1"),
                document.DocType.SALE_RETURN: Decimal("1"),
                document.DocType.PURCHASE_RETURN: Decimal("-1"),
                document.DocType.RECEIPT: Decimal("1"),
                document.DocType.WRITE_OFF: Decimal("-1"),
            }
            sign = sign_map.get(document.doc_type)
            if sign is None:
                raise ValueError("Unsupported document type for posting")
            for item in document.items.all():
                delta = sign * Decimal(item.qty)
                if not allow_negative:
                    bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                    cur = Decimal(bal.qty) if bal else Decimal("0")
                    if cur + delta < 0:
                        raise ValueError("Negative stock not allowed")
                mv = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=delta,
                )
                _apply_move(mv)

        document.status = document.Status.POSTED
        document.save()

    return document


def unpost_document(document: models.Document) -> models.Document:
    if document.status != document.Status.POSTED:
        raise ValueError("Document is not posted")

    with transaction.atomic():
        moves = list(document.moves.select_for_update())
        for mv in moves:
            bal = models.StockBalance.objects.select_for_update().filter(warehouse=mv.warehouse, product=mv.product).first()
            cur = Decimal(bal.qty) if bal else Decimal("0")
            # reverse
            bal.qty = cur - Decimal(mv.qty_delta)
            bal.save()
            mv.delete()

        document.status = document.Status.DRAFT
        document.save()

    return document
