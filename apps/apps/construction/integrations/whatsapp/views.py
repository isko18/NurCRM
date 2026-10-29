"""
Views для WhatsApp интеграции.
"""
import logging
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import WhatsAppConfig, WhatsAppMessage, WhatsAppContact
from .serializers import (
    WhatsAppConfigSerializer,
    WhatsAppMessageSerializer,
    WhatsAppContactSerializer,
    SendMessageSerializer,
    WebhookEventSerializer,
)
from .services import WhatsAppService

logger = logging.getLogger(__name__)


class WhatsAppConfigViewSet(viewsets.ModelViewSet):
    """ViewSet для управления конфигурацией WhatsApp."""
    
    queryset = WhatsAppConfig.objects.all()
    serializer_class = WhatsAppConfigSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Получить конфигурации текущего пользователя."""
        user = self.request.user
        # Предполагается, что у пользователя есть компания
        if hasattr(user, "employee_profile"):
            return WhatsAppConfig.objects.filter(
                company=user.employee_profile.company
            )
        return WhatsAppConfig.objects.none()
    
    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        """Получить статистику по конфигурации."""
        config = self.get_object()
        
        total_messages = config.messages.count()
        inbound_messages = config.messages.filter(direction="inbound").count()
        outbound_messages = config.messages.filter(direction="outbound").count()
        failed_messages = config.messages.filter(status="failed").count()
        contacts_count = config.contacts.filter(is_active=True).count()
        
        return Response({
            "total_messages": total_messages,
            "inbound_messages": inbound_messages,
            "outbound_messages": outbound_messages,
            "failed_messages": failed_messages,
            "contacts_count": contacts_count,
            "is_active": config.is_active,
        })


class WhatsAppMessageViewSet(viewsets.ModelViewSet):
    """ViewSet для просмотра сообщений WhatsApp."""
    
    queryset = WhatsAppMessage.objects.all()
    serializer_class = WhatsAppMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Получить сообщения текущего пользователя."""
        user = self.request.user
        if hasattr(user, "employee_profile"):
            return WhatsAppMessage.objects.filter(
                config__company=user.employee_profile.company
            ).order_by("-created_at")
        return WhatsAppMessage.objects.none()
    
    @action(detail=False, methods=["get"])
    def by_phone(self, request):
        """Получить сообщения по номеру телефона."""
        phone_number = request.query_params.get("phone_number")
        config_id = request.query_params.get("config_id")
        
        if not phone_number:
            return Response(
                {"error": "phone_number параметр обязателен"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        messages = self.get_queryset().filter(phone_number=phone_number)
        
        if config_id:
            messages = messages.filter(config_id=config_id)
        
        serializer = self.get_serializer(messages, many=True)
        return Response(serializer.data)


class WhatsAppContactViewSet(viewsets.ModelViewSet):
    """ViewSet для управления контактами WhatsApp."""
    
    queryset = WhatsAppContact.objects.all()
    serializer_class = WhatsAppContactSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Получить контакты текущего пользователя."""
        user = self.request.user
        if hasattr(user, "employee_profile"):
            return WhatsAppContact.objects.filter(
                config__company=user.employee_profile.company
            ).order_by("-last_message_at")
        return WhatsAppContact.objects.none()


class SendMessageView(generics.CreateAPIView):
    """API для отправки сообщения через WhatsApp."""
    
    serializer_class = SendMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def create(self, request, *args, **kwargs):
        """Отправить сообщение."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        config_id = request.data.get("config_id")
        if not config_id:
            return Response(
                {"error": "config_id обязателен"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        config = get_object_or_404(WhatsAppConfig, id=config_id)
        
        # Проверяем доступ
        if hasattr(request.user, "employee_profile"):
            if config.company != request.user.employee_profile.company:
                return Response(
                    {"error": "Нет доступа к этой конфигурации"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        
        try:
            service = WhatsAppService(config)
            message = service.send_message(
                phone_number=serializer.validated_data["phone_number"],
                message=serializer.validated_data["message"],
                content_type=serializer.validated_data.get("content_type"),
                object_id=serializer.validated_data.get("object_id"),
            )
            
            return Response(
                WhatsAppMessageSerializer(message).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class WebhookView(APIView):
    """API для получения вебхуков от WhatsApp."""
    
    def get(self, request):
        """Верификация вебхука."""
        verify_token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")
        
        config = WhatsAppConfig.objects.filter(
            phone_number_id=request.query_params.get("hub.phone_number_id")
        ).first()
        
        if not config:
            return Response(
                {"error": "Конфигурация не найдена"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        if verify_token != config.webhook_verify_token:
            return Response(
                {"error": "Неверный токен верификации"},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        return Response(challenge, status=status.HTTP_200_OK)
    
    def post(self, request):
        """Обработка вебхука от WhatsApp."""
        try:
            data = request.data
            
            # Извлекаем phone_number_id для поиска конфигурации
            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            phone_number_id = changes.get("value", {}).get("metadata", {}).get("phone_number_id")
            
            if not phone_number_id:
                return Response(
                    {"error": "phone_number_id не найден"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            config = get_object_or_404(
                WhatsAppConfig,
                phone_number_id=phone_number_id,
                is_active=True,
            )
            
            # Обновляем время последнего вебхука
            config.last_webhook_received = timezone.now()
            config.save(update_fields=["last_webhook_received"])
            
            # Обрабатываем событие
            service = WhatsAppService(config)
            service.handle_webhook(data)
            
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {str(e)}", exc_info=True)
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
