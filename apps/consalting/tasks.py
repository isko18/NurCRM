"""Celery-сканы воронки (Фаза 5): риск бездействия, просроченные задачи, SLA."""
import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import (
    LeadConsalting, LeadTaskConsalting, AutomationRuleConsalting,
    FunnelStageConsalting,
)
from .funnel.events import emit
from .funnel.state_machine import ACTIVE_TYPES

CLOSED_STATUSES = (LeadConsalting.Status.WON, LeadConsalting.Status.LOST)


@shared_task
def process_wazzup_webhook(payload):
    """Тяжёлая обработка вебхука Wazzup вне HTTP-запроса.

    Быстрая пред-трансляция сокетов уже выполнена синхронно в
    ``WazzupConsaltingService.enqueue_webhook`` — здесь её пропускаем.
    """
    from .funnel.wazzup import WazzupConsaltingService
    WazzupConsaltingService.handle_wazzup_webhook(payload, skip_fast_broadcast=True)


@shared_task
def send_wazzup_message(wa_message_id, account_id, text, content_uri):
    """Реальная отправка исходящего сообщения в Wazzup API вне HTTP-запроса.

    Сообщение уже создано в статусе PENDING и оптимистично разослано по сокетам.
    Здесь дергаем внешний API, фиксируем итоговый статус (SENT/FAILED) и шлём
    ``message_status`` фронту.
    """
    import requests
    from .models import WhatsAppMessageConsalting, WazzupAccountConsalting
    from .funnel.wazzup import WazzupConsaltingService, _broadcast_message_status
    from .funnel import realtime

    wa_message = (
        WhatsAppMessageConsalting.objects.select_related("lead")
        .filter(id=wa_message_id)
        .first()
    )
    account = WazzupAccountConsalting.objects.filter(id=account_id).first()
    if not wa_message or not account:
        logger.warning(
            "send_wazzup_message: missing wa_message=%s or account=%s", wa_message_id, account_id
        )
        return

    lead = wa_message.lead
    clean_phone = "".join(filter(str.isdigit, lead.phone or "")) if lead else ""

    url = f"{account.api_url.rstrip('/')}/v3/message"
    headers = {
        "Authorization": f"Bearer {account.api_key}",
        "Content-Type": "application/json",
    }
    api_payload = {
        "channelId": account.channel_id,
        "chatId": clean_phone,
        "chatType": account.integration_type,
        "text": text or "",
    }
    if content_uri:
        api_payload["contentUri"] = content_uri

    try:
        res = requests.post(url, json=api_payload, headers=headers, timeout=10.0)
        if res.status_code in (200, 201):
            data = res.json()
            wz_id = data.get("messageId") or data.get("id")
            if wz_id:
                wa_message.message_id = str(wz_id)
            wa_message.status = WhatsAppMessageConsalting.Status.SENT
            wa_message.save(update_fields=["message_id", "status"])
            try:
                WazzupConsaltingService.mark_chat_read(account, clean_phone)
            except Exception as e:
                logger.warning("mark_chat_read failed: %s", e)
        else:
            logger.error("Wazzup API Error: %s %s", res.status_code, res.text)
            wa_message.status = WhatsAppMessageConsalting.Status.FAILED
            wa_message.save(update_fields=["status"])
    except Exception as e:
        logger.error("Ошибка вызова Wazzup API: %s", e)
        wa_message.status = WhatsAppMessageConsalting.Status.FAILED
        wa_message.save(update_fields=["status"])

    _broadcast_message_status(account.company_id, wa_message, clean_phone)
    if lead:
        realtime.lead_updated(lead)


def _active_open_leads():
    return LeadConsalting.objects.filter(
        stage__stage_type__in=ACTIVE_TYPES, closed_at__isnull=True,
    ).exclude(status__in=CLOSED_STATUSES)


@shared_task
def scan_no_activity():
    """Лиды без активности дольше порога → событие no_activity (движок решает действия).

    Грубый floor=24ч отбирает кандидатов дёшево; точный порог каждого правила
    (`conditions.hours`) проверяется в conditions.match внутри движка.
    """
    now = timezone.now()
    fired = 0
    company_ids = list(
        AutomationRuleConsalting.objects.filter(
            is_active=True, trigger=AutomationRuleConsalting.Trigger.NO_ACTIVITY
        ).values_list("company_id", flat=True).distinct()
    )
    if not company_ids:
        return 0

    floor = now - timedelta(hours=24)
    qs = _active_open_leads().filter(company_id__in=company_ids).filter(
        Q(last_activity_at__lt=floor) | Q(last_activity_at__isnull=True, created_at__lt=floor)
    ).iterator()

    for lead in qs:
        emit("no_activity", lead)
        fired += 1
    return fired


@shared_task
def scan_overdue_tasks():
    """Открытые задачи с истёкшим сроком → статус OVERDUE + событие task_overdue."""
    now = timezone.now()
    fired = 0
    overdue = LeadTaskConsalting.objects.filter(
        status=LeadTaskConsalting.Status.OPEN, due_date__lt=now
    ).select_related("lead").iterator()
    for task in overdue:
        LeadTaskConsalting.objects.filter(pk=task.pk).update(
            status=LeadTaskConsalting.Status.OVERDUE
        )
        emit("task_overdue", task.lead, task_id=str(task.id))
        fired += 1
    return fired


@shared_task
def scan_sla_breach():
    """Лиды, превысившие SLA текущей стадии → событие sla_breach."""
    now = timezone.now()
    fired = 0
    stages = {
        s.id: s.sla_hours
        for s in FunnelStageConsalting.objects.filter(sla_hours__isnull=False)
    }
    if not stages:
        return 0
    qs = _active_open_leads().filter(
        stage_id__in=list(stages.keys()), stage_entered_at__isnull=False
    ).iterator()
    for lead in qs:
        sla = stages.get(lead.stage_id)
        if sla and (now - lead.stage_entered_at) >= timedelta(hours=sla):
            emit("sla_breach", lead, sla_hours=sla)
            fired += 1
    return fired


@shared_task
def scan_unanswered_leads(threshold_minutes=15):
    """
    Проверка лидов, которым не ответили в течение N минут после входящего сообщения.
    """
    from .models import WhatsAppMessageConsalting
    from apps.main.models import Notification
    from apps.users.models import User
    from apps.main.realtime import create_and_publish_notification

    now = timezone.now()
    threshold = now - timedelta(minutes=threshold_minutes)
    fired = 0

    open_leads = LeadConsalting.objects.filter(
        closed_at__isnull=True
    ).exclude(
        status__in=[LeadConsalting.Status.WON, LeadConsalting.Status.LOST]
    ).select_related("company", "owner").iterator()

    for lead in open_leads:
        last_msg = (
            WhatsAppMessageConsalting.objects.filter(lead=lead)
            .order_by("-created_at")
            .first()
        )
        if last_msg and last_msg.direction == WhatsAppMessageConsalting.Direction.INBOUND:
            if last_msg.created_at <= threshold:
                # Исключаем спам: проверяем, не отправлялось ли аналогичное алерт-уведомление за последние 15 мин
                recent_notif = Notification.objects.filter(
                    company=lead.company,
                    type="unanswered_lead_alert",
                    data__lead_id=str(lead.id),
                    created_at__gte=now - timedelta(minutes=15)
                ).exists()

                if recent_notif:
                    continue

                target_users = []
                if lead.owner:
                    target_users = [lead.owner]
                else:
                    target_users = list(User.objects.filter(company=lead.company, is_active=True))

                for u in target_users:
                    try:
                        create_and_publish_notification(
                            company=lead.company,
                            user=u,
                            title=f"⏰ Внимание: Лид без ответа > {threshold_minutes} мин!",
                            message=f"Клиент {lead.full_name} ({lead.phone}) ожидает вашего ответа более {threshold_minutes} минут.",
                            type="unanswered_lead_alert",
                            level="warning",
                            url=f"/consalting/leads/{lead.id}",
                            data={"lead_id": str(lead.id), "phone": lead.phone}
                        )
                    except Exception:
                        pass
                fired += 1
    return fired
