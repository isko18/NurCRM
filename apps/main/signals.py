from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import pre_delete
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.main.models import Product, ProductImage

logger = logging.getLogger("crm.webhooks")


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
