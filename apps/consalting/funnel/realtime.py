"""Real-time события канбана consalting через Channels (best-effort).

Все «карточные» события доски шлём ТОЛЬКО в группу компании
(``consalting_company_<company_id>``). Каждый подключённый сотрудник состоит в
этой группе, а персональную видимость («взятый лид виден только владельцу +
руководителям») применяет уже консьюмер при доставке (см. consumers.py).
Это избавляет от дублей, которые были бы при отправке и в company-, и в
user-группу одновременно.

Персональные уведомления (например, «лид назначен вам») шлём отдельным типом
в ``consalting_user_<user_id>`` — они не дублируют карточные события.
"""
import logging

logger = logging.getLogger("nurcrm.websocket.consalting")

# тип-метод консьюмера для карточных событий доски (с фильтром видимости)
_BOARD_HANDLER = "consalting.event"
# тип-метод консьюмера для персональных уведомлений (без фильтра)
_NOTIFY_HANDLER = "consalting.notify"


def company_group(company_id) -> str:
    return f"consalting_company_{company_id}"


def user_group(user_id) -> str:
    return f"consalting_user_{user_id}"


def serialize_lead(lead) -> dict:
    """Лёгкая сериализация лида для карточки канбана (без request-контекста)."""
    owner_display = None
    if lead.owner_id:
        try:
            owner = lead.owner
            if owner:
                full = f"{owner.first_name or ''} {owner.last_name or ''}".strip()
                owner_display = full or owner.email
        except Exception:
            owner_display = None

    return {
        "id": str(lead.id),
        "company": str(lead.company_id) if lead.company_id else None,
        "branch": str(lead.branch_id) if lead.branch_id else None,
        "funnel": str(lead.funnel_id) if lead.funnel_id else None,
        "stage": str(lead.stage_id) if lead.stage_id else None,
        "owner": str(lead.owner_id) if lead.owner_id else None,
        "owner_display": owner_display,
        "source_lead": str(lead.source_lead_id) if lead.source_lead_id else None,
        "title": lead.title,
        "status": lead.status,
        "score_grade": lead.score_grade,
        "score_value": lead.score_value,
        "estimated_value": str(lead.estimated_value),
        "is_at_risk": lead.is_at_risk,
        "next_action_type": lead.next_action_type,
        "next_action_date": lead.next_action_date.isoformat() if lead.next_action_date else None,
        "created_at": lead.created_at.isoformat() if getattr(lead, "created_at", None) else None,
        "updated_at": lead.updated_at.isoformat() if getattr(lead, "updated_at", None) else None,
    }


def _send(groups, event_type, payload, handler):
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        layer = get_channel_layer()
        if not layer:
            return

        body = {"type": handler, "event": event_type, "payload": payload}
        seen = set()
        for g in groups:
            if not g or g in seen:
                continue
            seen.add(g)
            async_to_sync(layer.group_send)(g, body)
    except Exception as e:  # pragma: no cover — realtime не должен ломать основной поток
        logger.warning("consalting realtime send failed: %s", e)


# ===== карточные события доски (company-group, с фильтром видимости в консьюмере) =====

def _broadcast(lead, event_type, payload=None):
    if not lead.company_id:
        return
    _send([company_group(lead.company_id)], event_type, payload or serialize_lead(lead), _BOARD_HANDLER)


def lead_created(lead):
    """Новый лид (по умолчанию в общем пуле, owner=None — виден всем)."""
    _broadcast(lead, "lead.created")


def lead_updated(lead):
    _broadcast(lead, "lead.updated")


def lead_claimed(lead):
    """Лид «взят» сотрудником: owner назначен → у остальных карточка исчезает."""
    _broadcast(lead, "lead.claimed")


def lead_released(lead):
    """Лид возвращён в общий пул: owner снят → снова виден всем."""
    _broadcast(lead, "lead.released")


def lead_deleted(lead):
    payload = {
        "id": str(lead.id),
        "company": str(lead.company_id) if lead.company_id else None,
        "branch": str(lead.branch_id) if lead.branch_id else None,
        "funnel": str(lead.funnel_id) if lead.funnel_id else None,
        "owner": str(lead.owner_id) if lead.owner_id else None,
    }
    _broadcast(lead, "lead.deleted", payload=payload)


# ===== персональные уведомления (user-group, без фильтра видимости) =====

def notify_user(user_id, event_type, payload):
    if not user_id:
        return
    _send([user_group(user_id)], event_type, payload, _NOTIFY_HANDLER)


# ===== обратная совместимость: вызывается из signals.py для событий воронки =====

def push(lead, event_type, payload=None):
    """Транслирует событие воронки (stage_changed / lead_won / …) на доску.

    Имена приводим к виду ``lead.<event_type>`` для единообразия с карточными
    событиями выше.
    """
    _broadcast(lead, f"lead.{event_type}", payload=payload)
