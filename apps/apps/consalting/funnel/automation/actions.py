"""Обработчики действий автоматизации. Каждый возвращает dict-результат для лога."""
from datetime import timedelta

from django.utils import timezone

from ...models import LeadConsalting, LeadTaskConsalting, LeadActivityConsalting
from ..activity import ActivityLogger
from ..scoring import ScoringService
from .. import realtime


def _create_task(action, lead, actor):
    due = timezone.now() + timedelta(days=int(action.get("due_in_days", 1)))
    task_type = action.get("task_type", LeadConsalting.NextAction.FOLLOW_UP)
    title = action.get("title", "Задача по лиду")
    task = LeadTaskConsalting.objects.create(
        company_id=lead.company_id, branch_id=lead.branch_id, lead=lead,
        assignee=lead.owner, type=task_type, title=title, due_date=due,
        created_by=None, created_by_automation=True,
    )
    # автозадача = следующий шаг
    LeadConsalting.objects.filter(pk=lead.pk).update(
        next_action_type=task_type, next_action_date=due, next_action_note=title
    )
    ActivityLogger.log(
        lead, LeadActivityConsalting.Type.TASK, actor=None,
        title=f"Авто-задача: {title}", payload={"task_id": str(task.id), "automation": True},
        touch_last_activity=False,
    )
    return {"action": "create_task", "task_id": str(task.id)}


def _notify_manager(action, lead, actor):
    text = action.get("text") or f"Лид «{lead.title}» требует внимания"
    realtime.push(lead, "notify", payload={"lead_id": str(lead.id), "message": text})
    ActivityLogger.log(
        lead, LeadActivityConsalting.Type.AUTOMATION, actor=None,
        title=text, payload={"kind": "notify_manager"}, touch_last_activity=False,
    )
    return {"action": "notify_manager"}


def _set_at_risk(action, lead, actor):
    reason = action.get("reason", "Под риском")
    LeadConsalting.objects.filter(pk=lead.pk).update(is_at_risk=True, risk_reason=reason)
    lead.is_at_risk = True
    lead.risk_reason = reason
    ActivityLogger.log(
        lead, LeadActivityConsalting.Type.AUTOMATION, actor=None,
        title=f"Помечен под риском: {reason}", payload={"kind": "set_at_risk"},
        touch_last_activity=False,
    )
    return {"action": "set_at_risk"}


def _recalculate_score(action, lead, actor):
    value, grade, changed = ScoringService.recalculate(lead, save=True)
    return {"action": "recalculate_score", "value": value, "grade": grade}


def _start_pipeline(action, lead, actor):
    # Полноценный онбординг-pipeline — отдельная фича; здесь фиксируем старт + задача.
    ActivityLogger.log(
        lead, LeadActivityConsalting.Type.AUTOMATION, actor=None,
        title="Запущен онбординг", payload={"kind": "start_pipeline"},
        touch_last_activity=False,
    )
    return {"action": "start_pipeline"}


HANDLERS = {
    "create_task": _create_task,
    "notify_manager": _notify_manager,
    "set_at_risk": _set_at_risk,
    "recalculate_score": _recalculate_score,
    "start_pipeline": _start_pipeline,
}


def run(action, lead, actor=None):
    handler = HANDLERS.get(action.get("type"))
    if not handler:
        return {"action": action.get("type"), "skipped": "unknown_action"}
    return handler(action, lead, actor)
