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

    def get_queryset(self):
        from .models import WhatsAppMessageConsalting
        qs = WhatsAppMessageConsalting.objects.filter(company=self.request.user.company)
        lead_id = self.request.query_params.get('lead') or self.request.query_params.get('lead_id')
        if lead_id:
            qs = qs.filter(lead_id=lead_id)
        return qs.order_by('created_at')


class WazzupChatListView(APIView):
    """
    Список диалогов/чатов WhatsApp воронки консалтинга (как в интерфейсе WhatsApp Web).
    Сортировка по времени последнего сообщения.
    Эндпоинты:
      GET /api/consalting/chats/
      GET /api/consalting/wazzup-chats/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .access import apply_lead_visibility
        from .models import LeadConsalting, WhatsAppMessageConsalting

        user = request.user
        company = getattr(user, "company", None)
        if not company:
            return Response([], status=status.HTTP_200_OK)

        qs = LeadConsalting.objects.filter(company=company).select_related("owner")
        qs = apply_lead_visibility(qs, user)
        qs = qs.exclude(phone="").order_by("-updated_at")

        chats = []
        for lead in qs:
            last_msg = lead.whatsapp_messages.order_by("-created_at").first()
            unread_cnt = lead.whatsapp_messages.filter(
                direction=WhatsAppMessageConsalting.Direction.INBOUND
            ).exclude(status=WhatsAppMessageConsalting.Status.READ).count()

            last_msg_data = None
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

            chats.append({
                "id": str(lead.id),
                "lead_id": str(lead.id),
                "chat_id": lead.phone,
                "name": lead.full_name or lead.title or lead.phone,
                "full_name": lead.full_name,
                "phone": lead.phone,
                "owner": {
                    "id": str(lead.owner.id),
                    "name": lead.owner.full_name or lead.owner.email
                } if lead.owner else None,
                "last_message": last_msg_data,
                "last_message_text": last_msg.text if last_msg else "",
                "last_message_time": last_msg.created_at.isoformat() if (last_msg and last_msg.created_at) else lead.updated_at.isoformat(),
                "unread_count": unread_cnt,
                "has_unread": unread_cnt > 0,
                "updated_at": lead.updated_at.isoformat(),
            })

        chats.sort(key=lambda c: c["last_message_time"] or "", reverse=True)
        return Response(chats, status=status.HTTP_200_OK)

