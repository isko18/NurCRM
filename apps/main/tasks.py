from __future__ import annotations

import logging
import time

from celery import shared_task
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import localtime

from apps.main.models import Notification, Task

logger = logging.getLogger("crm.webhooks")


@shared_task
def create_task_notification(task_id):
    try:
        task = Task.objects.select_related('company', 'assigned_to').get(id=task_id)

        if not task.assigned_to:
            return

        message = (
            f"Вам назначена новая задача: «{task.title}», "
            f"срок — {localtime(task.due_date).strftime('%d.%m.%Y %H:%M')}"
        )

        from apps.main.realtime import create_and_publish_notification

        create_and_publish_notification(
            company=task.company,
            user=task.assigned_to,
            message=message,
            type="task_created",
            title="Новая задача",
            level="info",
        )

    except Task.DoesNotExist:
        print(f"[ERROR] Task with ID {task_id} does not exist!")
    except Exception as e:
        print(f"[ERROR] Unexpected error while creating task notification: {e}")


@receiver(post_save, sender=Task)
def notify_assigned_user_async(sender, instance, created, **kwargs):
    if created and instance.assigned_to:
        transaction.on_commit(lambda: create_task_notification.delay(str(instance.id)))


@shared_task(name="apps.main.tasks.catalog_webhook_sync")
def catalog_webhook_sync():
    """
    Periodic full-catalog webhook sync.

    Iterates every product and re-sends it to the external catalog system
    so that missed signals (network failures, deploys, etc.) can't cause
    the remote catalog to fall out of sync.

    Products that have no images are logged as warnings so the team knows
    which items still need photos uploaded.
    """
    from apps.main.models import Product
    from apps.main.services.webhooks import send_product_webhook

    qs = (
        Product.objects
        .select_related(
            "company", "branch", "brand", "category",
            "client", "created_by", "characteristics",
        )
        .prefetch_related("images", "packages", "item_make")
        .order_by("created_at")
    )

    total = 0
    no_image_codes: list[str] = []
    started = time.time()

    for product in qs.iterator(chunk_size=200):
        send_product_webhook(product, "product.updated", retries=3, timeout=15, backoff=1.5)
        total += 1

        # Use prefetch cache — do NOT call .exists() here (bypasses cache → N+1)
        if not list(product.images.all()):
            no_image_codes.append(str(getattr(product, "code", None) or product.id))

    elapsed = time.time() - started

    logger.info(
        "catalog_webhook_sync: sent=%d no_images=%d elapsed=%.1fs",
        total,
        len(no_image_codes),
        elapsed,
    )

    if no_image_codes:
        # Log in chunks of 50 so the line doesn't become gigantic
        for i in range(0, len(no_image_codes), 50):
            chunk = no_image_codes[i: i + 50]
            logger.warning(
                "catalog_webhook_sync: products without images [%d/%d]: %s",
                i + len(chunk),
                len(no_image_codes),
                ", ".join(chunk),
            )

    return {"total": total, "no_images_count": len(no_image_codes)}


@shared_task(name="apps.main.tasks.product_expiry_digest")
def product_expiry_digest():
    """
    Daily digest for products that are expired or expiring soon (within 14 days).
    Sends aggregated notification to company owner / WS groups.
    """
    from apps.main.services_expiry import send_product_expiry_digest_for_all_companies

    count = send_product_expiry_digest_for_all_companies()
    logger.info("product_expiry_digest finished: created %d notifications", count)
    return {"created_notifications_count": count}
