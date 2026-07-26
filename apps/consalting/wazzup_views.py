from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
import requests

from .models import WazzupAccountConsalting, LeadConsalting
from .funnel.wazzup import WazzupConsaltingService
from .serializers import WazzupAccountConsaltingSerializer, WhatsAppMessageConsaltingSerializer


class WazzupAccountConsaltingViewSet(viewsets.ModelViewSet):
    """
    Управление аккаунтами Wazzup в воронке консалтинга
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WazzupAccountConsaltingSerializer


    def get_queryset(self):
        return WazzupAccountConsalting.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

    @action(detail=True, methods=['post'], url_path='setup-webhook')
    def setup_webhook(self, request, pk=None):
        """
        Регистрация Webhook в Wazzup API v3
        """
        account = self.get_object()
        webhook_url = request.data.get('webhook_url') or "https://app.nurcrm.kg/api/consalting/wazzup/webhook/"

        url = f"{account.api_url.rstrip('/')}/v3/webhooks"
        headers = {
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "webhooksUri": webhook_url,
            "subscriptions": {
                "messagesAndStatuses": True
            }
        }
        try:
            res = requests.patch(url, json=payload, headers=headers, timeout=12.0)
            res.raise_for_status()
            account.is_connected = True
            account.save(update_fields=['is_connected'])
            return Response({"detail": "Webhook успешно привязан", "response": res.json()})
        except Exception as e:
            return Response({"detail": f"Ошибка регистрации Webhook: {e}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='send-message')
    def send_message(self, request, pk=None):
        """
        Отправка сообщения из воронки консалтинга по лиду
        """
        account = self.get_object()
        lead_id = request.data.get('lead_id')
        text = request.data.get('message') or request.data.get('text') or ""
        media_url = request.data.get('media_url')

        if not lead_id:
            return Response({"detail": "Укажите lead_id"}, status=status.HTTP_400_BAD_REQUEST)

        lead = LeadConsalting.objects.filter(company=self.request.user.company, id=lead_id).first()
        if not lead:
            return Response({"detail": "Лид не найден"}, status=status.HTTP_404_NOT_FOUND)

        try:
            wa_msg = WazzupConsaltingService.send_message(
                account=account,
                lead=lead,
                text=text,
                user=request.user,
                content_uri=media_url
            )
            return Response({
                "id": str(wa_msg.id),
                "message_id": wa_msg.message_id,
                "status": wa_msg.status,
                "text": wa_msg.text,
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WazzupWebhookConsaltingView(APIView):
    """
    Приемник исходящих событий (сообщений и статусов) от Wazzup Webhook.
    POST /api/consalting/wazzup/webhook/
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.data
        try:
            WazzupConsaltingService.handle_wazzup_webhook(payload)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WhatsAppMessageConsaltingViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Просмотр сообщений Wazzup/WhatsApp воронки консалтинга.
    Поддерживает фильтрацию по ?lead=<lead_id> или ?lead_id=<lead_id>
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WhatsAppMessageConsaltingSerializer
    pagination_class = None

    def get_queryset(self):
        from .models import WhatsAppMessageConsalting
        qs = WhatsAppMessageConsalting.objects.filter(company=self.request.user.company)
        lead_id = self.request.query_params.get('lead') or self.request.query_params.get('lead_id')
        if lead_id:
            qs = qs.filter(lead_id=lead_id)
        return qs.order_by('created_at')


class WazzupChatListView(APIView):
    """
    Полный список чатов/диалогов WhatsApp компании (как в мобильном WhatsApp).
    Объединяет все диалоги из WhatsAppMessageConsalting, LeadConsalting и InboundLeadConsalting.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get(self, request):
        from .access import is_owner_like
        from .models import LeadConsalting, InboundLeadConsalting, WhatsAppMessageConsalting
        from django.db.models import Q

        user = request.user
        company = getattr(user, "company", None)
        if not company:
            return Response([], status=status.HTTP_200_OK)

        # Берём абсолютно ВСЕ лиды компании без каких-либо ограничений
        leads_qs = LeadConsalting.objects.filter(company=company).select_related("owner")
        leads_by_phone = {}
        for lead in leads_qs:
            if lead.phone:
                clean_phone = "".join(filter(str.isdigit, lead.phone))
                if clean_phone and clean_phone not in leads_by_phone:
                    leads_by_phone[clean_phone] = lead

        # Берём абсолютно ВСЕ входящие заявки компании
        inbound_qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner")
        inbounds_by_phone = {}
        for ib in inbound_qs:
            if ib.phone:
                clean_phone = "".join(filter(str.isdigit, ib.phone))
                if clean_phone and clean_phone not in inbounds_by_phone:
                    inbounds_by_phone[clean_phone] = ib

        all_phones = set(leads_by_phone.keys()) | set(inbounds_by_phone.keys())

        msg_qs = WhatsAppMessageConsalting.objects.filter(company=company)
        raw_msg_phones = msg_qs.values_list("lead__phone", flat=True).distinct()
        for p in raw_msg_phones:
            if p:
                cp = "".join(filter(str.isdigit, p))
                if cp:
                    all_phones.add(cp)

        chats = []
        for cp in all_phones:
            lead = leads_by_phone.get(cp)
            inbound = inbounds_by_phone.get(cp)

            last_msg = None
            if lead:
                last_msg = lead.whatsapp_messages.order_by("-created_at").first()
            elif inbound:
                last_msg = WhatsAppMessageConsalting.objects.filter(
                    company=company, lead__phone__icontains=cp[-10:]
                ).order_by("-created_at").first()

            unread_cnt = 0
            if lead:
                unread_cnt = lead.whatsapp_messages.filter(
                    direction=WhatsAppMessageConsalting.Direction.INBOUND
                ).exclude(status=WhatsAppMessageConsalting.Status.READ).count()

            contact_name = None
            phone_num = None
            lead_id = None
            owner_data = None

            if lead:
                lead_id = str(lead.id)
                phone_num = lead.phone
                contact_name = lead.full_name or lead.title or lead.phone
                if lead.owner:
                    owner_name = f"{(lead.owner.first_name or '').strip()} {(lead.owner.last_name or '').strip()}".strip() or getattr(lead.owner, "email", "")
                    owner_data = {"id": str(lead.owner.id), "name": owner_name}
            elif inbound:
                phone_num = inbound.phone
                contact_name = inbound.full_name or inbound.phone
                if inbound.owner:
                    owner_name = f"{(inbound.owner.first_name or '').strip()} {(inbound.owner.last_name or '').strip()}".strip() or getattr(inbound.owner, "email", "")
                    owner_data = {"id": str(inbound.owner.id), "name": owner_name}
            else:
                phone_num = f"+{cp}"
                contact_name = f"+{cp}"

            last_msg_data = None
            last_msg_text = ""
            last_msg_time = None

            if last_msg:
                last_msg_data = {
                    "id": str(last_msg.id),
                    "message_id": last_msg.message_id,
                    "text": last_msg.text,
                    "direction": last_msg.direction,
                    "status": last_msg.status,
                    "is_incoming": last_msg.direction == "inbound",
                    "created_at": last_msg.created_at.isoformat() if last_msg.created_at else None,
                }
                last_msg_text = last_msg.text
                last_msg_time = last_msg.created_at.isoformat() if last_msg.created_at else None
            elif inbound:
                last_msg_text = inbound.message or ""
                last_msg_time = inbound.updated_at.isoformat() if inbound.updated_at else inbound.created_at.isoformat()

            chats.append({
                "id": lead_id or f"phone_{cp}",
                "lead_id": lead_id,
                "chat_id": phone_num or f"+{cp}",
                "name": contact_name,
                "full_name": contact_name,
                "phone": phone_num or f"+{cp}",
                "owner": owner_data,
                "last_message": last_msg_data,
                "last_message_text": last_msg_text,
                "last_message_time": last_msg_time,
                "unread_count": unread_cnt,
                "has_unread": unread_cnt > 0,
                "updated_at": last_msg_time,
            })

        chats.sort(key=lambda c: c["last_message_time"] or "", reverse=True)
        return Response(chats, status=status.HTTP_200_OK)


class WazzupCredentialsView(APIView):
    """
    Эндпоинт получения ключей интеграции Wazzup для фронтенда.
    Данные заполняются только из админки Django.
    GET /api/consalting/wazzup/credentials/
    GET /api/consalting/wazzup-credentials/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = getattr(request.user, "company", None)
        if not company:
            return Response({
                "api_key": "",
                "channel_id": "",
                "integration_type": "whatsapp",
                "is_active": False
            }, status=status.HTTP_200_OK)

        account = WazzupAccountConsalting.objects.filter(company=company, is_active=True).first()
        if not account:
            account = WazzupAccountConsalting.objects.filter(company=company).first()

        if not account:
            return Response({
                "api_key": "",
                "channel_id": "",
                "integration_type": "whatsapp",
                "is_active": False
            }, status=status.HTTP_200_OK)

        return Response({
            "api_key": account.api_key or "",
            "channel_id": account.channel_id or "",
            "integration_type": account.integration_type or "whatsapp",
            "api_url": account.api_url or "https://api.wazzup24.com",
            "is_active": account.is_active,
        }, status=status.HTTP_200_OK)

