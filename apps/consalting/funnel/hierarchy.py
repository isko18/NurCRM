import logging
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("nurcrm.consalting.hierarchy")


def pick_company_recipient(company):
    """Авто-распределение ответственного для неназначенных лидов по настройкам компании."""
    from apps.consalting.models import LeadDistributionSettingsConsalting
    from apps.users.models import User

    try:
        settings, _ = LeadDistributionSettingsConsalting.objects.get_or_create(company=company)
        if not settings.enabled or settings.strategy == LeadDistributionSettingsConsalting.Strategy.MANUAL:
            return None

        target_roles = list(settings.roles.values_list("id", flat=True))
        if not target_roles:
            return None

        pool = list(User.objects.filter(company=company, is_active=True, custom_role_id__in=target_roles).order_by("id"))
        if not pool:
            return None

        cursor = settings._rr_cursor
        chosen_owner = pool[cursor % len(pool)]
        settings._rr_cursor = cursor + 1
        settings.save(update_fields=["_rr_cursor"])
        return chosen_owner
    except Exception as e:
        logger.warning("pick_company_recipient error: %s", e)
        return None


@transaction.atomic
def move_lead_to_next_funnel(lead, funnel, *, user=None, transition="auto"):
    """
    Автоматический перенос лида в следующую воронку (§3.4).
    Сохраняются клиент, переписка, вложения, сумма и услуга/тариф.
    """
    from apps.consalting.models import FunnelConsalting, LeadConsalting, LeadFunnelHistoryConsalting
    from . import realtime

    target = funnel.next_funnel
    if not target:
        return lead

    stage = funnel.next_stage or target.stages.order_by("order").first()

    # Фиксируем время выхода из предыдущей истории
    LeadFunnelHistoryConsalting.objects.filter(lead=lead, left_at__isnull=True).update(
        left_at=timezone.now()
    )

    # Определение ответственного
    if funnel.next_assign == FunnelConsalting.NextAssign.POOL:
        lead.owner = None
    elif funnel.next_assign == FunnelConsalting.NextAssign.USER:
        lead.owner = funnel.next_assign_user
    elif funnel.next_assign == FunnelConsalting.NextAssign.AUTO:
        lead.owner = pick_company_recipient(lead.company)
    # KEEP — владелец не меняется

    lead.funnel = target
    lead.stage = stage
    lead.status = LeadConsalting.Status.IN_WORK
    lead.stage_entered_at = timezone.now()
    lead.save()

    # Новая запись в истории
    LeadFunnelHistoryConsalting.objects.create(
        lead=lead,
        funnel=target,
        stage=stage,
        owner=lead.owner,
        transition=transition
    )

    if lead.owner:
        try:
            realtime.notify_user(
                lead.owner.id,
                "consulting.lead.moved_to_funnel",
                {
                    "title": f"Лид переведён в воронку: {target.name}",
                    "message": f"Лид «{lead.title}» назначен вам",
                    "lead_id": str(lead.id),
                    "funnel_id": str(target.id)
                }
            )
        except Exception as e:
            logger.warning("Failed to notify user on lead move: %s", e)

    try:
        realtime.lead_moved(lead)
    except Exception as e:
        logger.warning("Failed to broadcast board update: %s", e)

    return lead
