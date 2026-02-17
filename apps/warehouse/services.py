from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings

from . import models
from .models import q_qty


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
    # Пересчитываем line_total для каждого item (скидка на товар: percent + amount)
    for item in document.items.select_related("product").all():
        q = Decimal(item.qty or 0)
        p = Decimal(item.price or 0)
        dp = Decimal(item.discount_percent or 0) / Decimal("100")
        da = Decimal(item.discount_amount or 0)
        subtotal = (p * q * (Decimal("1") - dp)).quantize(Decimal("0.01"))
        new_line_total = max(Decimal("0.00"), (subtotal - da).quantize(Decimal("0.01")))
        if item.line_total != new_line_total:
            item.line_total = new_line_total
            item.save(update_fields=["line_total"])

    # Сумма по строкам (суммарная стоимость товаров до общей скидки)
    subtotal = sum(
        (item.line_total or Decimal("0.00"))
        for item in document.items.all()
    )
    subtotal = subtotal.quantize(Decimal("0.01"))

    # Общая скидка на документ: percent + amount
    doc_dp = Decimal(document.discount_percent or 0) / Decimal("100")
    doc_da = Decimal(document.discount_amount or 0)
    total = max(Decimal("0.00"), (subtotal * (Decimal("1") - doc_dp) - doc_da).quantize(Decimal("0.01")))
    document.total = total
    document.save(update_fields=["total"])
    return document


def _apply_move(move: models.StockMove):
    # apply qty_delta to StockBalance
    # Оптимизация: используем select_related если warehouse и product уже загружены
    bal, created = models.StockBalance.objects.select_for_update().get_or_create(
        warehouse=move.warehouse, product=move.product, defaults={"qty": Decimal("0.000")}
    )
    # Если StockBalance только что создан и товар принадлежит этому складу, инициализируем из quantity
    if created and move.product.warehouse_id == move.warehouse_id:
        initial_qty = Decimal(move.product.quantity) if move.product.quantity else Decimal("0.000")
        bal.qty = initial_qty
    bal.qty = Decimal(bal.qty or 0) + Decimal(move.qty_delta or 0)
    bal.save()
    if move.product.warehouse_id == move.warehouse_id:
        type(move.product).objects.filter(pk=move.product_id).update(quantity=q_qty(bal.qty))


def _apply_agent_move(move: models.AgentStockMove):
    bal, _ = models.AgentStockBalance.objects.select_for_update().get_or_create(
        agent=move.agent,
        warehouse=move.warehouse,
        product=move.product,
        defaults={
            "qty": Decimal("0.000"),
            "company": move.warehouse.company,
            "branch": move.warehouse.branch,
        },
    )
    bal.qty = Decimal(bal.qty or 0) + Decimal(move.qty_delta or 0)
    bal.save()


def _resolve_money_doc_type(doc_type: str):
    """
    Для каждой складской операции определяем, какой денежный документ нужен.
    None = кассовый документ не создается, но решение approve/reject все равно требуется.
    """
    mapping = {
        models.Document.DocType.SALE: models.MoneyDocument.DocType.MONEY_RECEIPT,
        models.Document.DocType.PURCHASE: models.MoneyDocument.DocType.MONEY_EXPENSE,
        models.Document.DocType.SALE_RETURN: models.MoneyDocument.DocType.MONEY_EXPENSE,
        models.Document.DocType.PURCHASE_RETURN: models.MoneyDocument.DocType.MONEY_RECEIPT,
        models.Document.DocType.RECEIPT: models.MoneyDocument.DocType.MONEY_RECEIPT,
        models.Document.DocType.WRITE_OFF: models.MoneyDocument.DocType.MONEY_EXPENSE,
        # INVENTORY / TRANSFER - без денежного движения (только подтверждение/отклонение).
    }
    return mapping.get(doc_type)


def _pick_single(qs, *, what: str):
    """
    Возвращает единственный объект из qs, иначе:
      - None, если пусто
      - ValueError, если найдено больше одного (неоднозначно)
    """
    objs = list(qs[:2])
    if len(objs) == 1:
        return objs[0]
    if len(objs) == 0:
        return None
    raise ValueError(f"Найдено несколько объектов ({what}). Укажите явно в документе.")


def _create_or_reset_cash_request(document: models.Document):
    """
    После проведения создается запрос на кассовое подтверждение.
    Денежный документ будет создан только на approve.
    """
    money_doc_type = _resolve_money_doc_type(document.doc_type)
    requires_money = (
        document.payment_kind == models.Document.PaymentKind.CASH
        and money_doc_type is not None
        and not document.agent_id
    )
    amount = Decimal(document.total or 0).quantize(Decimal("0.01"))

    req, _created = models.CashApprovalRequest.objects.update_or_create(
        document=document,
        defaults={
            "status": models.CashApprovalRequest.Status.PENDING,
            "requires_money": requires_money,
            "money_doc_type": money_doc_type if requires_money else None,
            "amount": amount,
            "decision_note": "",
            "decided_at": None,
            "decided_by": None,
            "money_document": None,
        },
    )
    return req


def _create_money_document_for_request(document: models.Document, request_obj: models.CashApprovalRequest):
    if not request_obj.requires_money:
        return None

    money_doc_type = request_obj.money_doc_type
    if not money_doc_type:
        raise ValueError("Не удалось определить тип денежного документа.")

    if not document.counterparty_id and document.doc_type in (
        models.Document.DocType.SALE,
        models.Document.DocType.PURCHASE,
        models.Document.DocType.SALE_RETURN,
        models.Document.DocType.PURCHASE_RETURN,
    ):
        raise ValueError("Для проведения в кассу укажите контрагента в документе.")

    amount = Decimal(request_obj.amount or 0).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Сумма для кассы должна быть больше 0.")

    warehouse = document.warehouse_from
    if not warehouse:
        raise ValueError("Для автокассы нужен warehouse_from.")

    company = warehouse.company
    branch = warehouse.branch

    # cash register: from document or auto-pick if unique
    cash_register = getattr(document, "cash_register", None)
    if cash_register is None:
        qs = models.CashRegister.objects.filter(company=company)
        qs = qs.filter(branch=branch) if branch is not None else qs.filter(branch__isnull=True)
        cash_register = _pick_single(qs, what="касс")
        if cash_register is None:
            raise ValueError("Не найдена касса. Создайте кассу или укажите cash_register в документе.")

    if cash_register.company_id != company.id:
        raise ValueError("Касса принадлежит другой компании.")
    if (branch is None and cash_register.branch_id is not None) or (branch is not None and cash_register.branch_id != branch.id):
        raise ValueError("Касса принадлежит другому филиалу.")

    # payment category: from document or auto-pick if unique
    payment_category = getattr(document, "payment_category", None)
    if payment_category is None:
        qs = models.PaymentCategory.objects.filter(company=company)
        qs = qs.filter(branch=branch) if branch is not None else qs.filter(branch__isnull=True)
        payment_category = _pick_single(qs, what="категорий платежа")
        if payment_category is None:
            raise ValueError(
                "Не найдена категория платежа. Создайте категорию или укажите payment_category в документе."
            )

    if payment_category.company_id != company.id:
        raise ValueError("Категория платежа принадлежит другой компании.")
    if (branch is None and payment_category.branch_id is not None) or (branch is not None and payment_category.branch_id != branch.id):
        raise ValueError("Категория платежа принадлежит другому филиалу.")

    from . import services_money

    money_doc = models.MoneyDocument.objects.create(
        doc_type=request_obj.money_doc_type,
        status=models.MoneyDocument.Status.DRAFT,
        cash_register=cash_register,
        counterparty=document.counterparty,
        payment_category=payment_category,
        amount=amount,
        comment=f"АВТО: {document.doc_type} {document.number or document.id}",
        company=company,
        branch=branch,
        source_document=document,
    )
    services_money.post_money_document(money_doc)
    return money_doc


def post_document(document: models.Document, allow_negative: bool = None) -> models.Document:
    if document.status in (document.Status.CASH_PENDING, document.Status.POSTED):
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

    # Если allow_negative не передан явно, берем из настроек
    if allow_negative is None:
        allow_negative = getattr(settings, "ALLOW_NEGATIVE_STOCK", False)
    if document.agent_id:
        allow_negative = False

    with transaction.atomic():
        # Оптимизация: предзагружаем items с продуктами
        items = list(document.items.select_related("product", "product__warehouse", "product__brand", "product__category").all())

        if document.agent_id:
            if document.doc_type in (document.DocType.TRANSFER, document.DocType.INVENTORY):
                raise ValueError("Agent documents cannot be TRANSFER or INVENTORY")

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
                raise ValueError("Unsupported document type for agent posting")

            for item in items:
                delta = sign * Decimal(item.qty)
                bal, _ = models.AgentStockBalance.objects.select_for_update().get_or_create(
                    agent_id=document.agent_id,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    defaults={
                        "qty": Decimal("0.000"),
                        "company": document.warehouse_from.company,
                        "branch": document.warehouse_from.branch,
                    },
                )
                cur = Decimal(bal.qty or 0)
                if not allow_negative and cur + delta < 0:
                    product_display = item.product.article if item.product.article else item.product.name
                    if not product_display:
                        product_display = f"ID {item.product_id}"
                    raise ValueError(
                        f"Недостаточно у агента для товара '{product_display}'. Доступно: {cur}, требуется: {abs(delta)}"
                    )

                move_kind = models.AgentStockMove.MoveKind.RECEIPT if delta > 0 else models.AgentStockMove.MoveKind.EXPENSE
                mv = models.AgentStockMove.objects.create(
                    document=document,
                    agent_id=document.agent_id,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=delta,
                    move_kind=move_kind,
                )
                _apply_agent_move(mv)

        # create moves according to type
        elif document.doc_type == document.DocType.TRANSFER:
            def _get_or_create_transfer_product(source: models.WarehouseProduct, warehouse_to: models.Warehouse):
                qs = models.WarehouseProduct.objects.filter(company_id=source.company_id, warehouse=warehouse_to)

                if source.barcode:
                    existing = qs.filter(barcode=source.barcode).first()
                    if existing:
                        return existing

                if source.code:
                    existing = qs.filter(code=source.code).first()
                    if existing:
                        return existing

                if source.article:
                    existing = qs.filter(article=source.article, name=source.name).first()
                    if existing:
                        return existing

                if source.name:
                    existing = qs.filter(name=source.name).first()
                    if existing:
                        return existing

                code = source.code
                if code and qs.filter(code=code).exists():
                    code = None

                plu = getattr(source, "plu", None)
                if plu is not None and qs.filter(plu=plu).exists():
                    plu = None

                return models.WarehouseProduct.objects.create(
                    company=warehouse_to.company,
                    branch=warehouse_to.branch,
                    warehouse=warehouse_to,
                    brand=source.brand,
                    category=source.category,
                    article=source.article,
                    name=source.name,
                    description=source.description,
                    barcode=source.barcode,
                    code=code,
                    unit=source.unit,
                    is_weight=source.is_weight,
                    purchase_price=source.purchase_price,
                    markup_percent=source.markup_percent,
                    price=source.price,
                    discount_percent=source.discount_percent,
                    plu=plu,
                    country=source.country,
                    status=source.status,
                    stock=source.stock,
                    expiration_date=source.expiration_date,
                    quantity=Decimal("0.000"),
                )

            for item in items:
                if item.product.warehouse_id != document.warehouse_from_id:
                    raise ValueError("Transfer requires product from warehouse_from")
                # Проверка остатков перед созданием moves
                if not allow_negative:
                    bal_from = models.StockBalance.objects.select_for_update().filter(
                        warehouse=document.warehouse_from, product=item.product
                    ).first()
                    if bal_from:
                        cur_from = Decimal(bal_from.qty) if bal_from.qty else Decimal("0")
                    else:
                        # Если StockBalance нет, проверяем quantity товара (если товар принадлежит этому складу)
                        if item.product.warehouse_id == document.warehouse_from_id:
                            cur_from = Decimal(item.product.quantity) if item.product.quantity else Decimal("0")
                        else:
                            cur_from = Decimal("0")
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
                
                # from — расход со склада-источника
                mv1 = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=-(item.qty),
                    move_kind=models.StockMove.MoveKind.EXPENSE,
                )
                # to — приход на склад-приёмник (product in destination warehouse)
                dest_product = _get_or_create_transfer_product(item.product, document.warehouse_to)
                mv2 = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_to,
                    product=dest_product,
                    qty_delta=(item.qty),
                    move_kind=models.StockMove.MoveKind.RECEIPT,
                )
                # apply moves
                _apply_move(mv1)
                _apply_move(mv2)

        elif document.doc_type == document.DocType.INVENTORY:
            for item in items:
                # fact = item.qty, compare with current
                bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                if bal:
                    cur = Decimal(bal.qty) if bal.qty else Decimal("0")
                else:
                    # Если StockBalance нет, проверяем quantity товара (если товар принадлежит этому складу)
                    if item.product.warehouse_id == document.warehouse_from_id:
                        cur = Decimal(item.product.quantity) if item.product.quantity else Decimal("0")
                    else:
                        cur = Decimal("0")
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
                move_kind = models.StockMove.MoveKind.RECEIPT if delta > 0 else models.StockMove.MoveKind.EXPENSE
                mv = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=delta,
                    move_kind=move_kind,
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
                    # Проверяем остатки: сначала в StockBalance, если нет - используем WarehouseProduct.quantity
                    bal = models.StockBalance.objects.select_for_update().filter(warehouse=document.warehouse_from, product=item.product).first()
                    if bal:
                        cur = Decimal(bal.qty) if bal.qty else Decimal("0")
                    else:
                        # Если StockBalance нет, проверяем quantity товара (если товар принадлежит этому складу)
                        if item.product.warehouse_id == document.warehouse_from_id:
                            cur = Decimal(item.product.quantity) if item.product.quantity else Decimal("0")
                        else:
                            cur = Decimal("0")
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
                move_kind = models.StockMove.MoveKind.RECEIPT if delta > 0 else models.StockMove.MoveKind.EXPENSE
                mv = models.StockMove.objects.create(
                    document=document,
                    warehouse=document.warehouse_from,
                    product=item.product,
                    qty_delta=delta,
                    move_kind=move_kind,
                )
                _apply_move(mv)

        document.status = document.Status.CASH_PENDING
        document.save()

        # На этапе post создаем запрос на решение по кассе.
        _create_or_reset_cash_request(document)

    return document


def unpost_document(document: models.Document) -> models.Document:
    if document.status not in (document.Status.POSTED, document.Status.CASH_PENDING):
        raise ValueError("Document is not posted")

    with transaction.atomic():
        if document.agent_id:
            moves = list(document.agent_moves.select_related("warehouse", "product").select_for_update())
            for mv in moves:
                bal, _ = models.AgentStockBalance.objects.select_for_update().get_or_create(
                    agent=mv.agent,
                    warehouse=mv.warehouse,
                    product=mv.product,
                    defaults={
                        "qty": Decimal("0.000"),
                        "company": mv.warehouse.company,
                        "branch": mv.warehouse.branch,
                    },
                )
                cur = Decimal(bal.qty or 0)
                bal.qty = cur - Decimal(mv.qty_delta or 0)
                if bal.qty < 0:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Unposting agent document {document.number} results in negative balance {bal.qty} "
                        f"for product {mv.product_id} at warehouse {mv.warehouse_id}"
                    )
                bal.save()
                if mv.product.warehouse_id == mv.warehouse_id:
                    type(mv.product).objects.filter(pk=mv.product_id).update(quantity=q_qty(bal.qty))
                mv.delete()
        else:
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
                if mv.product.warehouse_id == mv.warehouse_id:
                    type(mv.product).objects.filter(pk=mv.product_id).update(quantity=q_qty(bal.qty))
                mv.delete()

        # Если был уже создан денежный документ - откатим его.
        try:
            money_doc = getattr(document, "money_document", None)
        except Exception:
            money_doc = None
        if money_doc is not None and money_doc.status == models.MoneyDocument.Status.POSTED:
            from . import services_money
            services_money.unpost_money_document(money_doc)

        # Если был pending-запрос на кассу — пометим отклоненным.
        try:
            cash_req = getattr(document, "cash_request", None)
        except Exception:
            cash_req = None
        if cash_req is not None and cash_req.status == models.CashApprovalRequest.Status.PENDING:
            cash_req.status = models.CashApprovalRequest.Status.REJECTED
            cash_req.decision_note = "Отклонено автоматически: документ распроведен."
            cash_req.decided_at = timezone.now()
            cash_req.save(update_fields=["status", "decision_note", "decided_at"])

        document.status = document.Status.DRAFT
        document.save()

    return document


def approve_cash_request(document: models.Document, *, decided_by=None, note: str = "") -> models.Document:
    if document.status != document.Status.CASH_PENDING:
        raise ValueError("Документ не ожидает решения кассы.")

    request_obj = getattr(document, "cash_request", None)
    if request_obj is None:
        raise ValueError("Запрос в кассу не найден.")
    if request_obj.status != models.CashApprovalRequest.Status.PENDING:
        raise ValueError("Запрос в кассу уже обработан.")

    with transaction.atomic():
        money_doc = _create_money_document_for_request(document, request_obj)
        request_obj.status = models.CashApprovalRequest.Status.APPROVED
        request_obj.decision_note = note or ""
        request_obj.decided_at = timezone.now()
        request_obj.decided_by = decided_by
        request_obj.money_document = money_doc
        request_obj.save(update_fields=["status", "decision_note", "decided_at", "decided_by", "money_document"])

        document.status = document.Status.POSTED
        document.save(update_fields=["status"])

    return document


def reject_cash_request(document: models.Document, *, decided_by=None, note: str = "") -> models.Document:
    if document.status != document.Status.CASH_PENDING:
        raise ValueError("Документ не ожидает решения кассы.")

    request_obj = getattr(document, "cash_request", None)
    if request_obj is None:
        raise ValueError("Запрос в кассу не найден.")
    if request_obj.status != models.CashApprovalRequest.Status.PENDING:
        raise ValueError("Запрос в кассу уже обработан.")

    with transaction.atomic():
        # Откатываем складские движения
        unpost_document(document)
        document.status = document.Status.REJECTED
        document.save(update_fields=["status"])

        request_obj.status = models.CashApprovalRequest.Status.REJECTED
        request_obj.decision_note = note or ""
        request_obj.decided_at = timezone.now()
        request_obj.decided_by = decided_by
        request_obj.save(update_fields=["status", "decision_note", "decided_at", "decided_by"])

    return document
