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
        # Keep this virtual fallback aligned with the model default.  A settings
        # row may not exist yet when a sale is completed before the settings
        # endpoint has been opened.
        settings = CashConfirmationSettingsConsalting(company=company, mode="off", skip_for_cashier=True)

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
        sale=req.sale,
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
        from .completion import create_sale_side_effects, accrue_salary_for_sale
        create_sale_side_effects(req.sale)
        accrue_salary_for_sale(req.sale, seller=req.user)
        if req.sale.deal and req.sale.payment_mode == "installment":
            first_inst = req.sale.deal.installments.order_by("number").first()
            if first_inst and not first_inst.paid_on:
                first_inst.paid_on = timezone.localdate()
                first_inst.paid_amount = first_inst.amount
                first_inst.save(update_fields=["paid_on", "paid_amount"])
        if req.sale.tariff and getattr(req.sale.tariff, "provisions_crm_account", False):
            from .tenant_lifecycle import provision_tenant_account
            try:
                provision_tenant_account(
                    client=req.client or req.sale.client,
                    sale=req.sale,
                    lead=req.sale.lead,
                    tariff=req.sale.tariff,
                    actor=user,
                )
            except Exception as e:
                logger.warning("Tenant account auto-provision failed: %s", e)

    if req.kind == CashRequestConsalting.Kind.SUBSCRIPTION:
        sub = getattr(req, "subscription", None) or (req.subscription_payment.subscription if req.subscription_payment else None)
        count = getattr(req, "prepaid_count", 1) or 1
        if sub:
            unpaid_qs = sub.payments.filter(
                status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
            ).order_by("due_date")
            to_pay = list(unpaid_qs[:count])
            now = timezone.now()
            for p in to_pay:
                p.status = SubscriptionPaymentConsalting.Status.PAID
                p.paid_at = now
                p.payment_method = req.payment_method or "cash"
                p.cashbox_id = cashbox_id or req.cashbox_id
                p.paid_via = p.paid_via or "cash_confirmation"
                p.save(update_fields=["status", "paid_at", "payment_method", "cashbox_id", "paid_via"])
            if to_pay:
                sub.paid_through = to_pay[-1].due_date
                sub.save(update_fields=["paid_through"])
                if sub.client:
                    from .tenant_lifecycle import extend_tenant_subscription
                    extend_tenant_subscription(client=sub.client, subscription_payment=to_pay[-1], actor=user)
        elif req.subscription_payment:
            p = req.subscription_payment
            p.status = SubscriptionPaymentConsalting.Status.PAID
            p.paid_at = timezone.now()
            p.payment_method = req.payment_method or "cash"
            p.cashbox_id = cashbox_id or req.cashbox_id
            p.paid_via = p.paid_via or "cash_confirmation"
            p.save(update_fields=["status", "paid_at", "payment_method", "cashbox_id", "paid_via"])
            if hasattr(p, "subscription") and p.subscription and p.subscription.client:
                from .tenant_lifecycle import extend_tenant_subscription
                extend_tenant_subscription(
                    client=p.subscription.client,
                    subscription_payment=p,
                    actor=user,
                )

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
