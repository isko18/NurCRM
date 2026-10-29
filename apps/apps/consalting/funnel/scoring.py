"""Скоринг лида: факторы → 0..100 → грейд A/B/C."""
from decimal import Decimal

from django.utils import timezone

from ..models import LeadConsalting


# Порог «крупной сделки» по умолчанию (даёт максимум баллов за размер).
# Может быть переопределён через company-настройку в будущем.
DEFAULT_BIG_DEAL_VALUE = Decimal("100000")


class ScoringService:
    WEIGHTS = {
        "budget": 25,            # бюджет подтверждён
        "urgency_high": 20,      # high; medium = 10
        "decision_maker": 20,    # ЛПР вовлечён
        "fast_response": 15,     # отвечает быстро (<=30 мин)
        "deal_size": 20,         # размер сделки (нормируется)
    }

    @classmethod
    def _deal_size_points(cls, lead, big_deal_value=DEFAULT_BIG_DEAL_VALUE):
        value = lead.estimated_value or Decimal("0")
        if value <= 0 or big_deal_value <= 0:
            return 0
        ratio = min(Decimal("1"), Decimal(value) / Decimal(big_deal_value))
        return int(ratio * cls.WEIGHTS["deal_size"])

    @classmethod
    def compute(cls, lead):
        """Чистый расчёт без сохранения. Возвращает (value, grade)."""
        v = 0
        if lead.budget_confirmed:
            v += cls.WEIGHTS["budget"]
        if lead.urgency == LeadConsalting.Urgency.HIGH:
            v += cls.WEIGHTS["urgency_high"]
        elif lead.urgency == LeadConsalting.Urgency.MEDIUM:
            v += cls.WEIGHTS["urgency_high"] // 2
        if lead.decision_maker_engaged:
            v += cls.WEIGHTS["decision_maker"]
        if lead.avg_response_minutes is not None and lead.avg_response_minutes <= 30:
            v += cls.WEIGHTS["fast_response"]
        v += cls._deal_size_points(lead)
        v = max(0, min(100, v))

        grade = (
            LeadConsalting.Grade.A if v >= 70
            else LeadConsalting.Grade.B if v >= 40
            else LeadConsalting.Grade.C
        )
        return v, grade

    @classmethod
    def recalculate(cls, lead, save=True):
        """Пересчитать и (опц.) сохранить скоринг лида. Возвращает (value, grade, changed)."""
        value, grade = cls.compute(lead)
        changed = (value != lead.score_value) or (grade != lead.score_grade)
        lead.score_value = value
        lead.score_grade = grade
        lead.score_updated_at = timezone.now()
        if save:
            LeadConsalting.objects.filter(pk=lead.pk).update(
                score_value=value, score_grade=grade, score_updated_at=lead.score_updated_at
            )
        return value, grade, changed
