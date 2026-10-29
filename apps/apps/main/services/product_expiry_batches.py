from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.main.models import ProductExpiryBatch


def receive_stock(product, quantity, *, source_kind, source_id=None, user=None, received_at=None):
    """Create an immutable expiry batch when a perishable product is received."""
    quantity = Decimal(str(quantity or 0))
    if quantity <= 0 or product.shelf_life_days is None:
        return None
    received_at = received_at or timezone.now()
    return ProductExpiryBatch.objects.create(
        company=product.company, product=product, quantity=quantity,
        remaining_quantity=quantity, received_at=received_at,
        expires_at=received_at.date() + timedelta(days=product.shelf_life_days),
        source_kind=source_kind, source_id=str(source_id) if source_id else None,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


@transaction.atomic
def consume_stock(product, quantity, *, source_kind, source_id=None):
    """Best-effort FEFO consumption. Product.quantity remains the stock source of truth."""
    left = Decimal(str(quantity or 0))
    if left <= 0:
        return
    batches = ProductExpiryBatch.objects.select_for_update().filter(
        product=product, status=ProductExpiryBatch.Status.ACTIVE, remaining_quantity__gt=0
    ).order_by(F("expires_at").asc(nulls_last=True), "received_at")
    for batch in batches:
        taken = min(left, batch.remaining_quantity)
        batch.remaining_quantity -= taken
        left -= taken
        fields = ["remaining_quantity"]
        if batch.remaining_quantity == 0:
            batch.status = ProductExpiryBatch.Status.CONSUMED
            fields.append("status")
        batch.save(update_fields=fields)
        if left <= 0:
            break


def expire_due_batches(today=None):
    today = today or timezone.localdate()
    return ProductExpiryBatch.objects.filter(
        status=ProductExpiryBatch.Status.ACTIVE, remaining_quantity__gt=0, expires_at__lt=today
    ).update(status=ProductExpiryBatch.Status.EXPIRED)
