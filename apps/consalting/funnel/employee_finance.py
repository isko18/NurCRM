import logging
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum, Q
from django.utils import timezone

logger = logging.getLogger("nurcrm.consalting.employee_finance")


def calculate_employee_finance(user, dt_start, dt_end):
    """
    Финансовая сводка сотрудника (§7.3).
    `on_hands` считается нарастающим итогом за всё время!
    """
    from apps.consalting.models import (
        SaleConsalting, CashOperationConsalting, CashRequestConsalting
    )
    from apps.main.models import DealInstallment

    # Продажи за период
    sales = SaleConsalting.objects.filter(user=user, created_at__range=(dt_start, dt_end))
    completed = sales.exclude(description__icontains="отмена").exclude(description__icontains="отменён")

    sold_agg = completed.aggregate(s=Sum("total"))["s"] or Decimal("0")
    sold = float(sold_agg)

    cash_recv_agg = completed.filter(
        Q(lead__payment_mode="cash") | Q(description__icontains="наличные")
    ).aggregate(s=Sum("total"))["s"] or Decimal("0")
    cash_received = float(cash_recv_agg)

    if cash_received == 0 and completed.exists():
        cash_received = float(completed.exclude(description__icontains="перевод").aggregate(s=Sum("total"))["s"] or Decimal("0"))

    transfer_received = sold - cash_received

    handed_agg = CashOperationConsalting.objects.filter(
        user=user, kind="handover", created_at__range=(dt_start, dt_end)
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    handed_over = float(handed_agg)

    pending_agg = CashRequestConsalting.objects.filter(
        user=user, kind="handover", status="pending"
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    pending_handover = float(pending_agg)

    # Cumulative on_hands calculation (all time)
    all_completed = SaleConsalting.objects.filter(user=user).exclude(description__icontains="отмена").exclude(description__icontains="отменён")
    all_cash_recv = float(all_completed.filter(
        Q(lead__payment_mode="cash") | Q(description__icontains="наличные")
    ).aggregate(s=Sum("total"))["s"] or Decimal("0"))
    if all_cash_recv == 0 and all_completed.exists():
        all_cash_recv = float(all_completed.exclude(description__icontains="перевод").aggregate(s=Sum("total"))["s"] or Decimal("0"))

    all_handed = float(CashOperationConsalting.objects.filter(user=user, kind="handover").aggregate(s=Sum("amount"))["s"] or Decimal("0"))

    on_hands = max(0.0, round(all_cash_recv - all_handed - pending_handover, 2))

    # Overdue amount (held longer than 24h)
    cutoff = timezone.now() - timedelta(hours=24)
    old_cash_agg = SaleConsalting.objects.filter(
        user=user, created_at__lt=cutoff
    ).exclude(description__icontains="отмена").exclude(description__icontains="отменён").aggregate(s=Sum("total"))["s"] or Decimal("0")
    overdue_amount = min(on_hands, float(old_cash_agg))

    # Client debts summary
    installments = DealInstallment.objects.filter(deal__client__company=user.company)
    total_debt = 0.0
    overdue_debt = 0.0
    today = timezone.localdate()
    for inst in installments:
        rem = float(inst.amount - (inst.paid_amount or Decimal("0")))
        if rem > 0:
            total_debt += rem
            if inst.due_date < today:
                overdue_debt += rem

    return {
        "sold": sold,
        "cash_received": cash_received,
        "transfer_received": transfer_received,
        "handed_over": handed_over,
        "on_hands": on_hands,
        "pending_handover": pending_handover,
        "overdue_amount": round(overdue_amount, 2),
        "debts": {"total": round(total_debt, 2), "overdue": round(overdue_debt, 2)}
    }
