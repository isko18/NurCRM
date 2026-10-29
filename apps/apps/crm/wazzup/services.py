import logging
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.crm.models import WazzupAccount, WazzupMessage, Contact, Lead, SalesFunnel, FunnelStage
from .client import WazzupClient

logger = logging.getLogger(__name__)


def broadcast_ws(channel_group_name: str, event_type: str, data: dict):
    """
    Отправка события по WebSocket в Django Channels
    """
    channel_layer = get_channel_layer()
    if channel_layer:
        try:
            async_to_sync(channel_layer.group_send)(
                channel_group_name,
                {
                    "type": "wazzup_event",
                    "event": {
                        "type": event_type,
                        "data": data,
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to broadcast WebSocket event: {e}")


def send_message_service(account: WazzupAccount, to_phone: str, text: str, media_url: str = None):
    """
    Отправка исходящего сообщения через Wazzup API
    """
    client = WazzupClient(account.api_key, account.api_url)
    
    # 1. Отправляем в Wazzup API
    res = client.send_message(
        channel_id=account.channel_id,
        chat_id=to_phone.lstrip("+"),
        chat_type=account.integration_type,
        text=text,
        content_uri=media_url,
    )
    
    message_id = res.get("messageId") or res.get("id") or f"msg_{timezone.now().timestamp()}"

    # 2. Находим или создаем контакт
    contact, _ = Contact.objects.get_or_create(
        company=account.company,
        phone=to_phone,
        defaults={
            "first_name": to_phone,
            "whatsapp": to_phone,
            "source": f"Wazzup ({account.integration_type})",
        }
    )

    # 3. Сохраняем сообщение в БД
    msg = WazzupMessage.objects.create(
        account=account,
        contact=contact,
        message_id=message_id,
        chat_id=to_phone,
        chat_type=account.integration_type,
        is_incoming=False,
        text=text,
        media_url=media_url,
        status="sent",
        timestamp=timezone.now(),
    )

    # 4. Отправляем событие по WebSocket
    payload = {
        "id": str(msg.id),
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
        "text": msg.text,
        "media_url": msg.media_url,
        "is_incoming": False,
        "status": msg.status,
        "timestamp": msg.timestamp.isoformat(),
    }
    broadcast_ws(f"wazzup_company_{account.company_id}", "new_message", payload)
    broadcast_ws(f"wazzup_chat_{to_phone}", "new_message", payload)

    return msg


def process_webhook_payload(payload: dict):
    """
    Обработка входящих вебхуков от Wazzup (сообщения и статусы)
    """
    messages = payload.get("messages") or []
    statuses = payload.get("statuses") or []

    # 1. Обработка входящих/исходящих сообщений
    for item in messages:
        channel_id = item.get("channelId")
        account = WazzupAccount.objects.filter(channel_id=channel_id, is_active=True).first()
        if not account:
            continue

        message_id = item.get("messageId")
        chat_id = item.get("chatId") or item.get("author") or ""
        text = item.get("text") or ""
        content_uri = item.get("contentUri")
        is_incoming = item.get("isInbound", True)
        status = item.get("status", "sent")

        # Поиск или создание контакта
        phone = f"+{chat_id}" if not chat_id.startswith("+") else chat_id
        contact, contact_created = Contact.objects.get_or_create(
            company=account.company,
            phone=phone,
            defaults={
                "first_name": item.get("authorName") or phone,
                "whatsapp": phone,
                "source": f"Wazzup ({account.integration_type})",
            }
        )

        # Автоматическое создание Лида для первого обращения
        lead = None
        if contact_created:
            default_funnel = SalesFunnel.objects.filter(company=account.company, is_active=True).first()
            if default_funnel:
                default_stage = FunnelStage.objects.filter(funnel=default_funnel).first()
                lead = Lead.objects.create(
                    company=account.company,
                    contact=contact,
                    funnel=default_funnel,
                    stage=default_stage,
                    title=f"Заявка из Wazzup ({phone})",
                    source=f"Wazzup ({account.integration_type})",
                )

        msg, _ = WazzupMessage.objects.update_or_create(
            message_id=message_id,
            defaults={
                "account": account,
                "contact": contact,
                "lead": lead or contact.leads.first(),
                "chat_id": phone,
                "chat_type": item.get("chatType", account.integration_type),
                "is_incoming": is_incoming,
                "text": text,
                "media_url": content_uri,
                "status": status,
                "timestamp": timezone.now(),
            }
        )

        # WebSocket broadcast
        msg_payload = {
            "id": str(msg.id),
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "text": msg.text,
            "media_url": msg.media_url,
            "is_incoming": msg.is_incoming,
            "status": msg.status,
            "timestamp": msg.timestamp.isoformat(),
            "contact_name": contact.full_name,
        }
        broadcast_ws(f"wazzup_company_{account.company_id}", "new_message", msg_payload)
        broadcast_ws(f"wazzup_chat_{phone}", "new_message", msg_payload)

    # 2. Обработка обновлений статуса сообщений
    for item in statuses:
        message_id = item.get("messageId")
        new_status = item.get("status")
        if message_id and new_status:
            msg = WazzupMessage.objects.filter(message_id=message_id).first()
            if msg:
                msg.status = new_status
                msg.save(update_fields=["status"])
                
                status_payload = {
                    "id": str(msg.id),
                    "message_id": msg.message_id,
                    "chat_id": msg.chat_id,
                    "status": msg.status,
                }
                broadcast_ws(f"wazzup_company_{msg.account.company_id}", "message_status", status_payload)
