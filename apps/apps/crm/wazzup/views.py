from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.models import WazzupAccount, WazzupMessage
from apps.crm.serializers import WazzupAccountSerializer, WazzupMessageSerializer
from .services import send_message_service, process_webhook_payload
from .client import WazzupClient


class WazzupAccountViewSet(viewsets.ModelViewSet):
    """
    CRUD для аккаунтов Wazzup
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WazzupAccountSerializer

    def get_queryset(self):
        return WazzupAccount.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

    @action(detail=True, methods=['post'], url_path='setup-webhook')
    def setup_webhook(self, request, pk=None):
        """
        Регистрация Webhook в Wazzup
        """
        account = self.get_object()
        webhook_url = request.data.get('webhook_url') or "https://app.nurcrm.kg/api/crm/wazzup/webhook/"
        
        try:
            client = WazzupClient(account.api_key, account.api_url)
            res = client.setup_webhooks(webhook_url)
            account.is_connected = True
            account.save(update_fields=['is_connected'])
            return Response({"detail": "Webhook успешно зарегистрирован", "response": res})
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='send-message')
    def send_message(self, request, pk=None):
        """
        Отправка сообщения через аккаунт
        """
        account = self.get_object()
        to_phone = request.data.get('to') or request.data.get('phone')
        text = request.data.get('message') or request.data.get('text') or ""
        media_url = request.data.get('media_url')

        if not to_phone:
            return Response({"detail": "Укажите номер получателя 'to'"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            msg = send_message_service(account, to_phone, text, media_url)
            return Response(WazzupMessageSerializer(msg).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WazzupMessageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Просмотр сообщений Wazzup
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WazzupMessageSerializer

    def get_queryset(self):
        return WazzupMessage.objects.filter(account__company=self.request.user.company)


class WazzupWebhookView(APIView):
    """
    Публичный эндпоинт для приема Webhook сообщений от Wazzup
    POST /api/crm/wazzup/webhook/
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.data
        try:
            process_webhook_payload(payload)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
