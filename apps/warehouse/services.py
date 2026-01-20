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
    # Оптимизация: используем select_related для продукта
    # Пересчитываем line_total для каждого item, если нужно
    for item in document.items.select_related("product").all():
        # Убеждаемся, что line_total рассчитан правильно
        if not item.line_total or item.line_total == Decimal("0.00"):
            q = Decimal(item.qty or 0)
            p = Decimal(item.price or 0)
            dp = Decimal(item.discount_percent or 0) / Decimal("100")
            item.line_total = (p * q * (Decimal("1") - dp)).quantize(Decimal("0.01"))
            item.save(update_fields=["line_total"])
        total += (item.line_total or Decimal("0.00"))
    document.total = total.quantize(Decimal("0.01"))
    document.save(update_fields=["total"])
    return document


def _apply_move(move: models.StockMove):
    # apply qty_delta to StockBalance
    # Оптимизация: используем select_related если warehouse и product уже загружены
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
    
    # Валидация документа перед проведением
    try:
        document.clean()
    except Exception as e:
        raise ValueError(f"Document validation failed: {str(e)}")
    
    # Валидация всех items
    for item in document.items.select_related("product").all():
        try:
            item.clean()
        except Exception as e:
            raise ValueError(f"Item validation failed for product {item.product_id}: {str(e)}")

    if not document.number:
        _ensure_number(document)

    recalc_document_totals(document)

    allow_negative = getattr(settings, "ALLOW_NEGATIVE_STOCK", False)

    with transaction.atomic():
        # Оптимизация: предзагружаем items с продуктами
        items = list(document.items.select_related("product", "product__warehouse", "product__brand", "product__category").all())
        
        # create moves according to type
        if document.doc_type == document.DocType.TRANSFER:
            for item in items:
                # Проверка остатков перед созданием moves
                if not allow_negative:
                    bal_from = models.StockBalance.objects.select_for_update().filter(
                        warehouse=document.warehouse_from, product=item.product
                    ).first()
                    cur_from = Decimal(bal_from.qty) if bal_from else Decimal("0")
                    qty_to_move = Decimal(item.qty)
                    if cur_from - qty_to_move < 0:
                        # Формируем информативное название товара: артикул или имя
                        if item.product:
                            product_display = item.product.article if item.product.article else item.product.name
                            if not product_display:
                                product_display = f"ID {item.product_id}"
                        else:
                            product_display = f"ID {item.product_id}"
                        warehouse_name = document.warehouse_from.name if document.warehouse_from else "не указан"
                        raise ValueError(f"Недостаточно товара '{product_display}' на складе '{warehouse_name}'. Доступно: {cur_from}, требуется: {qty_to_move}")
                
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
                # apply moves
                _apply_move(mv1)
                _apply_move(mv2)

        elif document.doc_type == document.DocType.INVENTORY:
            for item in items:
                # fact = item.qty, compare with current
                bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                cur = Decimal(bal.qty) if bal else Decimal("0")
                delta = Decimal(item.qty) - cur
                if delta == 0:
                    continue
                if not allow_negative and cur + delta < 0:
                    # Формируем информативное название товара: артикул или имя
                    if item.product:
                        product_display = item.product.article if item.product.article else item.product.name
                        if not product_display:
                            product_display = f"ID {item.product_id}"
                    else:
                        product_display = f"ID {item.product_id}"
                    warehouse_name = document.warehouse_from.name if document.warehouse_from else "не указан"
                    raise ValueError(f"Инвентаризация приведет к отрицательному остатку для товара '{product_display}' на складе '{warehouse_name}'. Текущий остаток: {cur}, устанавливается: {item.qty}")
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
            for item in items:
                delta = sign * Decimal(item.qty)
                if not allow_negative:
                    bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                    cur = Decimal(bal.qty) if bal else Decimal("0")
                    if cur + delta < 0:
                        # Формируем информативное название товара: артикул или имя
                        if item.product:
                            product_display = item.product.article if item.product.article else item.product.name
                            if not product_display:
                                product_display = f"ID {item.product_id}"
                        else:
                            product_display = f"ID {item.product_id}"
                        warehouse_name = document.warehouse_from.name if document.warehouse_from else "не указан"
                        raise ValueError(f"Недостаточно товара '{product_display}' на складе '{warehouse_name}'. Доступно: {cur}, требуется: {abs(delta)}")
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
        # Оптимизация: предзагружаем moves с продуктами и складами
        moves = list(document.moves.select_related("warehouse", "product", "product__warehouse").select_for_update())
        for mv in moves:
            # Получаем или создаем StockBalance (на случай если был удален)
            bal, _ = models.StockBalance.objects.select_for_update().get_or_create(
                warehouse=mv.warehouse, 
                product=mv.product, 
                defaults={"qty": Decimal("0.000")}
            )
            cur = Decimal(bal.qty or 0)
            # reverse: вычитаем qty_delta (т.к. при post мы добавляли)
            bal.qty = cur - Decimal(mv.qty_delta or 0)
            if bal.qty < 0:
                # Логируем предупреждение, но не блокируем отмену
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Unposting document {document.number} results in negative balance {bal.qty} for product {mv.product_id} at warehouse {mv.warehouse_id}")
            bal.save()
            mv.delete()

        document.status = document.Status.DRAFT
        document.save()

    return document
