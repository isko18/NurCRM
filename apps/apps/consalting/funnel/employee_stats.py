import logging
from datetime import datetime, date, time
from decimal import Decimal
from django.db.models import Q, Count, Sum, Avg, F
from django.utils import timezone

logger = logging.getLogger("nurcrm.consalting.employee_stats")


class DefaultWeights:
    conversion = 0.35
    plan = 0.3
    speed = 0.2
    discipline = 0.15


def kpi_score(stats, weights):
    """Вычисление баллов КПД (§6.3)."""
    clamp = lambda v: max(0.0, min(100.0, float(v or 0)))

    conv_val = stats["sales"]["conversion"]
    conv_score = clamp(conv_val)

    plan_val = stats["sales"]["plan_done_percent"]
    has_plan = plan_val is not None

    if has_plan:
        plan_score = clamp(plan_val)
        w_conv = float(weights.conversion)
        w_plan = float(weights.plan)
        w_speed = float(weights.speed)
        w_disc = float(weights.discipline)
    else:
        plan_score = 0.0
        rem_weight = float(weights.conversion) + float(weights.speed) + float(weights.discipline)
        if rem_weight > 0:
            w_conv = float(weights.conversion) / rem_weight
            w_plan = 0.0
            w_speed = float(weights.speed) / rem_weight
            w_disc = float(weights.discipline) / rem_weight
        else:
            w_conv, w_plan, w_speed, w_disc = 0.35, 0.0, 0.35, 0.30

    reply = stats["speed"]["first_reply_avg_minutes"]
    speed_score = clamp((120.0 - min(reply, 120.0)) / 115.0 * 100.0) if reply is not None else 0.0

    deferred = stats["leads"]["deferred"]
    overdue = stats["leads"]["overdue"]
    discipline_score = clamp((1.0 - (overdue / deferred if deferred else 0)) * 100.0) if deferred else 100.0

    total_score = round(
        conv_score * w_conv + plan_score * w_plan + speed_score * w_speed + discipline_score * w_disc
    )
    return {
        "score": int(total_score),
        "conversion_score": int(round(conv_score)),
        "plan_score": int(round(plan_score)) if has_plan else None,
        "speed_score": int(round(speed_score)),
        "discipline_score": int(round(discipline_score)),
        "weights": {
            "conversion": round(w_conv, 4),
            "plan": round(w_plan, 4),
            "speed": round(w_speed, 4),
            "discipline": round(w_disc, 4),
        }
    }


def parse_date_range(request):
    d_from_str = request.query_params.get("date_from")
    d_to_str = request.query_params.get("date_to")

    today = timezone.localdate()
    if d_from_str:
        try:
            d_from = datetime.strptime(d_from_str, "%Y-%m-%d").date()
        except ValueError:
            d_from = today.replace(day=1)
    else:
        d_from = today.replace(day=1)

    if d_to_str:
        try:
            d_to = datetime.strptime(d_to_str, "%Y-%m-%d").date()
        except ValueError:
            d_to = today
    else:
        d_to = today

    tz = timezone.get_current_timezone()
    dt_start = timezone.make_aware(datetime.combine(d_from, time.min), tz)
    dt_end = timezone.make_aware(datetime.combine(d_to, time.max), tz)

    return d_from, d_to, dt_start, dt_end


def calculate_employee_stats(company, target_user, dt_start, dt_end, d_from, d_to):
    from apps.consalting.models import (
        LeadConsalting, SaleConsalting, LeadFunnelHistoryConsalting,
        SalesPlanConsalting, KpiWeightsConsalting, SalaryAccrualConsalting,
        SalaryPayoutConsalting, StageTransitionConsalting
    )

    # 1. Лиды
    leads_qs = LeadConsalting.objects.filter(
        company=company, owner=target_user, created_at__range=(dt_start, dt_end)
    )

    received = leads_qs.count()

    claimed = LeadFunnelHistoryConsalting.objects.filter(
        lead__company=company, owner=target_user, transition="manual",
        entered_at__range=(dt_start, dt_end)
    ).values("lead_id").distinct().count()

    in_work = LeadConsalting.objects.filter(
        company=company, owner=target_user, status=LeadConsalting.Status.IN_WORK
    ).count()

    deferred = leads_qs.filter(status="deferred").count()
    now = timezone.now()
    overdue = leads_qs.filter(status="deferred", next_action_date__lt=now).count()

    processed = leads_qs.filter(status__in=[LeadConsalting.Status.WON, LeadConsalting.Status.LOST]).count()
    no_reply_in_time = leads_qs.filter(avg_response_minutes__gt=15).count()

    # 2. Продажи
    sales_qs = SaleConsalting.objects.filter(
        company=company, user=target_user, created_at__range=(dt_start, dt_end)
    )

    valid_sales = sales_qs.exclude(description__icontains="отмена").exclude(description__icontains="отменён")
    deals = valid_sales.count()
    revenue_agg = valid_sales.aggregate(s=Sum("total"))["s"] or Decimal("0")
    revenue = float(revenue_agg)
    avg_check = round(revenue / deals, 2) if deals > 0 else 0.0

    conversion = round((deals / received * 100.0), 2) if received > 0 else 0.0

    # Личный план
    months_in_range = []
    curr = d_from.replace(day=1)
    while curr <= d_to:
        months_in_range.append(f"{curr.year:04d}-{curr.month:02d}")
        # next month
        if curr.month == 12:
            curr = date(curr.year + 1, 1, 1)
        else:
            curr = date(curr.year, curr.month + 1, 1)

    plans = SalesPlanConsalting.objects.filter(
        company=company, user=target_user, period_month__in=months_in_range
    )
    plan_amount_agg = plans.aggregate(s=Sum("amount"))["s"]
    plan = float(plan_amount_agg) if plan_amount_agg is not None else None

    plan_done_percent = round((revenue / plan * 100.0), 2) if (plan and plan > 0) else None

    canceled = sales_qs.filter(Q(description__icontains="отмена") | Q(description__icontains="отменён")).count()
    cancel_rate = round((canceled / (deals + canceled) * 100.0), 2) if (deals + canceled) > 0 else 0.0

    # 3. Скорость
    avg_resp = leads_qs.aggregate(a=Avg("avg_response_minutes"))["a"]
    first_reply_avg_minutes = round(float(avg_resp), 1) if avg_resp is not None else None

    won_leads = leads_qs.filter(status=LeadConsalting.Status.WON, won_at__isnull=False)
    cycles = []
    for l in won_leads:
        diff_days = (l.won_at - l.created_at).total_seconds() / 86400.0
        if diff_days >= 0:
            cycles.append(diff_days)
    deal_cycle_avg_days = round(sum(cycles) / len(cycles), 1) if cycles else None

    # Скорость по стадиям
    by_stage_rows = (
        StageTransitionConsalting.objects.filter(
            company=company, lead__owner=target_user, created_at__range=(dt_start, dt_end)
        )
        .values("from_stage_id", "from_stage__name")
        .annotate(avg_sec=Avg("seconds_in_prev"))
    )
    by_stage = []
    for row in by_stage_rows:
        if row["from_stage_id"]:
            avg_h = round((row["avg_sec"] or 0) / 3600.0, 1)
            by_stage.append({
                "stage": str(row["from_stage_id"]),
                "stage_display": row["from_stage__name"] or "—",
                "avg_hours": avg_h,
            })

    # 4. Веса КПД
    weights_obj = KpiWeightsConsalting.objects.filter(company=company).first()
    weights = weights_obj if weights_obj else DefaultWeights()

    raw_stats = {
        "leads": {
            "received": received, "claimed": claimed, "in_work": in_work,
            "deferred": deferred, "overdue": overdue, "processed": processed,
            "no_reply_in_time": no_reply_in_time,
        },
        "sales": {
            "deals": deals, "revenue": revenue, "avg_check": avg_check,
            "conversion": conversion, "plan": plan,
            "plan_done_percent": plan_done_percent,
            "canceled": canceled, "cancel_rate": cancel_rate,
        },
        "speed": {
            "first_reply_avg_minutes": first_reply_avg_minutes,
            "deal_cycle_avg_days": deal_cycle_avg_days,
            "by_stage": by_stage,
        }
    }

    kpi_res = kpi_score(raw_stats, weights)

    # 5. Рейтинг компании за этот период
    from apps.users.models import User
    active_employees = list(User.objects.filter(company=company, is_active=True).order_by("id"))
    all_kpi_scores = []

    for emp in active_employees:
        if emp.id == target_user.id:
            all_kpi_scores.append((emp.id, kpi_res["score"]))
        else:
            e_leads = LeadConsalting.objects.filter(company=company, owner=emp, created_at__range=(dt_start, dt_end)).count()
            e_sales = SaleConsalting.objects.filter(company=company, user=emp, created_at__range=(dt_start, dt_end)).exclude(description__icontains="отмена").count()
            e_conv = (e_sales / e_leads * 100.0) if e_leads > 0 else 0.0
            e_stats = {
                "sales": {"conversion": e_conv, "plan_done_percent": None},
                "speed": {"first_reply_avg_minutes": None},
                "leads": {"deferred": 0, "overdue": 0}
            }
            e_score = kpi_score(e_stats, weights)["score"]
            all_kpi_scores.append((emp.id, e_score))

    all_kpi_scores.sort(key=lambda x: x[1], reverse=True)
    rank = 1
    for idx, (emp_id, sc) in enumerate(all_kpi_scores):
        if emp_id == target_user.id:
            rank = idx + 1
            break

    kpi_res["rank"] = rank
    kpi_res["of"] = len(active_employees)
    raw_stats["kpi"] = kpi_res

    # 6. Зарплата за период
    accrued_agg = SalaryAccrualConsalting.objects.filter(
        company=company, user=target_user, created_at__range=(dt_start, dt_end)
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    paid_agg = SalaryPayoutConsalting.objects.filter(
        company=company, user=target_user, created_at__range=(dt_start, dt_end)
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    accrued = float(accrued_agg)
    paid = float(paid_agg)
    raw_stats["salary"] = {
        "accrued": accrued,
        "paid": paid,
        "remaining": round(accrued - paid, 2),
    }

    # 7. Топ услуг
    top_svc_rows = (
        valid_sales.values("services_id", "services__name")
        .annotate(deals_cnt=Count("id"), rev_sum=Sum("total"))
        .order_by("-rev_sum")[:5]
    )
    top_services = [
        {
            "service": str(row["services_id"]) if row["services_id"] else None,
            "service_name": row["services__name"] or "Услуга",
            "deals": row["deals_cnt"],
            "revenue": float(row["rev_sum"] or 0),
        }
        for row in top_svc_rows
    ]
    raw_stats["top_services"] = top_services

    return raw_stats
