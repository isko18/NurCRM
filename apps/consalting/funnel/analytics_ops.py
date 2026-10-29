"""Операционная аналитика консалтинга: мессенджер, источники, менеджеры, сводка.

Дополняет :mod:`analytics` (продажи + воронка), закрывая то, чего в ней не было:

* **Мессенджер** — весь бизнес идёт перепиской в WhatsApp, но по ней не считалось
  ничего: скорость первого ответа, объём переписки, неотвеченные диалоги,
  нагрузка по часам, доля неудачных отправок.
* **Источники** — у входящей заявки есть ``source``, но он нигде не агрегировался:
  не было видно, какой канал приносит заявки и как они конвертируются.
* **Менеджеры** — рейтинг был только по продажам; нагрузки и конверсии по лидам,
  а также скорости ответа в разрезе сотрудника не было.
* **Сводка + сравнение с прошлым периодом** — не было ни единой точки входа, ни
  динамики (рост/падение) ни в одном отчёте.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Sum, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from ..models import (
    LeadConsalting,
    InboundLeadConsalting,
    WhatsAppMessageConsalting,
)

# Диалог считается «без ответа», если последнее сообщение входящее и с него
# прошло больше этого времени (та же логика, что у скана SLA).
UNANSWERED_AFTER_MINUTES = 15


# ---------------------------------------------------------------- утилиты

def parse_period(date_from=None, date_to=None, default_days=30):
    """Приводит период к паре ``date`` и подставляет разумные значения по умолчанию."""
    today = timezone.localdate()
    d_to = parse_date(str(date_to)) if date_to else today
    d_from = parse_date(str(date_from)) if date_from else (d_to - timedelta(days=default_days - 1))
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


def previous_period(d_from, d_to):
    """Предыдущий период той же длины — для сравнения «период к периоду»."""
    span = (d_to - d_from).days + 1
    prev_to = d_from - timedelta(days=1)
    return prev_to - timedelta(days=span - 1), prev_to


def delta(current, previous):
    """Абсолютное и процентное изменение метрики (для стрелок роста на фронте)."""
    cur = float(current or 0)
    prev = float(previous or 0)
    if prev == 0:
        pct = 100.0 if cur > 0 else 0.0
    else:
        pct = round((cur - prev) / abs(prev) * 100, 1)
    return {"current": cur, "previous": prev, "diff": round(cur - prev, 2), "percent": pct}


def _median(values):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _fmt_minutes(seconds):
    return round(seconds / 60, 1) if seconds is not None else None


# ------------------------------------------------------------- мессенджер

class MessengerAnalytics:
    """Аналитика переписки WhatsApp/Wazzup — ключевой канал консалтинга."""

    @staticmethod
    def compute(company, date_from=None, date_to=None, branch=None, owner=None):
        d_from, d_to = parse_period(date_from, date_to)

        qs = WhatsAppMessageConsalting.objects.filter(
            company=company,
            created_at__date__gte=d_from,
            created_at__date__lte=d_to,
        )
        if branch:
            qs = qs.filter(branch_id=branch)
        if owner:
            qs = qs.filter(lead__owner_id=owner)

        rows = list(
            qs.order_by("created_at").values_list(
                "lead_id", "direction", "status", "created_at", "lead__owner_id",
            )
        )

        inbound = sum(1 for r in rows if r[1] == WhatsAppMessageConsalting.Direction.INBOUND)
        outbound = len(rows) - inbound
        failed = sum(1 for r in rows if r[2] == WhatsAppMessageConsalting.Status.FAILED)

        # --- группировка по диалогам (лидам) ---
        by_lead = defaultdict(list)
        for lead_id, direction, _status, created, owner_id in rows:
            if lead_id:
                by_lead[lead_id].append((direction, created, owner_id))

        response_secs = []
        per_owner = defaultdict(lambda: {"in": 0, "out": 0, "resp": []})
        answered = waiting = 0

        for lead_id, seq in by_lead.items():
            first_in = None
            for direction, created, owner_id in seq:
                if direction == WhatsAppMessageConsalting.Direction.INBOUND:
                    per_owner[owner_id]["in"] += 1
                    if first_in is None:
                        first_in = created
                else:
                    per_owner[owner_id]["out"] += 1

            # скорость первого ответа: первое входящее → первое исходящее после него
            for direction, created, owner_id in seq:
                if direction == WhatsAppMessageConsalting.Direction.OUTBOUND and first_in and created >= first_in:
                    secs = (created - first_in).total_seconds()
                    response_secs.append(secs)
                    per_owner[owner_id]["resp"].append(secs)
                    answered += 1
                    break
            else:
                if first_in is not None:
                    waiting += 1

        # --- динамика по дням ---
        day_map = defaultdict(lambda: {"inbound": 0, "outbound": 0})
        hour_map = defaultdict(int)
        for _lead, direction, _st, created, _own in rows:
            local = timezone.localtime(created)
            key = local.date().isoformat()
            if direction == WhatsAppMessageConsalting.Direction.INBOUND:
                day_map[key]["inbound"] += 1
                hour_map[local.hour] += 1
            else:
                day_map[key]["outbound"] += 1

        by_day = [
            {"date": d, "inbound": v["inbound"], "outbound": v["outbound"], "total": v["inbound"] + v["outbound"]}
            for d, v in sorted(day_map.items())
        ]
        by_hour = [{"hour": h, "inbound": hour_map.get(h, 0)} for h in range(24)]

        # --- сотрудники ---
        owner_ids = [o for o in per_owner.keys() if o]
        names = {
            str(u["id"]): (f"{u['first_name'] or ''} {u['last_name'] or ''}".strip() or u["email"])
            for u in _users_by_ids(owner_ids)
        }
        by_operator = []
        for owner_id, v in per_owner.items():
            avg_resp = sum(v["resp"]) / len(v["resp"]) if v["resp"] else None
            by_operator.append({
                "user_id": str(owner_id) if owner_id else None,
                "name": names.get(str(owner_id), "(без ответственного)"),
                "inbound": v["in"],
                "outbound": v["out"],
                "answered": len(v["resp"]),
                "avg_response_minutes": _fmt_minutes(avg_resp),
            })
        by_operator.sort(key=lambda x: x["outbound"], reverse=True)

        # --- диалоги без ответа прямо сейчас (по всей базе, не только период) ---
        waiting_now = MessengerAnalytics.unanswered_chats(company, branch=branch, owner=owner)

        avg_resp = sum(response_secs) / len(response_secs) if response_secs else None
        return {
            "period": {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
            "totals": {
                "messages": len(rows),
                "inbound": inbound,
                "outbound": outbound,
                "chats": len(by_lead),
                "failed": failed,
                "failure_rate": round(failed / outbound, 3) if outbound else 0.0,
            },
            "response": {
                "avg_minutes": _fmt_minutes(avg_resp),
                "median_minutes": _fmt_minutes(_median(response_secs)),
                "answered_chats": answered,
                "never_answered_chats": waiting,
                "answer_rate": round(answered / (answered + waiting), 3) if (answered + waiting) else None,
            },
            "waiting_now": waiting_now,
            "by_day": by_day,
            "by_hour": by_hour,
            "by_operator": by_operator,
        }

    @staticmethod
    def unanswered_chats(company, branch=None, owner=None, minutes=UNANSWERED_AFTER_MINUTES):
        """Диалоги, где последнее сообщение — входящее и висит дольше порога."""
        qs = WhatsAppMessageConsalting.objects.filter(company=company)
        if branch:
            qs = qs.filter(branch_id=branch)
        if owner:
            qs = qs.filter(lead__owner_id=owner)

        last_by_lead = {}
        for lead_id, direction, created in qs.order_by("created_at").values_list(
            "lead_id", "direction", "created_at"
        ):
            if lead_id:
                last_by_lead[lead_id] = (direction, created)

        threshold = timezone.now() - timedelta(minutes=minutes)
        stale = [
            (lead_id, created)
            for lead_id, (direction, created) in last_by_lead.items()
            if direction == WhatsAppMessageConsalting.Direction.INBOUND and created <= threshold
        ]
        if not stale:
            return {"count": 0, "items": []}

        leads = {
            str(l.id): l
            for l in LeadConsalting.objects.filter(
                id__in=[s[0] for s in stale]
            ).select_related("owner")
        }
        items = []
        for lead_id, created in sorted(stale, key=lambda x: x[1]):
            lead = leads.get(str(lead_id))
            if not lead:
                continue
            items.append({
                "lead_id": str(lead.id),
                "name": lead.full_name or lead.title,
                "phone": lead.phone,
                "owner": (
                    f"{lead.owner.first_name or ''} {lead.owner.last_name or ''}".strip() or lead.owner.email
                ) if lead.owner else None,
                "last_message_at": created.isoformat(),
                "waiting_minutes": int((timezone.now() - created).total_seconds() / 60),
            })
        return {"count": len(items), "items": items[:50]}


def _users_by_ids(ids):
    if not ids:
        return []
    from apps.users.models import User
    return User.objects.filter(id__in=ids).values("id", "first_name", "last_name", "email")


# -------------------------------------------------------------- источники

class SourceAnalytics:
    """Откуда приходят заявки и как они доходят до сделки."""

    @staticmethod
    def compute(company, date_from=None, date_to=None, branch=None):
        d_from, d_to = parse_period(date_from, date_to)

        qs = InboundLeadConsalting.objects.filter(
            company=company, created_at__date__gte=d_from, created_at__date__lte=d_to
        )

        total = qs.count()
        by_status = dict(qs.values("status").annotate(c=Count("id")).values_list("status", "c"))

        # разрез по источникам + конверсия в лид и в выигранную сделку
        rows = qs.values("source").annotate(
            count=Count("id"),
            converted=Count("id", filter=Q(status=InboundLeadConsalting.Status.CONVERTED)),
            rejected=Count("id", filter=Q(status=InboundLeadConsalting.Status.REJECTED)),
            linked=Count("id", filter=Q(lead__isnull=False)),
            won=Count("id", filter=Q(lead__status=LeadConsalting.Status.WON)),
        ).order_by("-count")

        by_source = []
        for r in rows:
            cnt = r["count"] or 0
            by_source.append({
                "source": r["source"] or "(не указан)",
                "count": cnt,
                "linked_leads": r["linked"],
                "converted": r["converted"],
                "rejected": r["rejected"],
                "won": r["won"],
                "conversion_to_lead": round(r["linked"] / cnt, 3) if cnt else None,
                "conversion_to_won": round(r["won"] / cnt, 3) if cnt else None,
                "share": round(cnt / total * 100, 1) if total else 0.0,
            })

        day_map = defaultdict(int)
        for d in qs.values_list("created_at", flat=True):
            day_map[timezone.localtime(d).date().isoformat()] += 1

        linked = qs.filter(lead__isnull=False).count()
        won = qs.filter(lead__status=LeadConsalting.Status.WON).count()

        return {
            "period": {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
            "totals": {
                "requests": total,
                "linked_leads": linked,
                "won": won,
                "conversion_to_lead": round(linked / total, 3) if total else None,
                "conversion_to_won": round(won / total, 3) if total else None,
            },
            "by_status": {
                "new": by_status.get(InboundLeadConsalting.Status.NEW, 0),
                "assigned": by_status.get(InboundLeadConsalting.Status.ASSIGNED, 0),
                "in_work": by_status.get(InboundLeadConsalting.Status.IN_WORK, 0),
                "converted": by_status.get(InboundLeadConsalting.Status.CONVERTED, 0),
                "rejected": by_status.get(InboundLeadConsalting.Status.REJECTED, 0),
            },
            "by_source": by_source,
            "by_day": [{"date": d, "count": c} for d, c in sorted(day_map.items())],
        }


# -------------------------------------------------------------- менеджеры

class ManagerAnalytics:
    """Нагрузка и результативность сотрудников по лидам (не только по продажам)."""

    @staticmethod
    def compute(company, date_from=None, date_to=None, branch=None):
        d_from, d_to = parse_period(date_from, date_to)

        leads = LeadConsalting.objects.filter(
            company=company, created_at__date__gte=d_from, created_at__date__lte=d_to
        )
        if branch:
            leads = leads.filter(branch_id=branch)

        rows = leads.values("owner_id").annotate(
            total=Count("id"),
            won=Count("id", filter=Q(status=LeadConsalting.Status.WON)),
            lost=Count("id", filter=Q(status=LeadConsalting.Status.LOST)),
            at_risk=Count("id", filter=Q(is_at_risk=True)),
            pipeline=Sum("estimated_value"),
        ).order_by("-total")

        # скорость ответа по сотрудникам берём из аналитики мессенджера
        msg = MessengerAnalytics.compute(company, date_from=d_from, date_to=d_to, branch=branch)
        resp_by_user = {o["user_id"]: o for o in msg["by_operator"]}

        names = {
            str(u["id"]): (f"{u['first_name'] or ''} {u['last_name'] or ''}".strip() or u["email"])
            for u in _users_by_ids([r["owner_id"] for r in rows if r["owner_id"]])
        }

        items = []
        for r in rows:
            uid = str(r["owner_id"]) if r["owner_id"] else None
            closed = (r["won"] or 0) + (r["lost"] or 0)
            op = resp_by_user.get(uid, {})
            items.append({
                "user_id": uid,
                "name": names.get(uid, "(без ответственного)"),
                "leads": r["total"],
                "won": r["won"],
                "lost": r["lost"],
                "win_rate": round(r["won"] / closed, 3) if closed else None,
                "at_risk": r["at_risk"],
                "pipeline_value": float(r["pipeline"] or 0),
                "messages_out": op.get("outbound", 0),
                "avg_response_minutes": op.get("avg_response_minutes"),
            })

        return {
            "period": {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
            "managers": items,
        }


# ----------------------------------------------------------------- сводка

class DashboardAnalytics:
    """Единая точка: всё главное за период + сравнение с предыдущим периодом."""

    @staticmethod
    def compute(company, date_from=None, date_to=None, branch=None):
        from .analytics import SalesAnalytics

        d_from, d_to = parse_period(date_from, date_to)
        p_from, p_to = previous_period(d_from, d_to)

        sales = SalesAnalytics.compute(company, date_from=d_from, date_to=d_to, branch=branch)
        sales_prev = SalesAnalytics.compute(company, date_from=p_from, date_to=p_to, branch=branch)
        messenger = MessengerAnalytics.compute(company, date_from=d_from, date_to=d_to, branch=branch)
        messenger_prev = MessengerAnalytics.compute(company, date_from=p_from, date_to=p_to, branch=branch)
        sources = SourceAnalytics.compute(company, date_from=d_from, date_to=d_to, branch=branch)
        sources_prev = SourceAnalytics.compute(company, date_from=p_from, date_to=p_to, branch=branch)

        leads = LeadConsalting.objects.filter(company=company)
        if branch:
            leads = leads.filter(branch_id=branch)
        period_leads = leads.filter(created_at__date__gte=d_from, created_at__date__lte=d_to)
        prev_leads = leads.filter(created_at__date__gte=p_from, created_at__date__lte=p_to)

        won = period_leads.filter(status=LeadConsalting.Status.WON).count()
        lost = period_leads.filter(status=LeadConsalting.Status.LOST).count()

        return {
            "period": {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
            "compare_period": {"date_from": p_from.isoformat(), "date_to": p_to.isoformat()},
            "kpis": {
                "revenue": delta(sales["kpis"]["revenue"], sales_prev["kpis"]["revenue"]),
                "net_revenue": delta(sales["kpis"]["net_revenue"], sales_prev["kpis"]["net_revenue"]),
                "cancellations": delta(sales["kpis"]["cancellations"], sales_prev["kpis"]["cancellations"]),
                "cancel_rate": delta(sales["kpis"]["cancel_rate"], sales_prev["kpis"]["cancel_rate"]),
                "paid_income": delta(sales["kpis"]["paid_income"], sales_prev["kpis"]["paid_income"]),
                "pending_cash": delta(sales["kpis"]["pending_cash"], sales_prev["kpis"]["pending_cash"]),
                "subscription_mrr": delta(sales["kpis"]["subscription_mrr"], sales_prev["kpis"]["subscription_mrr"]),
                "sales_count": delta(sales["kpis"]["sales_count"], sales_prev["kpis"]["sales_count"]),
                "avg_check": delta(sales["kpis"]["avg_check"], sales_prev["kpis"]["avg_check"]),
                "leads": delta(period_leads.count(), prev_leads.count()),
                "requests": delta(sources["totals"]["requests"], sources_prev["totals"]["requests"]),
                "messages": delta(messenger["totals"]["messages"], messenger_prev["totals"]["messages"]),
                "avg_response_minutes": delta(
                    messenger["response"]["avg_minutes"], messenger_prev["response"]["avg_minutes"]
                ),
            },
            "leads": {
                "total": period_leads.count(),
                "won": won,
                "lost": lost,
                "in_work": period_leads.exclude(
                    status__in=[LeadConsalting.Status.WON, LeadConsalting.Status.LOST]
                ).count(),
                "win_rate": round(won / (won + lost), 3) if (won + lost) else None,
                "pipeline_value": float(period_leads.aggregate(v=Sum("estimated_value"))["v"] or 0),
                "at_risk": period_leads.filter(is_at_risk=True).count(),
            },
            "messenger": {
                "totals": messenger["totals"],
                "response": messenger["response"],
                "waiting_now": messenger["waiting_now"]["count"],
                "by_day": messenger["by_day"],
                "by_hour": messenger["by_hour"],
            },
            "sources": {
                "totals": sources["totals"],
                "by_status": sources["by_status"],
                "by_source": sources["by_source"],
            },
            "sales": {
                "by_day": sales["by_day"],
                "by_service": sales["by_service"][:10],
                "by_employee": sales["by_employee"][:10],
            },
            "managers": ManagerAnalytics.compute(
                company, date_from=d_from, date_to=d_to, branch=branch
            )["managers"][:10],
        }
