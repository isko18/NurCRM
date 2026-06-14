"""Аналитика воронки: конверсия, время в стадии, drop-off, win-rate."""
from django.db.models import Sum, Count, Avg, F, DurationField, ExpressionWrapper

from ..models import LeadConsalting, StageTransitionConsalting, FunnelStageConsalting


class PipelineAnalytics:

    @staticmethod
    def compute(funnel, date_from=None, date_to=None, branch=None, owner=None):
        leads = LeadConsalting.objects.filter(funnel=funnel)
        trans = StageTransitionConsalting.objects.filter(lead__funnel=funnel)
        if date_from:
            leads = leads.filter(created_at__gte=date_from)
            trans = trans.filter(created_at__gte=date_from)
        if date_to:
            leads = leads.filter(created_at__lte=date_to)
            trans = trans.filter(created_at__lte=date_to)
        if branch:
            leads = leads.filter(branch_id=branch)
            trans = trans.filter(branch_id=branch)
        if owner:
            leads = leads.filter(owner_id=owner)
            trans = trans.filter(lead__owner_id=owner)

        total = leads.count()
        won = leads.filter(status=LeadConsalting.Status.WON).count()
        lost = leads.filter(status=LeadConsalting.Status.LOST).count()
        pipeline_value = leads.aggregate(v=Sum("estimated_value"))["v"] or 0

        # средний цикл сделки (создан → выигран)
        won_qs = leads.filter(status=LeadConsalting.Status.WON, won_at__isnull=False).annotate(
            cycle=ExpressionWrapper(F("won_at") - F("created_at"), output_field=DurationField())
        ).aggregate(avg=Avg("cycle"))
        avg_cycle = won_qs["avg"]
        avg_cycle_days = round(avg_cycle.total_seconds() / 86400, 1) if avg_cycle else None

        # по стадиям
        stages_stats = []
        for stage in funnel.stages.all().order_by("order"):
            in_stage = leads.filter(stage=stage)
            entered = trans.filter(to_stage=stage).count()
            left = trans.filter(from_stage=stage).count()
            lost_from = trans.filter(from_stage=stage, to_type=FunnelStageConsalting.StageType.LOST).count()
            avg_sec = trans.filter(from_stage=stage).aggregate(a=Avg("seconds_in_prev"))["a"]

            conversion = round((left - lost_from) / entered, 3) if entered else None
            drop_off = round(lost_from / entered, 3) if entered else None

            stages_stats.append({
                "stage_id": str(stage.id),
                "name": stage.name,
                "stage_type": stage.stage_type,
                "count": in_stage.count(),
                "value": float(in_stage.aggregate(v=Sum("estimated_value"))["v"] or 0),
                "avg_hours_in_stage": round(avg_sec / 3600, 1) if avg_sec else None,
                "entered": entered,
                "conversion_to_next": conversion,
                "drop_off_rate": drop_off,
            })

        by_loss = list(
            leads.filter(status=LeadConsalting.Status.LOST, loss_reason__isnull=False)
            .values("loss_reason__code", "loss_reason__label")
            .annotate(count=Count("id")).order_by("-count")
        )
        by_score = {
            row["score_grade"]: row["count"]
            for row in leads.values("score_grade").annotate(count=Count("id"))
        }

        return {
            "funnel_id": str(funnel.id),
            "funnel_name": funnel.name,
            "totals": {
                "deals": total,
                "pipeline_value": float(pipeline_value),
                "won": won,
                "lost": lost,
                "win_rate": round(won / (won + lost), 3) if (won + lost) else None,
                "avg_cycle_days": avg_cycle_days,
                "at_risk": leads.filter(is_at_risk=True).count(),
            },
            "stages": stages_stats,
            "by_loss_reason": [
                {"code": r["loss_reason__code"], "label": r["loss_reason__label"], "count": r["count"]}
                for r in by_loss
            ],
            "by_score": {"A": by_score.get("A", 0), "B": by_score.get("B", 0), "C": by_score.get("C", 0)},
        }
