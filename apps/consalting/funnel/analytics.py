"""Аналитика воронки: конверсия, время в стадии, drop-off, win-rate."""
from decimal import Decimal

from django.db.models import Sum, Count, Avg, F, Case, When, DurationField, ExpressionWrapper, Q
from django.db.models.functions import TruncDate

from ..models import (
    LeadConsalting, StageTransitionConsalting, FunnelStageConsalting, SaleConsalting,
)


def _consalting_deal_ids(company):
    """ID сделок (main.ClientDeal), относящихся к консалтингу:
    подписочные подложки (SaleConsalting.subscription_deal) + сделки из register-payment
    (LeadConsalting.payment_deal). Нужно, чтобы не захватить чужие CRM-сделки."""
    sub_ids = (
        SaleConsalting.objects.filter(company=company)
        .exclude(subscription_deal__isnull=True)
        .values_list("subscription_deal_id", flat=True)
    )
    pay_ids = (
        LeadConsalting.objects.filter(company=company)
        .exclude(payment_deal__isnull=True)
        .values_list("payment_deal_id", flat=True)
    )
    return set(sub_ids) | set(pay_ids)


def consalting_paid_income(company, date_from=None, date_to=None, branch=None, with_daily=False):
    """Фактически полученные деньги по консалтингу за период («факт оплаты»).

    Источники:
      A) DealPayment (PAY − REFUND) по сделкам консалтинга — подписочные взносы и
         любые оплаты рассрочки/долга через /deals/{id}/pay/ (по paid_date);
      B) разовые продажи cash/transfer из register-payment (ClientDeal kind=SALE) —
         оплачены в момент оформления, DealPayment для них не создаётся (по created_at).

    Возвращает Decimal total, либо (total, {date: Decimal}) при with_daily=True.
    """
    from apps.main.models import ClientDeal, DealPayment

    deal_ids = _consalting_deal_ids(company)
    if not deal_ids:
        return (Decimal("0.00"), {}) if with_daily else Decimal("0.00")

    signed = Case(When(kind=DealPayment.Kind.REFUND, then=-F("amount")), default=F("amount"))

    pay_qs = DealPayment.objects.filter(deal_id__in=deal_ids)
    sale_qs = ClientDeal.objects.filter(id__in=deal_ids, kind=ClientDeal.Kind.SALE)
    if branch:
        pay_qs = pay_qs.filter(deal__branch=branch)
        sale_qs = sale_qs.filter(branch=branch)
    if date_from:
        pay_qs = pay_qs.filter(paid_date__gte=date_from)
        sale_qs = sale_qs.filter(created_at__date__gte=date_from)
    if date_to:
        pay_qs = pay_qs.filter(paid_date__lte=date_to)
        sale_qs = sale_qs.filter(created_at__date__lte=date_to)

    pay_sum = pay_qs.aggregate(s=Sum(signed))["s"] or Decimal("0.00")
    sale_sum = sale_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    total = pay_sum + sale_sum

    if not with_daily:
        return total

    by_day = {}
    for r in pay_qs.values("paid_date").annotate(v=Sum(signed)):
        if r["paid_date"]:
            by_day[r["paid_date"]] = by_day.get(r["paid_date"], Decimal("0.00")) + (r["v"] or Decimal("0.00"))
    for r in sale_qs.annotate(d=TruncDate("created_at")).values("d").annotate(v=Sum("amount")):
        if r["d"]:
            by_day[r["d"]] = by_day.get(r["d"], Decimal("0.00")) + (r["v"] or Decimal("0.00"))
    return total, by_day


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


class SalesAnalytics:
    """Агрегированная аналитика продаж консалтинга (SaleConsalting).

    Реализация по спецификации docs-consaltion/analytics.md:
    KPIs (доход, продажи, заявки, средний чек, уникальные/повторные клиенты, MRR, факт оплаты),
    детализация по услугам с разбивкой по тарифам, рейтинг сотрудников, заявки по статусам.
    """

    @staticmethod
    def compute(company, date_from=None, date_to=None, branch=None, user=None, service=None):
        from ..models import RequestsConsalting

        qs = SaleConsalting.objects.filter(company=company)
        if branch:
            qs = qs.filter(branch_id=branch)
        if user:
            qs = qs.filter(user_id=user)
        if service:
            qs = qs.filter(services_id=service)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        agg = qs.aggregate(
            revenue=Sum("total"),
            count=Count("id"),
            subscription_total=Sum("subscription_amount"),
        )
        count = agg["count"] or 0
        revenue = float(agg["revenue"] or 0)
        sub_count = qs.filter(subscription_amount__gt=0).count()

        # 1. Заявки на консультацию (RequestsConsalting)
        req_qs = RequestsConsalting.objects.filter(company=company)
        if branch:
            req_qs = req_qs.filter(branch_id=branch)
        if date_from:
            req_qs = req_qs.filter(created_at__date__gte=date_from)
        if date_to:
            req_qs = req_qs.filter(created_at__date__lte=date_to)

        req_counts = dict(req_qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
        requests_count = req_qs.count()
        requests_by_status = {
            "new": req_counts.get("new", 0),
            "in_work": req_counts.get("in_work", 0),
            "done": req_counts.get("done", 0),
            "canceled": req_counts.get("canceled", 0),
        }

        # 2. Уникальные и повторные клиенты
        clients_qs = (
            qs.filter(client__isnull=False)
            .values("client_id")
            .annotate(sales_cnt=Count("id"))
        )
        unique_clients = len(clients_qs)
        repeat_clients = sum(1 for c in clients_qs if c["sales_cnt"] > 1)

        # 3. Расчёт MRR (абонентская плата)
        sub_mrr = Decimal("0.00")
        sub_sales = qs.select_related("tariff").filter(
            Q(subscription_amount__gt=0) | Q(tariff__subscription_amount__gt=0)
        )
        for sale in sub_sales:
            amt = sale.subscription_amount or (sale.tariff.subscription_amount if sale.tariff else Decimal("0.00"))
            period = sale.subscription_period or (sale.tariff.subscription_period if sale.tariff else "month")
            if amt > 0:
                if period == "year":
                    sub_mrr += amt / Decimal("12")
                else:
                    sub_mrr += amt
        subscription_mrr = float(round(sub_mrr, 2))

        # 4. Детализация по услугам и тарифам
        service_groups = {}
        for sale in qs.select_related("services", "tariff").all():
            srv_id = str(sale.services.id) if sale.services else None
            srv_name = sale.services.name if sale.services else "(без услуги)"
            if srv_id not in service_groups:
                service_groups[srv_id] = {
                    "service_id": srv_id,
                    "service_name": srv_name,
                    "count": 0,
                    "revenue": Decimal("0.00"),
                    "clients_set": set(),
                    "tariffs_dict": {},
                }
            sg = service_groups[srv_id]
            sg["count"] += 1
            sale_amt = sale.total or Decimal("0.00")
            sg["revenue"] += sale_amt
            if sale.client_id:
                sg["clients_set"].add(sale.client_id)

            t_name = sale.tariff.name if sale.tariff else "(без тарифа)"
            t_id = str(sale.tariff.id) if sale.tariff else None
            if t_name not in sg["tariffs_dict"]:
                sg["tariffs_dict"][t_name] = {
                    "tariff_id": t_id,
                    "tariff_name": t_name,
                    "count": 0,
                    "revenue": Decimal("0.00"),
                }
            td = sg["tariffs_dict"][t_name]
            td["count"] += 1
            td["revenue"] += sale_amt

        by_service = []
        for srv_id, sg in service_groups.items():
            s_rev = float(sg["revenue"])
            s_cnt = sg["count"]
            tariffs_list = [
                {
                    "tariff_id": td["tariff_id"],
                    "tariff_name": td["tariff_name"],
                    "count": td["count"],
                    "revenue": float(td["revenue"]),
                }
                for td in sg["tariffs_dict"].values()
            ]
            tariffs_list.sort(key=lambda x: x["revenue"], reverse=True)

            by_service.append({
                "service_id": sg["service_id"],
                "service_name": sg["service_name"],
                "count": s_cnt,
                "revenue": s_rev,
                "avg_check": round(s_rev / s_cnt, 2) if s_cnt else 0.0,
                "clients": len(sg["clients_set"]),
                "share": round((s_rev / revenue * 100), 1) if revenue > 0 else 0.0,
                "tariffs": tariffs_list,
            })
        by_service.sort(key=lambda x: x["revenue"], reverse=True)

        # 5. Детализация по сотрудникам
        emp_groups = {}
        for sale in qs.select_related("user").all():
            u_id = str(sale.user.id) if sale.user else None
            u_name = (f"{sale.user.first_name or ''} {sale.user.last_name or ''}".strip() or sale.user.email) if sale.user else "(не указан)"
            if u_id not in emp_groups:
                emp_groups[u_id] = {
                    "user_id": u_id,
                    "name": u_name,
                    "count": 0,
                    "revenue": Decimal("0.00"),
                }
            eg = emp_groups[u_id]
            eg["count"] += 1
            eg["revenue"] += (sale.total or Decimal("0.00"))

        by_employee = []
        for eg in emp_groups.values():
            by_employee.append({
                "user_id": eg["user_id"],
                "name": eg["name"],
                "user_name": eg["name"],
                "count": eg["count"],
                "revenue": float(eg["revenue"]),
            })
        by_employee.sort(key=lambda x: x["revenue"], reverse=True)

        # 6. Динамика по дням
        by_day = [
            {"date": r["d"].isoformat() if r["d"] else None, "revenue": float(r["v"] or 0), "count": r["c"]}
            for r in (
                qs.annotate(d=TruncDate("created_at"))
                .values("d").annotate(v=Sum("total"), c=Count("id")).order_by("d")
            )
        ]

        # 7. Зарегистрированный факт оплаты
        paid_income = consalting_paid_income(company, date_from=date_from, date_to=date_to, branch=branch)

        kpis = {
            "revenue": revenue,
            "sales_count": count,
            "requests_count": requests_count,
            "avg_check": round(revenue / count, 2) if count else 0.0,
            "unique_clients": unique_clients,
            "repeat_clients": repeat_clients,
            "subscription_mrr": subscription_mrr,
            "paid_income": float(paid_income),
        }

        return {
            "kpis": kpis,
            "totals": {
                "count": count,
                "revenue": revenue,
                "avg_check": round(revenue / count, 2) if count else 0.0,
                "subscription_count": sub_count,
                "subscription_total": float(agg["subscription_total"] or 0),
                "paid_income": float(paid_income),
            },
            "by_service": by_service,
            "by_employee": by_employee,
            "by_day": by_day,
            "requests_by_status": requests_by_status,
        }

