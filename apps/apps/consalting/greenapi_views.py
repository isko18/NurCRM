import uuid
import logging
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import (
    WazzupAccountConsalting,
    LeadConsalting,
    InboundLeadConsalting,
    WhatsAppMessageConsalting,
)
from .funnel.wazzup import chat_events_group, _normalize_phone
from .funnel import realtime

logger = logging.getLogger(__name__)


class GreenApiWebhookConsaltingView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        type_webhook = payload.get("typeWebhook")
        instance_data = payload.get("instanceData") or {}
        id_instance = str(instance_data.get("idInstance") or "")

        logger.info("GreenAPI Webhook received: type=%s, instance=%s", type_webhook, id_instance)

        if not id_instance:
            return Response({"status": "ignored", "reason": "no_instance_id"}, status=status.HTTP_200_OK)

        account = WazzupAccountConsalting.objects.filter(
            green_api_id_instance=id_instance
        ).first()

        if not account:
            account = WazzupAccountConsalting.objects.filter(is_active=True).first()

        company = account.company if account else None
        company_id = company.id if company else None

        if type_webhook in ("incomingMessageReceived", "outgoingAPIMessageReceived", "outgoingMessageReceived"):
            id_message = payload.get("idMessage") or str(uuid.uuid4())
            sender_data = payload.get("senderData") or {}
            chat_id = sender_data.get("chatId") or sender_data.get("sender") or ""

            if not chat_id:
                chat_id = payload.get("chatId") or ""

            raw_phone = chat_id.split("@")[0]
            clean_phone = _normalize_phone(raw_phone)
            sender_name = sender_data.get("senderName") or sender_data.get("chatName") or clean_phone
            if "556900556" in sender_name or "556 900 556" in sender_name: sender_name = clean_phone

            message_data = payload.get("messageData") or {}
            type_message = message_data.get("typeMessage") or ""

            text = ""
            content_uri = None
            media_type = None

            if "textMessageData" in message_data:
                text = (message_data["textMessageData"].get("textMessage") or "").strip()
            elif "extendedTextMessageData" in message_data:
                text = (message_data["extendedTextMessageData"].get("text") or "").strip()
            elif "fileMessageData" in message_data:
                file_data = message_data["fileMessageData"]
                content_uri = file_data.get("downloadUrl")
                text = (file_data.get("caption") or file_data.get("fileName") or "").strip()
                media_type = "document"
            elif "imageMessageData" in message_data:
                img_data = message_data["imageMessageData"]
                content_uri = img_data.get("downloadUrl")
                text = (img_data.get("caption") or "📷 [Фотография]").strip()
                media_type = "image"
            elif "audioMessageData" in message_data:
                aud_data = message_data["audioMessageData"]
                content_uri = aud_data.get("downloadUrl")
                text = "🎵 [Аудиозапись]"
                media_type = "audio"
            elif "voiceMessageData" in message_data:
                voice_data = message_data["voiceMessageData"]
                content_uri = voice_data.get("downloadUrl")
                text = "🎤 [Голосовое сообщение]"
                media_type = "audio"
            elif "videoMessageData" in message_data:
                vid_data = message_data["videoMessageData"]
                content_uri = vid_data.get("downloadUrl")
                text = (vid_data.get("caption") or "🎥 [Видео]").strip()
                media_type = "video"
            elif "documentMessageData" in message_data:
                doc_data = message_data["documentMessageData"]
                content_uri = doc_data.get("downloadUrl")
                text = (doc_data.get("caption") or doc_data.get("fileName") or "📄 [Документ]").strip()
                media_type = "document"
            elif "locationMessageData" in message_data:
                loc_data = message_data["locationMessageData"]
                text = "📍 [Локация]"
            elif "contactMessageData" in message_data:
                cnt_data = message_data["contactMessageData"]
                text = "👤 [Контакт]"

            if not text and not content_uri:
                text = f"[{type_message}]" if type_message else "[Новое сообщение]"

            direction = (
                WhatsAppMessageConsalting.Direction.OUTBOUND
                if type_webhook in ("outgoingAPIMessageReceived", "outgoingMessageReceived")
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

                if not lead:
                    from .funnel.regional_routing import resolve_funnel_and_assignee
                    from .models import FunnelConsalting, FunnelStageConsalting

                    reg_funnel, reg_stage, reg_rule, reg_user = resolve_funnel_and_assignee(
                        company=company,
                        phone=clean_phone,
                        source="whatsapp"
                    )

                    funnel = reg_funnel or FunnelConsalting.objects.filter(company_id=company_id).first()
                    if not funnel:
                        funnel = FunnelConsalting.objects.create(
                            company_id=company_id,
                            name="Воронка консалтинга"
                        )

                    stage = reg_stage or FunnelStageConsalting.objects.filter(funnel=funnel).order_by("order").first()
                    if not stage:
                        stage = FunnelStageConsalting.objects.create(
                            company_id=company_id,
                            funnel=funnel,
                            name="Первичный контакт",
                            order=1,
                            stage_type=FunnelStageConsalting.StageType.NEW
                        )

                    lead = LeadConsalting.objects.create(
                        company_id=company_id,
                        branch_id=account.branch_id if account else None,
                        funnel=funnel,
                        stage=stage,
                        owner=reg_user,
                        title=sender_name or clean_phone,
                        full_name=sender_name or clean_phone,
                        phone=clean_phone,
                        source="whatsapp",
                    )

            if lead:
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
                                "lead_id": str(lead.id),
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

                try:
                    realtime.lead_updated(lead)
                except Exception:
                    pass

        return Response({"status": "ok"}, status=status.HTTP_200_OK)
