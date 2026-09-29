from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import pre_delete
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.main.models import Product, ProductImage, Notification, ManufactureSubreal, ReturnFromAgent
from apps.main.models import Debt, DebtPayment

logger = logging.getLogger("crm.webhooks")


@receiver(pre_delete, sender=Product)
def product_deletion_audit(sender, instance: Product, **kwargs):
    """
    ДИАГНОСТИКА «товары исчезают»: логируем КАЖДОЕ удаление Product вместе с
    цепочкой вызовов. По стеку сразу видно, ЧТО удалило товар — каскад от
    Branch/Company (on_delete=CASCADE), management-команда, вьюха API или
    прямой ORM/SQL. Пишем в тот же logger, что и вебхуки (гарантированно
    попадает в логи). Никогда не бросает исключение.

    После следующего инцидента: grep 'PRODUCT_DELETE_AUDIT' по логам —
    в стеке будет источник (напр. cascade из users/models Branch.delete,
    либо конкретная management-команда/скрипт).
    """
    try:
        import traceback

        logger.warning(
            "PRODUCT_DELETE_AUDIT id=%s code=%s company=%s branch=%s\nCALL_STACK:\n%s",
            getattr(instance, "id", None),
            getattr(instance, "code", None),
            getattr(instance, "company_id", None),
            getattr(instance, "branch_id", None),
            "".join(traceback.format_stack(limit=20)),
        )
    except Exception:
        pass


@receiver(post_save, sender=Product)
def product_webhook_on_save(sender, instance: Product, created: bool, **kwargs):
    event = "product.created" if created else "product.updated"

    def _send():
        try:
            from apps.main.services.webhooks import send_product_webhook

            send_product_webhook(instance, event)
        except Exception:
            logger.error(
                "Unexpected error while preparing/sending product webhook. product_id=%s event=%s",
                getattr(instance, "id", None),
                event,
                exc_info=True,
            )

    try:
        transaction.on_commit(_send)
    except Exception:
        _send()


@receiver(post_save, sender=ProductImage)
def product_webhook_on_image_save(sender, instance: ProductImage, created: bool, **kwargs):
    """
    Product images are usually created/updated separately from Product,
    so Product.post_save won't fire for those changes.
    """
    event = "product.updated"
    product_id = getattr(instance, "product_id", None)
    if not product_id:
        return

    def _send():
        try:
            from apps.main.services.webhooks import send_product_webhook

            send_product_webhook(instance.product, event)
        except Exception:
            logger.error(
                "Unexpected error while sending product webhook after image save. product_id=%s",
                product_id,
                exc_info=True,
            )

    try:
        transaction.on_commit(_send)
    except Exception:
        _send()


@receiver(pre_delete, sender=ProductImage)
def product_webhook_on_image_delete(sender, instance: ProductImage, **kwargs):
    event = "product.updated"
    product_id = getattr(instance, "product_id", None)
    if not product_id:
        return

    def _send():
        try:
            from apps.main.models import Product as ProductModel
            from apps.main.services.webhooks import send_product_webhook

            product = ProductModel.objects.filter(pk=product_id).first()
            if product:
                send_product_webhook(product, event)
        except Exception:
            logger.error(
                "Unexpected error while sending product webhook after image delete. product_id=%s",
                product_id,
                exc_info=True,
            )

    try:
        transaction.on_commit(_send)
    except Exception:
        _send()


@receiver(pre_delete, sender=Product)
def product_webhook_on_delete(sender, instance: Product, **kwargs):
    """
    Send delete event with the same product JSON as in list endpoint.
    We serialize BEFORE deletion and send AFTER commit.
    """
    event = "product.deleted"

    try:
        from apps.main.serializers import ProductSerializer

        data = ProductSerializer(instance, context={"request": None}).data
    except Exception:
        logger.error(
            "Failed to serialize product for delete webhook. product_id=%s",
            getattr(instance, "id", None),
            exc_info=True,
        )
        return

    def _send():
        try:
            from apps.main.services.webhooks import send_product_webhook_data

            send_product_webhook_data(data, event)
        except Exception:
            logger.error(
                "Unexpected error while sending product delete webhook. product_id=%s",
                getattr(instance, "id", None),
                exc_info=True,
            )

    try:
        transaction.on_commit(_send)
    except Exception:
        _send()


# ─────────────────────────────────────────────────────────────
# Уведомления агенту-пользователю (web-версия агента, колокольчик).
# Канал доставки — существующий GET /main/notifications/.
# ─────────────────────────────────────────────────────────────
def _safe_create_notification(*, company, user, message, branch=None,
                              type="system", title="", level="info", url="",
                              category="system", data=None):
    """Создаёт уведомление и публикует его в WS; не роняет основную операцию при ошибке."""
    if not user:
        return
    try:
        from apps.main.notifications_market import publish_event

        publish_event(
            company=company, branch=branch, recipients=[user], event_type=type,
            title=title, message=message, level=level, url=url,
            category=category, meta=data or {},
        )
    except Exception:
        logger.error(
            "Failed to create notification for user=%s",
            getattr(user, "id", None),
            exc_info=True,
        )


@receiver(post_save, sender=ManufactureSubreal)
def notify_agent_on_transfer(sender, instance: ManufactureSubreal, created, **kwargs):
    """Назначена передача (subreal) → уведомление агенту-получателю."""
    if not created or not instance.agent_id:
        return
    product_name = getattr(getattr(instance, "product", None), "name", None) or "товар"
    message = f"Вам передан товар: {product_name}, {instance.qty_transferred} шт"
    transaction.on_commit(lambda: _safe_create_notification(
        company=instance.company,
        branch=instance.branch,
        user=instance.agent,
        message=message,
        type="market.transfer.received",
        title="Вам передана новая партия товара",
        level="info",
        url="/crm/pending",
        data={
            "source_kind": "subreal_transfer",
            "source_id": str(instance.id),
            "product_id": str(instance.product_id) if instance.product_id else None,
            "qty": str(instance.qty_transferred),
            "cta_label": "Принять",
        },
    ))


@receiver(post_save, sender=ReturnFromAgent)
def notify_agent_on_return_decision(sender, instance: ReturnFromAgent, created, **kwargs):
    """Возврат/брак одобрен или отклонён → уведомление агенту-инициатору."""
    if created:
        return
    update_fields = kwargs.get("update_fields")
    # accept()/reject() сохраняют с update_fields={"status", ...} — реагируем только на смену статуса.
    if update_fields is not None and "status" not in update_fields:
        return
    if instance.status not in (ReturnFromAgent.Status.ACCEPTED, ReturnFromAgent.Status.REJECTED):
        return

    product = getattr(getattr(instance, "subreal", None), "product", None)
    product_name = getattr(product, "name", None) or "товар"
    kind = "Брак" if instance.is_defect else "Возврат"
    verb = "принят" if instance.status == ReturnFromAgent.Status.ACCEPTED else "отклонён"
    message = f"{kind} «{product_name}» {verb}"
    transaction.on_commit(lambda: _safe_create_notification(
        company=instance.company,
        branch=instance.branch,
        user=instance.returned_by,
        message=message,
        type="market.product.status_changed",
        title=f"{kind} {verb}",
        level="success" if instance.status == ReturnFromAgent.Status.ACCEPTED else "warning",
        url="/crm/documents",
        data={
            "source_kind": "return_request",
            "source_id": str(instance.id),
            "status": "approved" if instance.status == ReturnFromAgent.Status.ACCEPTED else "rejected",
            "is_defect": bool(instance.is_defect),
        },
    ))


# ─────────────────────────────────────────────────────────────
# Долги клиентов: market.debt.created / market.debt.paid
# Кому: владельцы и администраторы компании.
# ─────────────────────────────────────────────────────────────
@receiver(post_save, sender=Debt)
def notify_debt_created(sender, instance: Debt, created: bool, **kwargs):
    if not created:
        return

    def _publish():
        from apps.main.notifications_market import (
            DEBT_CREATED, fmt_money, owner_like_users, publish_event,
        )

        recipients = owner_like_users(instance.company)
        if not recipients:
            return
        publish_event(
            company=instance.company,
            branch=instance.branch,
            recipients=recipients,
            event_type=DEBT_CREATED,
            title="Новый долг",
            message=f"{instance.name}: {fmt_money(instance.amount)} сом",
            level="info",
            url="/crm/debts",
            cta_label="Открыть долги",
            source_kind="debt",
            source_id=instance.id,
            meta={
                "debt_id": str(instance.id),
                "amount": f"{instance.amount:.2f}",
                "client_name": instance.name,
                "phone": instance.phone,
            },
        )

    transaction.on_commit(_publish)


@receiver(post_save, sender=DebtPayment)
def notify_debt_paid(sender, instance: DebtPayment, created: bool, **kwargs):
    if not created:
        return

    def _publish():
        from apps.main.notifications_market import (
            DEBT_PAID, fmt_money, owner_like_users, publish_event,
        )

        debt = instance.debt
        recipients = owner_like_users(instance.company)
        if not recipients:
            return
        balance = debt.balance
        tail = "долг закрыт" if balance <= 0 else f"остаток {fmt_money(balance)} сом"
        publish_event(
            company=instance.company,
            branch=instance.branch,
            recipients=recipients,
            event_type=DEBT_PAID,
            title="Погашение долга",
            message=f"{debt.name}: +{fmt_money(instance.amount)} сом, {tail}",
            level="success",
            url="/crm/debts",
            cta_label="Открыть долги",
            source_kind="debt_payment",
            source_id=instance.id,
            meta={
                "debt_id": str(debt.id),
                "payment_id": str(instance.id),
                "amount": f"{instance.amount:.2f}",
                "balance": f"{balance:.2f}",
                "client_name": debt.name,
            },
        )

    transaction.on_commit(_publish)


# --- BE2-19: быстрая синхронизация каталога кассы ---

from django.db.models.signals import post_delete  # noqa: E402

from apps.main.models import ProductDeletion, ProductPromotionTier  # noqa: E402


@receiver(post_delete, sender=Product)
def product_deletion_tombstone(sender, instance: Product, **kwargs):
    """Удалённый товар попадает в deleted ответа products/list/?updated_since=."""
    try:
        if instance.company_id:
            with transaction.atomic():
                ProductDeletion.objects.create(company_id=instance.company_id, product_id=instance.pk)
    except Exception:
        # Каскадное удаление компании: писать уже некуда — это не должно мешать удалению.
        logger.warning("product tombstone failed for %s", instance.pk, exc_info=True)


@receiver(post_save, sender=ProductPromotionTier)
@receiver(post_delete, sender=ProductPromotionTier)
def promotion_tier_touches_product(sender, instance: ProductPromotionTier, **kwargs):
    """Изменили акцию — товар должен прийти кассе при следующей быстрой синхронизации."""
    Product.objects.filter(pk=instance.product_id).update()


# --- 09: статус продажи в долг следует за погашением её сделки ---

from apps.main.models import DealPayment, sync_sale_status_from_deal  # noqa: E402


@receiver(post_save, sender=DealPayment)
@receiver(post_delete, sender=DealPayment)
def deal_payment_syncs_sale_status(sender, instance: DealPayment, **kwargs):
    """После оплаты или возврата платежа по сделке — пересчитать статус связанной продажи."""
    deal_id = instance.deal_id
    transaction.on_commit(lambda: sync_sale_status_from_deal(deal_id))
