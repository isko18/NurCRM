"""Сопоставление условий правила с событием/лидом (JSON-предикат)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone


def _as_list(v):
    return v if isinstance(v, (list, tuple, set)) else [v]


def match(conditions, lead, ctx):
    """
    conditions — dict из AutomationRuleConsalting.conditions.
    Поддерживаемые ключи (все опциональны, объединяются по И):
      to_type        — тип целевой стадии (из ctx перехода)
      stage_type     — текущий тип стадии лида
      score_grade    — грейд лида (строка или список)
      status         — статус лида (строка или список)
      min_value      — estimated_value >= X
      source         — источник лида
      urgency        — срочность
    Технические ключи скана (hours и т.п.) игнорируются как фильтр.
    """
    ctx = ctx or {}

    if "to_type" in conditions:
        if ctx.get("to_type") not in _as_list(conditions["to_type"]):
            return False

    if "stage_type" in conditions:
        cur = lead.stage.stage_type if lead.stage_id else None
        if cur not in _as_list(conditions["stage_type"]):
            return False

    if "score_grade" in conditions:
        if lead.score_grade not in _as_list(conditions["score_grade"]):
            return False

    if "status" in conditions:
        if lead.status not in _as_list(conditions["status"]):
            return False

    if "source" in conditions:
        if (lead.source or "") not in _as_list(conditions["source"]):
            return False

    if "urgency" in conditions:
        if lead.urgency not in _as_list(conditions["urgency"]):
            return False

    if "min_value" in conditions:
        try:
            if (lead.estimated_value or Decimal("0")) < Decimal(str(conditions["min_value"])):
                return False
        except (TypeError, ValueError):
            return False

    # порог бездействия (для no_activity-правил): точная проверка возраста
    if "hours" in conditions:
        base = lead.last_activity_at or lead.created_at
        if base and (timezone.now() - base) < timedelta(hours=int(conditions["hours"])):
            return False

    return True
