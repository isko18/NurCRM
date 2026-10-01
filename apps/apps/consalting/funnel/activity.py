"""Единая точка записи в неизменяемую ленту активностей лида (audit trail)."""
from django.utils import timezone

from ..models import LeadActivityConsalting, LeadConsalting


class ActivityLogger:
    """
    Все записи в timeline создаются ТОЛЬКО здесь — это гарантирует полноту аудита
    и единообразно обновляет lead.last_activity_at.
    """

    @staticmethod
    def log(lead, activity_type, actor=None, title="", body="", payload=None, file=None,
            touch_last_activity=True):
        activity = LeadActivityConsalting.objects.create(
            company_id=lead.company_id,
            branch_id=lead.branch_id,
            lead=lead,
            actor=actor,
            type=activity_type,
            title=title or LeadActivityConsalting.Type(activity_type).label,
            body=body or "",
            payload=payload or {},
            file=file,
        )
        # системные/служебные записи (смена скоринга и т.п.) не считаем «контактом»
        if touch_last_activity and activity_type in {
            LeadActivityConsalting.Type.NOTE,
            LeadActivityConsalting.Type.CALL,
            LeadActivityConsalting.Type.MESSAGE,
            LeadActivityConsalting.Type.EMAIL,
            LeadActivityConsalting.Type.MEETING,
            LeadActivityConsalting.Type.FILE,
        }:
            now = timezone.now()
            lead.last_activity_at = now
            # любая живая активность снимает флаг риска
            if lead.is_at_risk:
                lead.is_at_risk = False
                lead.risk_reason = ""
            LeadConsalting.objects.filter(pk=lead.pk).update(
                last_activity_at=now, is_at_risk=lead.is_at_risk, risk_reason=lead.risk_reason
            )
        return activity
