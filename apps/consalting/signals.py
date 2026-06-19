"""Подписка на события воронки → движок автоматизации + real-time (Фаза 6).

Дополнительно: автоматическое создание воронки роли при появлении кастомной роли
в компании сектора «Консалтинг».
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import CustomRole

from .funnel.events import funnel_event
from .funnel.automation.engine import AutomationEngine
from .funnel import realtime
from .funnel.provisioning import provision_funnel_for_role

logger = logging.getLogger(__name__)

# события, по которым шлём live-уведомление в поток менеджера
_REALTIME_TRIGGERS = {"stage_changed", "lead_won", "lead_lost", "no_activity", "sla_breach"}


@receiver(funnel_event)
def on_funnel_event(sender, trigger, lead, actor=None, ctx=None, **kwargs):
    ctx = ctx or {}
    try:
        AutomationEngine.run(trigger, lead, actor=actor, ctx=ctx)
    except Exception as e:  # автоматизация не должна ломать вызывающий код
        logger.exception("automation dispatch failed for %s: %s", trigger, e)

    if trigger in _REALTIME_TRIGGERS:
        realtime.push(lead, trigger)


@receiver(post_save, sender=CustomRole)
def on_custom_role_created(sender, instance, created, **kwargs):
    """При создании кастомной роли в консалтинговой компании — создаём воронку роли."""
    if not created:
        return
    company = instance.company
    if not company or not getattr(company, "is_consulting", None) or not company.is_consulting():
        return

    def _provision():
        try:
            provision_funnel_for_role(instance)
        except Exception as e:  # provisioning не должен ломать создание роли
            logger.exception("provision_funnel_for_role failed for role %s: %s", instance.id, e)

    transaction.on_commit(_provision)
