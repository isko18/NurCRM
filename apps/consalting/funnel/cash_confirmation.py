import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

logger = logging.getLogger("nurcrm.consalting.cash_confirmation")


def needs_confirmation(company, payment_method, author):
    """
    Определяет, требуется ли подтверждение кассиром для операции (§9.3).
    """
    from apps.consalting.models import CashConfirmationSettingsConsalting
    settings = getattr(company, "consalting_cash_confirmation", None)
    if not settings:
        settings = CashConfirmationSettingsConsalting.objects.filter(company=company).first()
    if not settings:
        settings = CashConfirmationSettingsConsalting(company=company, mode="cash_only", skip_for_cashier=True)

    if settings.mode == CashConfirmationSettingsConsalting.Mode.OFF:
        return False

    if settings.mode == CashConfirmationSettingsConsalting.Mode.CASH_ONLY and payment_method != "cash":
        return False

    is_cashier_or_mgr = getattr(author, "is_staff", False) or getattr(author, "is_superuser", False)
    if hasattr(author, "role") and author.role in ("owner", "admin", "cashier"):
        is_cashier_or_mgr = True

    if settings.skip_for_cashier and is_cashier_or_mgr:
        return False

    return True


@transaction.atomic
def confirm_request(req, *, user, cashbox_id=None, comment=""):
    """
    Подтверждение заявки кассовой операции (§9.5).
    """
    from apps.consalting.models import (
        CashRequestConsalting, CashOperationConsalting, SaleConsalting, SubscriptionPaymentConsalting
    )

    if req.status != CashRequestConsalting.Status.PENDING:
        raise ValidationError({"detail": "Заявка уже обработана."})

    client_display = req.client.full_name if req.client else req.get_kind_display()

    op = CashOperationConsalting.objects.create(
        company=req.company,
        user=req.user,  # чьи деньги (не того, кто подтвердил)
        confirmed_by=user,
        kind=req.kind,
        direction=req.direction,
        amount=req.amount,
        payment_method=req.payment_method or "cash",
        comment=comment or req.comment or f"Подтверждение: {client_display}",
        cashbox_id=cashbox_id or req.cashbox_id,
    )

    req.status = CashRequestConsalting.Status.CONFIRMED
    req.confirmed_by = user
    req.confirmed_at = timezone.now()
    req.cash_operation = op
    if comment:
        req.comment = comment
    req.save()

    if req.kind == CashRequestConsalting.Kind.SALE and req.sale:
        req.sale.status = SaleConsalting.Status.COMPLETED
        req.sale.save(update_fields=["status"])

    if req.kind == CashRequestConsalting.Kind.SUBSCRIPTION and req.subscription_payment:
        p = req.subscription_payment
        p.status = SubscriptionPaymentConsalting.Status.PAID
        p.paid_at = timezone.now()
        p.save(update_fields=["status", "paid_at"])

    return req, op


@transaction.atomic
def reject_request(req, *, user, reason, comment=""):
    """
    Отклонение заявки кассовой операции (§9.5).
    """
    from apps.consalting.models import CashRequestConsalting

    if req.status != CashRequestConsalting.Status.PENDING:
        raise ValidationError({"detail": "Заявка уже обработана."})

    if not reason:
        raise ValidationError({"reason": "Причина отклонения обязательна."})

    if reason == "other" and not (comment and comment.strip()):
        raise ValidationError({"comment": "При выборе 'Другое' комментарий обязателен."})

    req.status = CashRequestConsalting.Status.REJECTED
    req.confirmed_by = user
    req.confirmed_at = timezone.now()
    req.reject_reason = reason
    req.reject_comment = comment or ""
    req.save()

    return req
