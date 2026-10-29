"""
Serializers для WhatsApp интеграции.
"""
from rest_framework import serializers

from .models import WhatsAppConfig, WhatsAppMessage, WhatsAppContact


class WhatsAppConfigSerializer(serializers.ModelSerializer):
    """Сериализатор для конфигурации WhatsApp."""
    
    class Meta:
        model = WhatsAppConfig
        fields = [
            "id",
            "company",
            "branch",
            "phone_number",
            "business_account_id",
            "phone_number_id",
            "is_active",
            "auto_reply_enabled",
            "auto_reply_message",
            "webhook_url",
            "created_at",
            "updated_at",
            "last_webhook_received",
        ]
        read_only_fields = ["id", "webhook_url", "created_at", "updated_at", "last_webhook_received"]
        extra_kwargs = {
            "access_token": {"write_only": True},
        }


class WhatsAppMessageSerializer(serializers.ModelSerializer):
    """Сериализатор для сообщений WhatsApp."""
    
    config_phone = serializers.CharField(source="config.phone_number", read_only=True)
    
    class Meta:
        model = WhatsAppMessage
        fields = [
            "id",
            "config",
            "config_phone",
            "whatsapp_message_id",
            "phone_number",
            "message_type",
            "content",
            "media_url",
            "direction",
            "status",
            "metadata",
            "content_type",
            "object_id",
            "created_at",
            "updated_at",
            "sent_at",
        ]
        read_only_fields = [
            "id",
            "whatsapp_message_id",
            "created_at",
            "updated_at",
            "sent_at",
        ]


class WhatsAppContactSerializer(serializers.ModelSerializer):
    """Сериализатор для контактов WhatsApp."""
    
    last_message = serializers.SerializerMethodField()
    
    class Meta:
        model = WhatsAppContact
        fields = [
            "id",
            "config",
            "phone_number",
            "name",
            "is_active",
            "is_blocked",
            "last_message_at",
            "last_message",
            "content_type",
            "object_id",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
    
    def get_last_message(self, obj):
        """Получить последнее сообщение контакта."""
        last_message = obj.config.messages.filter(
            phone_number=obj.phone_number
        ).order_by("-created_at").first()
        
        if last_message:
            return WhatsAppMessageSerializer(last_message).data
        return None


class SendMessageSerializer(serializers.Serializer):
    """Сериализатор для отправки сообщения."""
    
    phone_number = serializers.CharField(max_length=20)
    message = serializers.CharField()
    content_type = serializers.CharField(required=False, allow_blank=True)
    object_id = serializers.UUIDField(required=False, allow_null=True)
    
    def validate_phone_number(self, value):
        """Валидация номера телефона."""
        # Базовая валидация формата
        if not value.startswith("+"):
            raise serializers.ValidationError("Номер должен начинаться с '+'")
        if len(value) < 10:
            raise serializers.ValidationError("Номер телефона слишком короткий")
        return value


class WebhookEventSerializer(serializers.Serializer):
    """Сериализатор для вебхук-событий от WhatsApp."""
    
    object = serializers.CharField()
    entry = serializers.ListField()
