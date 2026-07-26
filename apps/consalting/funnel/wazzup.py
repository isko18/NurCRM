import logging
import uuid
import requests
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.main.models import Company
from apps.users.models import User, Branch
from ..models import (
    LeadConsalting,
    InboundLeadConsalting,
    WhatsAppMessageConsalting,
    WazzupAccountConsalting,
    LeadActivityConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
)
from .activity import ActivityLogger
from . import events
from . import realtime

logger = logging.getLogger(__name__)


def _broadcast_consalting_message(company_id, lead, wa_message, is_inbound, text, phone):
    """
    Мгновенная трансляция события сообщения по WebSocket без задержек.
    """
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    layer = get_channel_layer()
    if not layer:
        return

    msg_payload = {
        "id": str(wa_message.id),
        "message_id": wa_message.message_id,
        "lead_id": str(lead.id),
        "chat_id": phone,
        "text": text,
        "is_incoming": is_inbound,
        "direction": "inbound" if is_inbound else "outbound",
        "status": wa_message.status,
        "timestamp": timezone.now().isoformat(),
        "contact_name": lead.full_name,
    }

    event_envelope = {
        "type": "wazzup_event",
        "event": {
            "type": "new_message",
            "data": msg_payload
        }
    }

    groups = [
        f"consalting_company_{company_id}",
        f"wazzup_company_{company_id}",
        f"wazzup_chat_{phone}"
    ]

    for g in groups:
        try:
            async_to_sync(layer.group_send)(g, event_envelope)
        except Exception as e:
            logger.warning("Failed to broadcast websocket event to %s: %s", g, e)


class WazzupConsaltingService:
    """
    Интеграционный сервис Wazzup API v3 для воронки консалтинга.
    https://api.wazzup24.com/v3
    """

    @staticmethod
    def mark_chat_read(account: WazzupAccountConsalting, phone: str):
        """
        Сброс непрочитанных в Wazzup API (PATCH /v3/chats unread=0).
        Проставляет синие галочки в WhatsApp и снимает счётчик непрочитанных на телефоне.
        """
        if not phone:
            return
        clean_phone = "".join(filter(str.isdigit, phone))
        url = f"{account.api_url.rstrip('/')}/v3/chats"
        headers = {
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json"
        }
        payload = [
            {
                "chatId": clean_phone,
                "chatType": account.integration_type or "whatsapp",
                "unread": 0
            }
        ]
        try:
            res = requests.patch(url, json=payload, headers=headers, timeout=5.0)
            logger.info(f"Wazzup chat {clean_phone} marked read (unread=0): status={res.status_code}")
        except Exception as e:
            logger.warning(f"Failed to mark chat read in Wazzup: {e}")

    @staticmethod
    def send_message(account: WazzupAccountConsalting, lead: LeadConsalting, text: str, user: User = None, content_uri: str = None) -> WhatsAppMessageConsalting:
        """
        Отправка исходящего сообщения в Wazzup API (POST /v3/message)
        """
        if not lead.phone:
            raise ValueError("У лида не указан номер телефона.")

        if user:
            from apps.consalting.access import is_owner_like
            if lead.owner_id and lead.owner_id != user.id and not is_owner_like(user):
                raise ValueError("Отправлять сообщения лиду может только назначенный сотрудник или руководитель.")

        clean_phone = "".join(filter(str.isdigit, lead.phone))
        message_id = f"wz_out_{uuid.uuid4().hex[:12]}_{int(timezone.now().timestamp())}"

        with transaction.atomic():
            wa_message = WhatsAppMessageConsalting.objects.create(
                company_id=lead.company_id,
                branch_id=lead.branch_id,
                lead=lead,
                message_id=message_id,
                direction=WhatsAppMessageConsalting.Direction.OUTBOUND,
                text=text,
                status=WhatsAppMessageConsalting.Status.PENDING
            )

            # Мгновенная сокет-трансляция (0ms)
            _broadcast_consalting_message(account.company_id, lead, wa_message, False, text, clean_phone)

            ActivityLogger.log(
                lead=lead,
                activity_type=LeadActivityConsalting.Type.MESSAGE,
                actor=user,
                title="Wazzup (исходящее)",
                body=text,
                payload={
                    "direction": "outbound",
                    "message_id": message_id,
                    "status": "pending",
                    "channel_id": account.channel_id,
                }
            )

        # Вызов Wazzup API /v3/message
        url = f"{account.api_url.rstrip('/')}/v3/message"
        headers = {
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "channelId": account.channel_id,
            "chatId": clean_phone,
            "chatType": account.integration_type,
            "text": text,
        }
        if content_uri:
            payload["contentUri"] = content_uri

        try:
            res = requests.post(url, json=payload, headers=headers, timeout=12.0)
            if res.status_code in (200, 201):
                data = res.json()
                wz_id = data.get("messageId") or data.get("id")
                if wz_id:
                    wa_message.message_id = str(wz_id)
                wa_message.status = WhatsAppMessageConsalting.Status.SENT
                wa_message.save(update_fields=["message_id", "status"])
                # Сбрасываем счётчик непрочитанных в Wazzup для галочек в WhatsApp
                WazzupConsaltingService.mark_chat_read(account, clean_phone)
            else:
                logger.error(f"Wazzup API Error: {res.status_code} {res.text}")
                wa_message.status = WhatsAppMessageConsalting.Status.FAILED
                wa_message.save(update_fields=["status"])
        except Exception as e:
            logger.error(f"Ошибка вызова Wazzup API: {e}")
            wa_message.status = WhatsAppMessageConsalting.Status.FAILED
            wa_message.save(update_fields=["status"])

        # Обновляем канбан воронку через WebSocket
        realtime.lead_updated(lead)

        return wa_message

    @staticmethod
    def handle_wazzup_webhook(payload: dict):
        """
        Обработка входящих сообщений и статусов от Wazzup Webhook.
        Атомарность, идемпотентность (external_id), автораспределение (Round-Robin/Least-Loaded) и персональные WS-уведомления.
        """
        from apps.consalting.views import distribute_inbound_lead

        messages = payload.get("messages") or []
        statuses = payload.get("statuses") or []

        # 1. Обработка входящих/исходящих сообщений
        for item in messages:
            channel_id = item.get("channelId")
            account = WazzupAccountConsalting.objects.filter(channel_id=channel_id, is_active=True).first()
            if not account:
                logger.warning(f"Wazzup account not found or inactive for channelId={channel_id}")
                continue

            message_id = str(item.get("messageId") or "").strip()
            chat_id = item.get("chatId") or item.get("author") or ""
            text = item.get("text") or ""
            is_inbound = item.get("isInbound", True)
            author_name = item.get("authorName") or ""

            # Защита от дублей по message_id
            if message_id and WhatsAppMessageConsalting.objects.filter(message_id=message_id).exists():
                logger.info(f"Wazzup duplicate webhook ignored for message_id={message_id}")
                continue

            clean_phone = "".join(filter(str.isdigit, chat_id))
            if clean_phone.startswith("8") and len(clean_phone) == 11:
                clean_phone = "7" + clean_phone[1:]
            elif not clean_phone.startswith("7") and len(clean_phone) == 10:
                clean_phone = "7" + clean_phone

            phone = f"+{clean_phone}" if not clean_phone.startswith("+") else clean_phone
            source_name = f"Wazzup ({account.integration_type})"

            with transaction.atomic():
                # Идемпотентная привязка/обновление входящей заявки (InboundLeadConsalting) по номеру телефона
                clean_phone_10 = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
                inbound_lead = InboundLeadConsalting.objects.filter(
                    company=account.company,
                    phone__icontains=clean_phone_10
                ).exclude(
                    status__in=[InboundLeadConsalting.Status.CONVERTED, InboundLeadConsalting.Status.REJECTED]
                ).order_by("-created_at").first()

                inbound_created = False
                if inbound_lead:
                    # Обновляем последнее сообщение в имеющейся заявке
                    inbound_lead.message = text
                    if author_name and author_name != phone:
                        inbound_lead.full_name = author_name
                    if message_id:
                        inbound_lead.external_id = message_id
                    inbound_lead.save(update_fields=["message", "full_name", "external_id", "updated_at"])
                else:
                    inbound_lead = InboundLeadConsalting.objects.create(
                        company=account.company,
                        external_id=message_id if message_id else f"wz_{uuid.uuid4().hex[:12]}",
                        full_name=author_name or phone or "Wazzup Клиент",
                        phone=phone,
                        source=source_name,
                        message=text,
                        status=InboundLeadConsalting.Status.NEW,
                    )
                    inbound_created = True

                # Автораспределение лида (Round-Robin / Least-Loaded)
                assigned_owner = None
                if inbound_created:
                    distributed_lead = distribute_inbound_lead(inbound_lead)
                    if distributed_lead and distributed_lead.owner:
                        assigned_owner = distributed_lead.owner

                # Быстрый поиск лида по точному совпадению телефона (индексированный запрос)
                lead = LeadConsalting.objects.filter(
                    company_id=account.company_id,
                    phone=phone
                ).exclude(
                    stage__stage_type__in=[
                        FunnelStageConsalting.StageType.WON,
                        FunnelStageConsalting.StageType.COMPLETED,
                        FunnelStageConsalting.StageType.LOST
                    ]
                ).order_by("-updated_at").first()

                if not lead and len(clean_phone) >= 10:
                    lead = LeadConsalting.objects.filter(
                        company_id=account.company_id,
                        phone__endswith=clean_phone[-10:]
                    ).exclude(
                        stage__stage_type__in=[
                            FunnelStageConsalting.StageType.WON,
                            FunnelStageConsalting.StageType.COMPLETED,
                            FunnelStageConsalting.StageType.LOST
                        ]
                    ).order_by("-updated_at").first()

                created_lead = False
                if not lead:
                    funnel = FunnelConsalting.objects.filter(company_id=account.company_id).first()
                    if not funnel:
                        funnel = FunnelConsalting.objects.create(
                            company_id=account.company_id,
                            branch_id=account.branch_id,
                            name="Воронка консалтинга"
                        )

                    stage = FunnelStageConsalting.objects.filter(funnel=funnel).order_by("order").first()
                    if not stage:
                        stage = FunnelStageConsalting.objects.create(
                            company_id=account.company_id,
                            funnel=funnel,
                            name="Новый лид",
                            stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
                            order=100
                        )

                    lead = LeadConsalting.objects.create(
                        company_id=account.company_id,
                        branch_id=account.branch_id,
                        funnel=funnel,
                        stage=stage,
                        owner=assigned_owner,
                        title=f"Заявка из {account.get_integration_type_display()} ({phone})",
                        phone=phone,
                        full_name=author_name or f"Клиент {phone}",
                        status=LeadConsalting.Status.NEW
                    )
                    created_lead = True

                direction = (
                    WhatsAppMessageConsalting.Direction.INBOUND
                    if is_inbound else
                    WhatsAppMessageConsalting.Direction.OUTBOUND
                )

                effective_msg_id = message_id if message_id else f"msg_{uuid.uuid4().hex[:12]}"
                wa_message, msg_created = WhatsAppMessageConsalting.objects.get_or_create(
                    message_id=effective_msg_id,
                    defaults={
                        "company_id": lead.company_id,
                        "branch_id": lead.branch_id,
                        "lead": lead,
                        "direction": direction,
                        "text": text,
                        "status": WhatsAppMessageConsalting.Status.READ if is_inbound else WhatsAppMessageConsalting.Status.SENT
                    }
                )

                # Мгновенная трансляция нового сообщения по WebSocket (0ms задержка)
                _broadcast_consalting_message(account.company_id, lead, wa_message, is_inbound, text, phone)

                if msg_created:
                    ActivityLogger.log(
                        lead=lead,
                        activity_type=LeadActivityConsalting.Type.MESSAGE,
                        actor=None,
                        title=f"Wazzup ({'входящее' if is_inbound else 'исходящее'})",
                        body=text,
                        payload={
                            "direction": "inbound" if is_inbound else "outbound",
                            "message_id": effective_msg_id,
                            "channel_id": channel_id,
                        }
                    )

            events.emit(
                trigger="activity_added" if not created_lead else "stage_changed",
                lead=lead,
                actor=None,
                ctx={"is_wazzup": True, "message_id": effective_msg_id}
            )

            if created_lead:
                realtime.lead_created(lead)
            else:
                realtime.lead_updated(lead)

            # Персональное системное уведомление менеджеру ("Сообщение от лида ...")
            if is_inbound:
                target_owner = assigned_owner or lead.owner
                from apps.main.realtime import create_and_publish_notification

                if target_owner:
                    logger.info(
                        "[WAZZUP NOTIF] Inbound msg lead_id=%s, target_user_id=%s, group=notif_user_%s",
                        lead.id, target_owner.id, target_owner.id
                    )
                    try:
                        create_and_publish_notification(
                            company=account.company,
                            user=target_owner,
                            title=f"📩 Сообщение от лида: {lead.full_name}",
                            message=text[:120] if text else "Входящее медиасообщение",
                            type="lead_message",
                            level="info",
                            url=f"/consalting/leads/{lead.id}",
                            data={
                                "lead_id": str(lead.id),
                                "phone": phone
                            }
                        )
                    except Exception as e:
                        logger.warning("Failed to publish lead message system notification: %s", e)
                else:
                    company_users = User.objects.filter(company=account.company, is_active=True)
                    logger.info(
                        "[WAZZUP NOTIF] Unassigned lead_id=%s, broadcasting notification to %d company users",
                        lead.id, company_users.count()
                    )
                    for u in company_users:
                        try:
                            create_and_publish_notification(
                                company=account.company,
                                user=u,
                                title=f"📩 Сообщение от лида: {lead.full_name}",
                                message=text[:120] if text else "Входящее медиасообщение",
                                type="lead_message",
                                level="info",
                                url=f"/consalting/leads/{lead.id}",
                                data={
                                    "lead_id": str(lead.id),
                                    "phone": phone
                                }
                            )
                        except Exception as e:
                            logger.warning("Failed to publish unassigned lead notification to user %s: %s", u.id, e)

                    realtime.notify_user(
                        target_owner.id,
                        "lead.message_received",
                        {
                            "id": str(lead.id),
                            "title": f"📩 Сообщение от лида: {lead.full_name}",
                            "message": text[:120] if text else "Входящее медиасообщение",
                            "full_name": lead.full_name,
                            "phone": lead.phone,
                            "lead_id": str(lead.id),
                            "created_at": timezone.now().isoformat(),
                        }
                    )

        # Обработка обновлений статусов сообщений
        for item in statuses:
            message_id = item.get("messageId")
            status_str = item.get("status")
            if message_id and status_str:
                status_map = {
                    "sent": WhatsAppMessageConsalting.Status.SENT,
                    "delivered": WhatsAppMessageConsalting.Status.DELIVERED,
                    "read": WhatsAppMessageConsalting.Status.READ,
                    "failed": WhatsAppMessageConsalting.Status.FAILED,
                }
                st = status_map.get(status_str.lower())
                if st:
                    msg = WhatsAppMessageConsalting.objects.filter(message_id=message_id).first()
                    if msg:
                        msg.status = st
                        msg.save(update_fields=["status"])
                        if msg.lead:
                            realtime.lead_updated(msg.lead)
