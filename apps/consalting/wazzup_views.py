from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
import requests

from .models import WazzupAccountConsalting, LeadConsalting
from .funnel.wazzup import WazzupConsaltingService
from .serializers import WazzupAccountConsaltingSerializer


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

