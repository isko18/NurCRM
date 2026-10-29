"""Машина состояний воронки: матрица переходов, guard-правила, атомарный переход."""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import (
    LeadConsalting,
    FunnelStageConsalting,
    StageTransitionConsalting,
    LeadActivityConsalting,
)
from .activity import ActivityLogger

T = FunnelStageConsalting.StageType

# Каноничная матрица разрешённых переходов (по stage_type).
TRANSITIONS = {
    T.NEW_LEAD:         {T.FIRST_CONTACT, T.QUALIFICATION, T.LOST},
    T.FIRST_CONTACT:    {T.QUALIFICATION, T.NURTURE, T.LOST},
    T.QUALIFICATION:    {T.NURTURE, T.PROPOSAL_SENT, T.LOST},
    T.NURTURE:          {T.PROPOSAL_SENT, T.QUALIFICATION, T.LOST},
    T.PROPOSAL_SENT:    {T.NEGOTIATION, T.DECISION_PENDING, T.LOST},
    T.NEGOTIATION:      {T.DECISION_PENDING, T.PROPOSAL_SENT, T.WON, T.LOST},
    T.DECISION_PENDING: {T.WON, T.NEGOTIATION, T.LOST},
    T.WON:              {T.ONBOARDING},
    T.ONBOARDING:       {T.COMPLETED},
    T.COMPLETED:        set(),
    T.LOST:             set(),
}

# Активные (не терминальные) стадии — в них обязателен next_action.
ACTIVE_TYPES = set(T.values) - FunnelStageConsalting.TERMINAL_TYPES


class StateTransitionError(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


def _strict():
    return getattr(settings, "CONSALTING_FUNNEL_STRICT", False)


def allowed_next_types(stage):
    """Разрешённые целевые типы: переопределение воронки или каноничная матрица."""
    if stage is None:
        return set(TRANSITIONS.keys())  # лид без стадии может встать в любую начальную
    if stage.allowed_next:
        return {t for t in stage.allowed_next if t in T.values}
    return set(TRANSITIONS.get(stage.stage_type, set()))


class FunnelStateMachine:

    @staticmethod
    def can_transition(lead, target_stage, actor=None):
        """Возвращает (ok: bool, errors: list[str]) без записи."""
        errors = []
        source = lead.stage

        # 1) целостность воронки
        if target_stage.funnel_id != lead.funnel_id:
            errors.append("Стадия относится к другой воронке.")
            return False, errors

        # 2) нельзя назад из закрытых
        if source and source.stage_type in (T.WON, T.COMPLETED):
            if target_stage.stage_type not in allowed_next_types(source):
                errors.append("Нельзя двигать сделку назад из закрытой стадии.")

        # 3) разрешённость перехода / запрет пропуска
        allowed = allowed_next_types(source)
        same = source and source.id == target_stage.id
        if not same and target_stage.stage_type not in allowed:
            if not (source and source.allow_skip):
                src_label = source.get_stage_type_display() if source else "—"
                errors.append(
                    f"Недопустимый переход: {src_label} → {target_stage.get_stage_type_display()}."
                )

        # 4) обязательные поля стадии-источника
        for field in (source.required_fields if source else []):
            if not getattr(lead, field, None):
                errors.append(f"Заполните поле «{field}» перед переходом.")

        # 5) встроенные правила по типу цели
        tt = target_stage.stage_type
        if tt == T.PROPOSAL_SENT and (lead.estimated_value or 0) <= 0:
            errors.append("Перед отправкой КП укажите оценочную стоимость.")
        if tt == T.WON and not lead.budget_confirmed:
            errors.append("Перед закрытием в WON подтвердите бюджет/оплату (budget_confirmed).")
        if tt == T.LOST and not lead.loss_reason_id:
            errors.append("Для проигрыша обязательна причина (loss_reason).")
        if tt in ACTIVE_TYPES and not (lead.next_action_type and lead.next_action_date):
            errors.append("В активной стадии нужны next_action_type и next_action_date.")

        return (len(errors) == 0), errors

    @staticmethod
    def _sync_lifecycle(lead, stage_type):
        now = timezone.now()
        # сброс закрытых полей по умолчанию
        if stage_type in ACTIVE_TYPES:
            lead.status = LeadConsalting.Status.IN_WORK if stage_type != T.NEW_LEAD else LeadConsalting.Status.NEW
            lead.closed_at = None
            lead.won_at = lead.lost_at = lead.completed_at = None
        elif stage_type == T.WON:
            lead.status = LeadConsalting.Status.WON
            lead.won_at = lead.closed_at = now
            lead.lost_at = None
        elif stage_type == T.LOST:
            lead.status = LeadConsalting.Status.LOST
            lead.lost_at = lead.closed_at = now
        elif stage_type == T.COMPLETED:
            lead.status = LeadConsalting.Status.WON
            lead.completed_at = now
            lead.closed_at = lead.closed_at or now
        if stage_type == T.FIRST_CONTACT and not lead.first_contact_at:
            lead.first_contact_at = now

    @classmethod
    @transaction.atomic
    def transition(cls, lead, target_stage, actor=None, automated=False):
        """Атомарный переход лида в стадию. Возвращает обновлённый lead."""
        lead = LeadConsalting.objects.select_for_update().get(pk=lead.pk)

        ok, errors = cls.can_transition(lead, target_stage, actor)
        if not ok and _strict():
            raise StateTransitionError(errors)

        now = timezone.now()
        source = lead.stage
        base = lead.stage_entered_at or lead.created_at
        seconds_in_prev = int((now - base).total_seconds()) if base else None

        StageTransitionConsalting.objects.create(
            company_id=lead.company_id, branch_id=lead.branch_id, lead=lead,
            from_stage=source, to_stage=target_stage,
            from_type=(source.stage_type if source else ""), to_type=target_stage.stage_type,
            actor=actor, automated=automated, seconds_in_prev=seconds_in_prev,
        )

        lead.stage = target_stage
        lead.stage_entered_at = now
        cls._sync_lifecycle(lead, target_stage.stage_type)
        lead.save()

        ActivityLogger.log(
            lead, LeadActivityConsalting.Type.STAGE_CHANGE, actor=actor,
            title=f"Стадия: {source.name if source else '—'} → {target_stage.name}",
            payload={
                "from_stage": str(source.id) if source else None,
                "to_stage": str(target_stage.id),
                "from_type": source.stage_type if source else None,
                "to_type": target_stage.stage_type,
                "automated": automated,
                "soft_violations": [] if ok else errors,  # в мягком режиме фиксируем нарушения
            },
            touch_last_activity=False,
        )

        # Сигнал и саму завершающую логику
        try:
            from .events import emit
            from .hierarchy import move_lead_to_next_funnel
            from .completion import apply_completion_side_effects

            emit("stage_changed", lead, actor=actor, automated=automated,
                 to_type=target_stage.stage_type)
            if target_stage.stage_type == T.WON:
                emit("lead_won", lead, actor=actor)
                if lead.funnel and not lead.funnel.is_final:
                    move_lead_to_next_funnel(lead, lead.funnel, user=actor, transition="auto")
                else:
                    apply_completion_side_effects(lead, actor=actor)
            elif target_stage.stage_type == T.LOST:
                emit("lead_lost", lead, actor=actor)
        except Exception as e:
            logger.warning("Event/hierarchy trigger failed: %s", e)

        return lead
