"""Real-time уведомления через Channels (best-effort, не ломает основной поток)."""
import logging

logger = logging.getLogger(__name__)


def push(lead, event_type, payload=None):
    """
    Шлёт сообщение в группы менеджера и компании. Если channel layer не настроен —
    тихо выходит. Группы:
      - consalting_user_<owner_id>     (личный поток ответственного)
      - consalting_company_<company_id>
    Подписка консьюмера — отдельная задача фронта/ws-слоя.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        layer = get_channel_layer()
        if not layer:
            return

        body = {
            "type": "consalting.event",   # метод консьюмера consalting_event
            "event": event_type,
            "payload": payload or _default_payload(lead),
        }
        groups = [f"consalting_company_{lead.company_id}"]
        if lead.owner_id:
            groups.append(f"consalting_user_{lead.owner_id}")
        for g in groups:
            async_to_sync(layer.group_send)(g, body)
    except Exception as e:  # pragma: no cover
        logger.warning("consalting realtime push failed: %s", e)


def _default_payload(lead):
    return {
        "lead_id": str(lead.id),
        "title": lead.title,
        "stage": str(lead.stage_id) if lead.stage_id else None,
        "status": lead.status,
        "score_grade": lead.score_grade,
        "is_at_risk": lead.is_at_risk,
    }
