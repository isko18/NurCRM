"""Celery-сканы воронки (Фаза 5): риск бездействия, просроченные задачи, SLA."""
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import (
    LeadConsalting, LeadTaskConsalting, AutomationRuleConsalting,
    FunnelStageConsalting,
)
from .funnel.events import emit
from .funnel.state_machine import ACTIVE_TYPES

CLOSED_STATUSES = (LeadConsalting.Status.WON, LeadConsalting.Status.LOST)


def _active_open_leads():
    return LeadConsalting.objects.filter(
        stage__stage_type__in=ACTIVE_TYPES, closed_at__isnull=True,
    ).exclude(status__in=CLOSED_STATUSES)


@shared_task
def scan_no_activity():
    """Лиды без активности дольше порога → событие no_activity (движок решает действия).

    Грубый floor=24ч отбирает кандидатов дёшево; точный порог каждого правила
    (`conditions.hours`) проверяется в conditions.match внутри движка.
    """
    now = timezone.now()
    fired = 0
    company_ids = list(
        AutomationRuleConsalting.objects.filter(
            is_active=True, trigger=AutomationRuleConsalting.Trigger.NO_ACTIVITY
        ).values_list("company_id", flat=True).distinct()
    )
    if not company_ids:
        return 0

    floor = now - timedelta(hours=24)
    qs = _active_open_leads().filter(company_id__in=company_ids).filter(
        Q(last_activity_at__lt=floor) | Q(last_activity_at__isnull=True, created_at__lt=floor)
    ).iterator()

    for lead in qs:
        emit("no_activity", lead)
        fired += 1
    return fired


@shared_task
def scan_overdue_tasks():
    """Открытые задачи с истёкшим сроком → статус OVERDUE + событие task_overdue."""
    now = timezone.now()
    fired = 0
    overdue = LeadTaskConsalting.objects.filter(
        status=LeadTaskConsalting.Status.OPEN, due_date__lt=now
    ).select_related("lead").iterator()
    for task in overdue:
        LeadTaskConsalting.objects.filter(pk=task.pk).update(
            status=LeadTaskConsalting.Status.OVERDUE
        )
        emit("task_overdue", task.lead, task_id=str(task.id))
        fired += 1
    return fired


@shared_task
def scan_sla_breach():
    """Лиды, превысившие SLA текущей стадии → событие sla_breach."""
    now = timezone.now()
    fired = 0
    stages = {
        s.id: s.sla_hours
        for s in FunnelStageConsalting.objects.filter(sla_hours__isnull=False)
    }
    if not stages:
        return 0
    qs = _active_open_leads().filter(
        stage_id__in=list(stages.keys()), stage_entered_at__isnull=False
    ).iterator()
    for lead in qs:
        sla = stages.get(lead.stage_id)
        if sla and (now - lead.stage_entered_at) >= timedelta(hours=sla):
            emit("sla_breach", lead, sla_hours=sla)
            fired += 1
    return fired
