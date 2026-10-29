import logging
import uuid
import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from apps.main.models import Company
from apps.users.models import User, Branch
from ..models import (
    LeadConsalting,
    WhatsAppMessageConsalting,
    LeadActivityConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
)
from .activity import ActivityLogger
from . import events
from . import realtime

logger = logging.getLogger(__name__)


class WhatsAppConsaltingService:
    """
    Сервис для работы с WhatsApp в рамках воронки консалтинга.
    Поддерживает Meta WhatsApp Cloud API, внешний Node.js шлюз и локальный режим.
    """

    @staticmethod
    def send_message(lead: LeadConsalting, text: str, user: User = None) -> WhatsAppMessageConsalting:
        """
        Отправка сообщения клиенту через WhatsApp.
        1. Создает запись сообщения в статусе PENDING.
        2. Добавляет запись в неизменяемую ленту активностей лида.
        3. Вызывает Meta Cloud API или внешний Node.js шлюз для реальной отправки.
        """
        if not lead.phone:
            raise ValueError("У лида не указан номер телефона.")

        message_id = f"wa_out_{uuid.uuid4().hex[:12]}_{int(timezone.now().timestamp())}"
        
        with transaction.atomic():
            # 1. Создаем сообщение в БД
            wa_message = WhatsAppMessageConsalting.objects.create(
                company_id=lead.company_id,
                branch_id=lead.branch_id,
                lead=lead,
                message_id=message_id,
                direction=WhatsAppMessageConsalting.Direction.OUTBOUND,
                text=text,
                status=WhatsAppMessageConsalting.Status.PENDING
            )

            # 2. Логируем в таймлайн лида
            ActivityLogger.log(
                lead=lead,
                activity_type=LeadActivityConsalting.Type.MESSAGE,
                actor=user,
                title="WhatsApp (исходящее)",
                body=text,
                payload={
                    "direction": "outbound",
                    "message_id": message_id,
                    "status": "pending"
                }
            )

        # 3. Реальная отправка через Meta Cloud API или Node Gateway
        access_token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", None) or getattr(settings, "META_WA_ACCESS_TOKEN", None)
        phone_number_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None) or getattr(settings, "META_WA_PHONE_NUMBER_ID", None)
        node_url = getattr(settings, "WHATSAPP_NODE_URL", None)

        clean_phone = "".join(filter(str.isdigit, lead.phone))

        try:
            if access_token and phone_number_id:
                # Отправка через Meta WhatsApp Cloud API
                url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": clean_phone,
                    "type": "text",
                    "text": {"preview_url": False, "body": text}
                }
                logger.info(f"Отправка WhatsApp через Meta Cloud API для lead={lead.id}, phone={clean_phone}")
                res = requests.post(url, json=payload, headers=headers, timeout=10.0)
                if res.status_code in (200, 201):
                    res_data = res.json()
                    meta_msg_id = res_data.get("messages", [{}])[0].get("id")
                    if meta_msg_id:
                        wa_message.message_id = meta_msg_id
                    wa_message.status = WhatsAppMessageConsalting.Status.SENT
                    wa_message.save(update_fields=["message_id", "status"])
                else:
                    logger.error(f"Meta WhatsApp API error: {res.status_code} {res.text}")
                    wa_message.status = WhatsAppMessageConsalting.Status.FAILED
                    wa_message.save(update_fields=["status"])

            elif node_url:
                # Отправка через Node.js шлюз
                token = getattr(settings, "WHATSAPP_NODE_TOKEN", "change-me")
                payload = {
                    "phone": lead.phone,
                    "message": text,
                    "message_id": message_id,
                    "company_id": str(lead.company_id)
                }
                headers = {"X-WA-TOKEN": token}
                logger.info(f"Отправка сообщения через WhatsApp Node Gateway: lead={lead.id}, phone={lead.phone}")
                res = requests.post(f"{node_url.rstrip('/')}/api/send", json=payload, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    wa_message.status = WhatsAppMessageConsalting.Status.SENT
                    wa_message.save(update_fields=["status"])
                else:
                    wa_message.status = WhatsAppMessageConsalting.Status.FAILED
                    wa_message.save(update_fields=["status"])
            else:
                # Режим разработки / Симуляция
                logger.info(f"Имитация успешной отправки WhatsApp: lead={lead.id}, phone={lead.phone}")
                wa_message.status = WhatsAppMessageConsalting.Status.SENT
                wa_message.save(update_fields=["status"])

        except Exception as e:
            logger.error(f"Ошибка вызова WhatsApp API/шлюза: {e}")
            wa_message.status = WhatsAppMessageConsalting.Status.FAILED
            wa_message.save(update_fields=["status"])

        return wa_message

    @staticmethod
    def handle_incoming_message(company_id: uuid.UUID, phone: str, text: str, message_id: str) -> LeadConsalting:
        """
        Обработка входящего сообщения от клиента.
        1. Ищет активный лид для этого номера телефона.
        2. Если лид не найден, создает новый лид в первой воронке и первой стадии.
        3. Создает запись входящего сообщения.
        4. Добавляет входящее сообщение в ленту активностей лида.
        5. Триггерит события воронки для автоматизации и отправляет WS-уведомление.
        """
        company = get_object_or_404(Company, id=company_id)
        
        clean_phone = "".join(filter(str.isdigit, phone))
        if clean_phone.startswith("8") and len(clean_phone) == 11:
            clean_phone = "7" + clean_phone[1:]
        elif not clean_phone.startswith("7") and len(clean_phone) == 10:
            clean_phone = "7" + clean_phone

        # Ищем активный лид (не WON, COMPLETED, LOST)
        lead = LeadConsalting.objects.filter(
            company_id=company_id,
            phone__icontains=clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
        ).exclude(
            stage__stage_type__in=[
                FunnelStageConsalting.StageType.WON,
                FunnelStageConsalting.StageType.COMPLETED,
                FunnelStageConsalting.StageType.LOST
            ]
        ).order_by("-updated_at").first()

        created_lead = False
        if not lead:
            funnel = FunnelConsalting.objects.filter(company_id=company_id).first()
            if not funnel:
                funnel = FunnelConsalting.objects.create(
                    company_id=company_id,
                    name="Основная воронка консалтинга"
                )
            
            stage = FunnelStageConsalting.objects.filter(funnel=funnel).order_by("order").first()
            if not stage:
                stage = FunnelStageConsalting.objects.create(
                    company_id=company_id,
                    funnel=funnel,
                    name="Новый лид",
                    stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
                    order=100
                )

            lead = LeadConsalting.objects.create(
                company_id=company_id,
                funnel=funnel,
                stage=stage,
                title=f"Лид из WhatsApp ({phone})",
                phone=phone,
                full_name=f"WhatsApp {phone}",
                status=LeadConsalting.Status.NEW
            )
            created_lead = True

        with transaction.atomic():
            wa_message, msg_created = WhatsAppMessageConsalting.objects.get_or_create(
                message_id=message_id,
                defaults={
                    "company_id": lead.company_id,
                    "branch_id": lead.branch_id,
                    "lead": lead,
                    "direction": WhatsAppMessageConsalting.Direction.INBOUND,
                    "text": text,
                    "status": WhatsAppMessageConsalting.Status.READ
                }
            )

            if msg_created:
                ActivityLogger.log(
                    lead=lead,
                    activity_type=LeadActivityConsalting.Type.MESSAGE,
                    actor=None,
                    title="WhatsApp (входящее)",
                    body=text,
                    payload={
                        "direction": "inbound",
                        "message_id": message_id,
                        "status": "read"
                    }
                )

        # Вызываем триггер события автоматизации
        events.emit(
            trigger="activity_added" if not created_lead else "stage_changed",
            lead=lead,
            actor=None,
            ctx={"is_whatsapp": True, "message_id": message_id}
        )

        # Real-time WebSocket трансляция
        if created_lead:
            realtime.lead_created(lead)
        else:
            realtime.lead_updated(lead)

        return lead

    @staticmethod
    def update_message_status(message_id: str, status_str: str):
        """
        Обновление статуса доставки сообщения (вебхуки от шлюза/Meta API).
        """
        status_map = {
            "sent": WhatsAppMessageConsalting.Status.SENT,
            "delivered": WhatsAppMessageConsalting.Status.DELIVERED,
            "read": WhatsAppMessageConsalting.Status.READ,
            "failed": WhatsAppMessageConsalting.Status.FAILED,
        }
        target_status = status_map.get(status_str.lower())
        if target_status:
            WhatsAppMessageConsalting.objects.filter(message_id=message_id).update(
                status=target_status,
                updated_at=timezone.now()
            )

