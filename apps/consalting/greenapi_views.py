import logging
import uuid
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.main.models import Company
from .models import (
    WazzupAccountConsalting,
    WhatsAppMessageConsalting,
    InboundLeadConsalting,
    LeadConsalting,
)
from .funnel.wazzup import chat_events_group, _normalize_phone
from .funnel import realtime
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


class GreenApiWebhookConsaltingView(APIView):
    """Вебхук для приема входящих событий и сообщений от GREEN-API."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        type_webhook = payload.get("typeWebhook")
        instance_data = payload.get("instanceData") or {}
        id_instance = str(instance_data.get("idInstance") or "")

        logger.info("GreenAPI Webhook received: type=%s, instance=%s", type_webhook, id_instance)

        if not id_instance:
            return Response({"status": "ignored", "reason": "no_instance_id"}, status=status.HTTP_200_OK)

        # Находим аккаунт WazzupAccountConsalting по green_api_id_instance
        account = WazzupAccountConsalting.objects.filter(
            green_api_id_instance=id_instance
        ).first()

        if not account:
            account = WazzupAccountConsalting.objects.filter(is_active=True).first()

        company = account.company if account else None
        company_id = company.id if company else None

        if type_webhook in ("incomingMessageReceived", "outgoingAPIMessageReceived"):
            id_message = payload.get("idMessage") or str(uuid.uuid4())
            sender_data = payload.get("senderData") or {}
            chat_id = sender_data.get("chatId") or sender_data.get("sender") or ""
            raw_phone = chat_id.split("@")[0]
            clean_phone = _normalize_phone(raw_phone)
            sender_name = sender_data.get("senderName") or sender_data.get("chatName") or clean_phone

            message_data = payload.get("messageData") or {}
            type_message = message_data.get("typeMessage") or ""

            text = ""
            content_uri = None
            media_type = None

            if "textMessageData" in message_data:
                text = (message_data["textMessageData"].get("textMessage") or "").strip()
            elif "fileMessageData" in message_data:
                file_data = message_data["fileMessageData"]
                content_uri = file_data.get("downloadUrl")
                text = (file_data.get("caption") or file_data.get("fileName") or "").strip()
                media_type = "document"
            elif "extendedTextMessageData" in message_data:
                text = (message_data["extendedTextMessageData"].get("text") or "").strip()

            if not text and not content_uri:
                text = f"[{type_message}]"

            direction = (
                WhatsAppMessageConsalting.Direction.OUTBOUND
                if type_webhook == "outgoingAPIMessageReceived"
                else WhatsAppMessageConsalting.Direction.INBOUND
            )

            lead = None
            if company_id and clean_phone:
                clean_phone_10 = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
                lead = LeadConsalting.objects.filter(
                    company_id=company_id,
                    phone__icontains=clean_phone_10
                ).first()

                InboundLeadConsalting.objects.update_or_create(
                    company_id=company_id,
                    phone=clean_phone,
                    defaults={
                        "name": sender_name,
                        "source": "GreenAPI (whatsapp)",
                        "message": text or "[Вложение]",
                        "updated_at": timezone.now(),
                    }
                )

            wa_msg, created = WhatsAppMessageConsalting.objects.update_or_create(
                message_id=str(id_message),
                defaults={
                    "company_id": company_id,
                    "lead": lead,
                    "direction": direction,
                    "text": text,
                    "content_uri": content_uri,
                    "media_type": media_type,
                    "status": WhatsAppMessageConsalting.Status.READ if direction == "outbound" else WhatsAppMessageConsalting.Status.DELIVERED,
                    "provider": "greenapi",
                }
            )

            channel_layer = get_channel_layer()
            if channel_layer and company_id:
                event_group = chat_events_group(company_id)
                msg_payload = {
                    "type": "wazzup_event",
                    "event": {
                        "type": "new_message",
                        "data": {
                            "id": str(wa_msg.id),
                            "message_id": wa_msg.message_id,
                            "lead_id": str(lead.id) if lead else "",
                            "phone": clean_phone,
                            "name": sender_name,
                            "direction": direction,
                            "text": text,
                            "content_uri": content_uri,
                            "status": wa_msg.status,
                            "provider": "greenapi",
                            "timestamp": timezone.now().isoformat(),
                        }
                    }
                }
                async_to_sync(channel_layer.group_send)(event_group, msg_payload)

            if lead:
                try:
                    realtime.lead_updated(lead)
                except Exception:
                    pass

        return Response({"status": "ok"}, status=status.HTTP_200_OK)
