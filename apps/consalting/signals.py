"""Подписка на события воронки → движок автоматизации + real-time (Фаза 6)."""
import logging

from django.dispatch import receiver

from .funnel.events import funnel_event
from .funnel.automation.engine import AutomationEngine
from .funnel import realtime

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
