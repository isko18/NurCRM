from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta, date as dt_date
from uuid import UUID
import logging

from django.db import transaction, connection
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from django.db.models import Q

from apps.main.models import (
    BranchTransfer,
    BranchTransferItem,
    Product,
    ProductExpiryBatch,
    ProductInventorySession,
    _pg_advisory_xact_lock_company,
)
from apps.users.models import Branch, Company

logger = logging.getLogger(__name__)


def generate_branch_transfer_number(company) -> str:
    """
    Последовательная генерация номера ПЕР-000001 по компании в рамках транзакции.
    Вызывается под advisory-lock.
    """
    _pg_advisory_xact_lock_company(company.id)
    numbers = BranchTransfer.objects.filter(
        company=company,
        number__startswith="ПЕР-",
    ).values_list("number", flat=True)
    max_num = 0
    for num_str in numbers:
        try:
            val = int(num_str.split("-")[1])
            if val > max_num:
                max_num = val
        except (IndexError, ValueError):
            continue
    return f"ПЕР-{max_num + 1:06d}"


def is_user_admin_or_owner(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return (
        getattr(user, "is_superuser", False)
        or bool(getattr(user, "owned_company", None))
        or getattr(user, "is_admin", False)
        or getattr(user, "role", None) in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор")
    )


def get_user_allowed_branch_ids(user) -> set:
    allowed = set()
    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)):
        allowed.update(str(x) for x in branch_ids)
    if hasattr(user, "branch_memberships"):
        allowed.update(str(x) for x in user.branch_memberships.values_list("branch_id", flat=True))
    if hasattr(user, "branches"):
        allowed.update(str(x) for x in user.branches.values_list("id", flat=True))
    if getattr(user, "branch_id", None):
        allowed.add(str(user.branch_id))
    return allowed


def create_and_execute_branch_transfer(user, data: dict, idempotency_key: str = None) -> BranchTransfer:
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if not company:
        raise ValidationError({"detail": "Компания пользователя не найдена."})

    # Проверка идемпотентности
    if idempotency_key:
        idempotency_key = str(idempotency_key).strip()
        existing = BranchTransfer.objects.filter(
            company=company,
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            return existing

    # Проверка прав: can_view_branch или owner/admin
    is_admin = is_user_admin_or_owner(user)
    if not is_admin and not getattr(user, "can_view_branch", False):
        raise PermissionDenied("У вас нет прав для работы с филиалами.")

    # 1. Валидация from_branch / to_branch
    raw_from = data.get("from_branch")
    raw_to = data.get("to_branch")

    from_branch = None
    if raw_from:
        try:
            from_branch_id = UUID(str(raw_from))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"from_branch": ["Некорректный UUID филиала-отправителя."]})
        try:
            from_branch = Branch.objects.get(id=from_branch_id, company=company)
        except Branch.DoesNotExist:
            raise ValidationError({"from_branch": ["Филиал-отправитель не найден в вашей компании."]})

    to_branch = None
    if raw_to:
        try:
            to_branch_id = UUID(str(raw_to))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"to_branch": ["Некорректный UUID филиала-получателя."]})
        try:
            to_branch = Branch.objects.get(id=to_branch_id, company=company)
        except Branch.DoesNotExist:
            raise ValidationError({"to_branch": ["Филиал-получатель не найден в вашей компании."]})

    # from_branch != to_branch (включая оба None)
    if from_branch == to_branch:
        raise ValidationError({"to_branch": ["Склад-получатель должен отличаться от склада-отправителя."]})

    # Филиал-получатель должен быть активен (из неактивного вывозить можно)
    if to_branch is not None and not to_branch.is_active:
        raise ValidationError({"to_branch": ["Филиал-получатель деактивирован. Перемещение невозможно."]})

    # Ограничения для пользователя-сотрудника с фиксированным филиалом
    if not is_admin:
        allowed_branch_ids = get_user_allowed_branch_ids(user)
        if allowed_branch_ids:
            if from_branch is None or str(from_branch.id) not in allowed_branch_ids:
                raise PermissionDenied("Создавать перемещения можно только из своего филиала.")

    # Проверка открытой инвентаризации
    inv_branches = []
    if from_branch:
        inv_branches.append(from_branch)
    if to_branch:
        inv_branches.append(to_branch)
    
    inv_qs = ProductInventorySession.objects.filter(
        company=company,
        status=ProductInventorySession.Status.DRAFT,
    )
    if from_branch is None or to_branch is None:
        has_open_inv = inv_qs.filter(
            Q(branch__isnull=True) | Q(branch__in=inv_branches)
        ).exists()
    else:
        has_open_inv = inv_qs.filter(branch__in=inv_branches).exists()

    if has_open_inv:
        raise ValidationError({"detail": "На одном из складов открыта инвентаризация. Перемещение временно заблокировано."})

    # 2. Валидация даты
    raw_date = data.get("date")
    if not raw_date:
        transfer_date = timezone.localdate()
    elif isinstance(raw_date, dt_date):
        transfer_date = raw_date
    else:
        try:
            transfer_date = dt_date.fromisoformat(str(raw_date))
        except (ValueError, TypeError):
            raise ValidationError({"date": ["Ожидается дата в формате YYYY-MM-DD."]})

    max_allowed_date = timezone.localdate() + timedelta(days=1)
    if transfer_date > max_allowed_date:
        raise ValidationError({"date": ["Дата не может быть позже чем завтра."]})

    # 3. Валидация позиций (items)
    items_data = data.get("items")
    if not items_data or not isinstance(items_data, list):
        raise ValidationError({"items": ["Список позиций не может быть пустым."]})

    # Проверка дубликатов product
    seen_products = set()
    product_uuids = []
    for item in items_data:
        p_raw = item.get("product") if isinstance(item, dict) else None
        if not p_raw:
            continue
        try:
            p_uuid = UUID(str(p_raw))
        except (ValueError, TypeError):
            continue
        if p_uuid in seen_products:
            raise ValidationError({"items": ["Позиции содержат дублирующиеся товары."]})
        seen_products.add(p_uuid)
        product_uuids.append(p_uuid)

    comment = str(data.get("comment") or "").strip()

    # 4. Транзакционное проведение
    with transaction.atomic():
        _pg_advisory_xact_lock_company(company.id)

        # Выбираем товары источника с блокировкой строк в детерминированном порядке по id
        source_products_qs = (
            Product.objects.select_for_update()
            .filter(id__in=product_uuids, company=company)
            .order_by("id")
        )
        if from_branch is None:
            source_products_qs = source_products_qs.filter(branch__isnull=True)
        else:
            source_products_qs = source_products_qs.filter(branch=from_branch)

        source_products_map = {p.id: p for p in source_products_qs}

        # Валидация каждого элемента по индексу
        item_errors = []
        has_errors = False
        parsed_items = []

        for idx, it in enumerate(items_data):
            errs = {}
            if not isinstance(it, dict):
                item_errors.append({"non_field_errors": ["Некорректный формат строки позиции."]})
                has_errors = True
                continue

            p_raw = it.get("product")
            p_uuid = None
            try:
                p_uuid = UUID(str(p_raw))
            except (ValueError, TypeError):
                errs["product"] = ["Некорректный UUID товара."]

            product_obj = source_products_map.get(p_uuid) if p_uuid else None
            if not product_obj and "product" not in errs:
                errs["product"] = ["Товар не найден на складе-отправителе."]

            raw_qty = it.get("quantity")
            qty = None
            try:
                qty = Decimal(str(raw_qty))
                if qty <= Decimal("0"):
                    errs["quantity"] = ["Количество должно быть больше нуля."]
            except Exception:
                errs["quantity"] = ["Некорректное значение количества."]

            if product_obj and qty is not None and "quantity" not in errs:
                if not product_obj.is_weight and (qty % Decimal("1") != Decimal("0")):
                    errs["quantity"] = ["Для штучного товара количество должно быть целым числом."]
                
                avail = product_obj.quantity or Decimal("0")
                if qty > avail:
                    errs["quantity"] = [f"Недостаточно на складе: доступно {avail}"]

            if errs:
                has_errors = True
                item_errors.append(errs)
            else:
                item_errors.append({})
                parsed_items.append((product_obj, qty))

        if has_errors:
            raise ValidationError({"items": item_errors})

        # Генерируем номер накладной
        number = generate_branch_transfer_number(company)

        transfer = BranchTransfer.objects.create(
            company=company,
            number=number,
            status=BranchTransfer.Status.COMPLETED,
            date=transfer_date,
            from_branch=from_branch,
            to_branch=to_branch,
            comment=comment,
            created_by=user,
            idempotency_key=idempotency_key,
        )

        total_quantity = Decimal("0")
        total_amount = Decimal("0")

        # Проводим каждую позицию по Схеме B
        for source_product, qty in parsed_items:
            # 1. Уменьшаем остаток у источника
            source_product.quantity = (source_product.quantity or Decimal("0")) - qty
            source_product.save(update_fields=["quantity", "updated_at"])

            # 2. Находим или создаем двойник на складе-получателе
            dest_product = None
            if source_product.barcode and source_product.barcode.strip():
                dest_qs = Product.objects.select_for_update().filter(
                    company=company,
                    barcode=source_product.barcode.strip(),
                )
                if to_branch is None:
                    dest_qs = dest_qs.filter(branch__isnull=True)
                else:
                    dest_qs = dest_qs.filter(branch=to_branch)
                dest_product = dest_qs.first()

            if not dest_product and (source_product.article or source_product.name):
                dest_qs = Product.objects.select_for_update().filter(
                    company=company,
                    name=source_product.name,
                    article=source_product.article or "",
                )
                if to_branch is None:
                    dest_qs = dest_qs.filter(branch__isnull=True)
                else:
                    dest_qs = dest_qs.filter(branch=to_branch)
                dest_product = dest_qs.first()

            if dest_product:
                dest_product.quantity = (dest_product.quantity or Decimal("0")) + qty
                dest_product.save(update_fields=["quantity", "updated_at"])
            else:
                # Создаем копию товара на складе-получателе
                dest_product = Product(
                    company=company,
                    branch=to_branch,
                    kind=source_product.kind,
                    client=source_product.client,
                    name=source_product.name,
                    article=source_product.article or "",
                    description=source_product.description or "",
                    barcode=source_product.barcode or None,
                    brand=source_product.brand,
                    category=source_product.category,
                    unit=source_product.unit or "шт",
                    is_weight=source_product.is_weight,
                    is_adult=source_product.is_adult,
                    quantity=qty,
                    minimum_quantity=source_product.minimum_quantity,
                    purchase_price=source_product.purchase_price,
                    markup_percent=source_product.markup_percent,
                    price=source_product.price,
                    wholesale_price=source_product.wholesale_price,
                    discount_percent=source_product.discount_percent,
                    duration_min=source_product.duration_min,
                    performer_commission_percent=source_product.performer_commission_percent,
                    country=source_product.country or "",
                    status=source_product.status or Product.Status.ACCEPTED,
                    hotkey_group=source_product.hotkey_group,
                    stock=source_product.stock,
                    expiration_date=source_product.expiration_date,
                    shelf_life_days=source_product.shelf_life_days,
                    created_by=user,
                )
                dest_product.save()

            # 3. Партии и сроки годности (FEFO)
            _transfer_expiry_batches(source_product, dest_product, qty, transfer)

            # 4. Учётная цена: purchase_price (себестоимость); если нет — розничная price
            unit_price = source_product.purchase_price if (
                source_product.purchase_price is not None and source_product.purchase_price > Decimal("0")
            ) else (source_product.price or Decimal("0"))
            unit_price = Decimal(str(unit_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            line_amount = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            BranchTransferItem.objects.create(
                transfer=transfer,
                product=source_product,
                dest_product=dest_product,
                name=source_product.name,
                article=source_product.article or "",
                barcode=source_product.barcode or "",
                unit=source_product.unit or "шт",
                quantity=qty,
                price=unit_price,
                amount=line_amount,
            )

            total_quantity += qty
            total_amount += line_amount

        transfer.total_quantity = total_quantity
        transfer.total_amount = total_amount
        transfer.save(update_fields=["total_quantity", "total_amount"])

    # Realtime notification
    _send_realtime_transfer_notification(transfer)

    return transfer


def _transfer_expiry_batches(source_product, dest_product, qty_needed: Decimal, transfer: BranchTransfer):
    """
    Перемещение партий со сроком годности по FEFO (сначала с меньшим сроком).
    """
    batches = list(
        ProductExpiryBatch.objects.select_for_update()
        .filter(
            product=source_product,
            status=ProductExpiryBatch.Status.ACTIVE,
            remaining_quantity__gt=Decimal("0"),
        )
        .order_by("expires_at", "received_at")
    )
    if not batches:
        return

    rem = qty_needed
    for b in batches:
        if rem <= Decimal("0"):
            break
        take = min(b.remaining_quantity, rem)
        b.remaining_quantity -= take
        if b.remaining_quantity <= Decimal("0"):
            b.status = ProductExpiryBatch.Status.CONSUMED
        b.save(update_fields=["remaining_quantity", "status"])

        # Создаем соответствующую партию в получателе
        ProductExpiryBatch.objects.create(
            company=transfer.company,
            product=dest_product,
            quantity=take,
            remaining_quantity=take,
            received_at=b.received_at,
            expires_at=b.expires_at,
            status=ProductExpiryBatch.Status.ACTIVE,
            source_kind="branch_transfer",
            source_id=str(transfer.id),
            created_by=transfer.created_by,
        )
        rem -= take


def cancel_branch_transfer(user, transfer_id, reason: str = None) -> BranchTransfer:
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if not company:
        raise ValidationError({"detail": "Компания пользователя не найдена."})

    is_admin = is_user_admin_or_owner(user)
    if not is_admin and not getattr(user, "can_view_branch", False):
        raise PermissionDenied("У вас нет прав для работы с филиалами.")

    with transaction.atomic():
        _pg_advisory_xact_lock_company(company.id)

        try:
            transfer = (
                BranchTransfer.objects.select_for_update()
                .prefetch_related("items")
                .get(id=transfer_id, company=company)
            )
        except BranchTransfer.DoesNotExist:
            raise ValidationError({"detail": "Перемещение не найдено."})

        if transfer.status != BranchTransfer.Status.COMPLETED:
            raise ValidationError({"detail": "Перемещение уже отменено."})

        # Проверяем, что у получателя достаточно остатков для возврата
        items = list(transfer.items.select_related("dest_product", "product").all())
        dest_product_ids = [it.dest_product_id for it in items if it.dest_product_id]
        
        dest_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=dest_product_ids)
        }

        source_product_ids = [it.product_id for it in items if it.product_id]
        source_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=source_product_ids)
        }

        for it in items:
            dest_prod = dest_products.get(it.dest_product_id)
            avail = dest_prod.quantity if dest_prod else Decimal("0")
            if not dest_prod or avail < it.quantity:
                raise ValidationError({
                    "detail": f"Нельзя отменить: товар «{it.name}» уже частично продан/списан на складе-получателе (доступно {avail})."
                })

        # Возвращаем остатки
        for it in items:
            dest_prod = dest_products.get(it.dest_product_id)
            source_prod = source_products.get(it.product_id)

            dest_prod.quantity = (dest_prod.quantity or Decimal("0")) - it.quantity
            dest_prod.save(update_fields=["quantity", "updated_at"])

            if source_prod:
                source_prod.quantity = (source_prod.quantity or Decimal("0")) + it.quantity
                source_prod.save(update_fields=["quantity", "updated_at"])

            # Возврат партий со сроком годности (если были созданы этим перемещением)
            _revert_expiry_batches(source_prod, dest_prod, it.quantity, transfer)

        transfer.status = BranchTransfer.Status.CANCELLED
        transfer.cancelled_at = timezone.now()
        transfer.cancelled_by = user
        transfer.cancel_reason = str(reason or "").strip()
        transfer.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])

    return transfer


def _revert_expiry_batches(source_product, dest_product, qty_to_revert: Decimal, transfer: BranchTransfer):
    """
    Возврат партий при отмене перемещения.
    """
    dest_batches = ProductExpiryBatch.objects.filter(
        product=dest_product,
        source_kind="branch_transfer",
        source_id=str(transfer.id),
    )
    for db in dest_batches:
        # Восстанавливаем партию у источника
        if source_product:
            src_b = ProductExpiryBatch.objects.filter(
                product=source_product,
                expires_at=db.expires_at,
            ).first()
            if src_b:
                src_b.remaining_quantity += db.quantity
                if src_b.status == ProductExpiryBatch.Status.CONSUMED:
                    src_b.status = ProductExpiryBatch.Status.ACTIVE
                src_b.save(update_fields=["remaining_quantity", "status"])
        db.delete()


def _send_realtime_transfer_notification(transfer: BranchTransfer):
    import sys
    if "test" in sys.argv:
        return
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        company_group = f"notif_company_{transfer.company_id}"
        async_to_sync(channel_layer.group_send)(
            company_group,
            {
                "type": "market.notification",
                "event": "market.branch_transfer.created",
                "data": {
                    "transfer_id": str(transfer.id),
                    "number": transfer.number,
                    "from_branch_id": str(transfer.from_branch_id) if transfer.from_branch_id else None,
                    "to_branch_id": str(transfer.to_branch_id) if transfer.to_branch_id else None,
                },
            },
        )
    except Exception:
        pass
