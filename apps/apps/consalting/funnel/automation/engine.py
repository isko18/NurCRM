"""Исполнение правил автоматизации для события воронки."""
import logging

from django.db.models import Q
from django.utils import timezone

from ...models import AutomationRuleConsalting, AutomationLogConsalting
from . import conditions, actions

logger = logging.getLogger(__name__)

# Триггеры, для которых нужна дедупликация (периодические сканы), и её гранулярность.
_DEDUP_DAILY = {
    AutomationRuleConsalting.Trigger.NO_ACTIVITY,
    AutomationRuleConsalting.Trigger.SLA_BREACH,
}


def _dedup_key(rule, lead, trigger, ctx):
    if trigger in _DEDUP_DAILY:
        return f"{rule.id}:{lead.id}:{trigger}:{timezone.now().date().isoformat()}"
    if trigger == AutomationRuleConsalting.Trigger.TASK_OVERDUE and ctx.get("task_id"):
        return f"{rule.id}:{lead.id}:{trigger}:{ctx['task_id']}"
    return ""  # событийные триггеры не дедуплицируем


class AutomationEngine:

    @staticmethod
    def run(trigger, lead, actor=None, ctx=None):
        ctx = ctx or {}
        rules = (
            AutomationRuleConsalting.objects
            .filter(company_id=lead.company_id, is_active=True, trigger=trigger)
            .filter(Q(funnel__isnull=True) | Q(funnel_id=lead.funnel_id))
            .order_by("priority", "name")
        )
        for rule in rules:
            try:
                if not conditions.match(rule.conditions or {}, lead, ctx):
                    continue

                dedup = _dedup_key(rule, lead, trigger, ctx)
                if dedup and AutomationLogConsalting.objects.filter(dedup_key=dedup).exists():
                    continue

                results = [actions.run(a, lead, actor) for a in (rule.actions or [])]
                AutomationLogConsalting.objects.create(
                    company_id=lead.company_id, rule=rule, lead=lead,
                    trigger=trigger, matched=True, actions_result=results, dedup_key=dedup,
                )
            except Exception as e:  # одно правило не должно ронять остальные
                logger.exception("automation rule %s failed: %s", rule.id, e)
