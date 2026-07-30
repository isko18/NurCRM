import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

logger = logging.getLogger("nurcrm.consalting.sale_cancel")


@transaction.atomic
def cancel_sale(sale, *, user, reason, comment, refund_mode, lead_action=None, partial_amount=None):
    from apps.consalting.models import (
        SaleConsalting, SubscriptionConsalting, SubscriptionPaymentConsalting,
        SalaryAccrualConsalting, SalaryAdjustmentConsalting, CashRequestConsalting,
        SaleRefundConsalting, LeadConsalting
    )
    from apps.main.models import DealInstallment

    if sale.status == SaleConsalting.Status.CANCELED:
        raise ValidationError({"detail": "Продажа уже отменена."})

    partial = partial_amount is not None
    if partial:
        p_amount = Decimal(str(partial_amount))
        if p_amount <= Decimal("0") or p_amount > (sale.total - (sale.refunded_amount or Decimal("0"))):
            raise ValidationError({"detail": "Сумма возврата больше остатка по продаже."})
        ratio = p_amount / sale.total if sale.total > 0 else Decimal("1")
    else:
        p_amount = sale.total
        ratio = Decimal("1")

    # 1. АБОНЕНТКА: будущие неоплаченные платежи аннулируем
    if not partial:
        for sub in SubscriptionConsalting.objects.filter(sale=sale):
            sub.payments.filter(status__in=[
                SubscriptionPaymentConsalting.Status.PLANNED,
                SubscriptionPaymentConsalting.Status.OVERDUE
            ]).update(status=SubscriptionPaymentConsalting.Status.CANCELED)
            sub.status = SubscriptionConsalting.Status.CANCELED
            sub.canceled_at = timezone.now()
            sub.save(update_fields=["status", "canceled_at"])

    # 2. ДОЛГ / РАССРОЧКА: неоплаченные строки графика аннулируем
    if not partial and sale.subscription_deal_id:
        DealInstallment.objects.filter(deal_id=sale.subscription_deal_id, paid_on__isnull=True).delete()

    # 3. ЗАРПЛАТА: отменяем начисление; если уже выплачено — создаём удержание
    for accrual in SalaryAccrualConsalting.objects.filter(sale=sale).exclude(status="canceled"):
        if accrual.status == "paid":
            deduct_amt = (accrual.amount * ratio).quantize(Decimal("0.01"))
            SalaryAdjustmentConsalting.objects.create(
                company=sale.company,
                user=accrual.user,
                kind=SalaryAdjustmentConsalting.Kind.DEDUCTION,
                amount=deduct_amt,
                reason=SalaryAdjustmentConsalting.Reason.SALE_CANCELED,
                comment=f"Отмена продажи №{sale.id}",
                date=timezone.localdate(),
                status="active",
                source_sale=sale,
            )
        elif partial:
            accrual.amount = (accrual.amount * (Decimal("1") - ratio)).quantize(Decimal("0.01"))
            accrual.base_amount = (accrual.base_amount * (Decimal("1") - ratio)).quantize(Decimal("0.01"))
            accrual.save(update_fields=["amount", "base_amount"])
        else:
            accrual.status = "canceled"
            accrual.save(update_fields=["status"])

    # 4. КАССА
    pending = CashRequestConsalting.objects.filter(sale=sale, status="pending").first()
    if pending and not partial:
        pending.status = CashRequestConsalting.Status.REJECTED
        pending.save(update_fields=["status"])
    elif refund_mode in ("cash", "transfer"):
        CashRequestConsalting.objects.create(
            company=sale.company,
            user=user,
            kind="refund",
            direction="outcome",
            amount=p_amount,
            status=CashRequestConsalting.Status.PENDING,
            comment=f"Возврат по продаже: {comment or reason}",
        )

    # 5. ЛИД
    if sale.lead and not partial:
        lead = sale.lead
        if lead_action == "return_to_work":
            lead.status = LeadConsalting.Status.IN_WORK
            lead.won_at = None
            lead.converted_at = None
        elif lead_action == "reject":
            lead.status = LeadConsalting.Status.LOST
            lead.lost_reason = "Продажа отменена"
            lead.closed_at = timezone.now()
        lead.save()

    # 6. САМА ПРОДАЖА
    if partial:
        sale.refunded_amount = (sale.refunded_amount or Decimal("0")) + p_amount
        sale.status = SaleConsalting.Status.REFUNDED
        SaleRefundConsalting.objects.create(
            company=sale.company,
            sale=sale,
            amount=p_amount,
            reason=reason,
            comment=comment,
            refund_mode=refund_mode,
            created_by=user,
        )
    else:
        sale.status = SaleConsalting.Status.CANCELED
        sale.canceled_at = timezone.now()
        sale.canceled_by = user
        sale.cancel_reason = reason
        sale.cancel_comment = comment
    sale.save()

    return sale
