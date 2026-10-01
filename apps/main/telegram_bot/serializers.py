import uuid
from decimal import Decimal
from rest_framework import serializers

from apps.main.telegram_bot.models import TelegramBotSettings, TelegramInquiry, TelegramCustomerProfile
from apps.main.telegram_bot.services import telegram_api
from django.conf import settings as django_settings


class TelegramBotSettingsSerializer(serializers.ModelSerializer):
    token_set = serializers.BooleanField(read_only=True)
    ai_key_set = serializers.BooleanField(read_only=True)

    token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text="Токен от @BotFather. Пустая строка удаляет токен.",
    )
    ai_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text="Ключ Google Gemini. Пустая строка удаляет ключ.",
    )

    class Meta:
        model = TelegramBotSettings
        fields = [
            "mode",
            "bot_username",
            "token_set",
            "webhook_ok",
            "webhook_error",
            "owner_chat_id",
            "owner_chat_title",
            "shift_summary_enabled",
            "commands_enabled",
            "ai_enabled",
            "ai_key_set",
            "consultant_enabled",
            "customer_limit_per_hour",
            "voice_replies_enabled",
            "owner_phone",
            "token",
            "ai_key",
        ]
        read_only_fields = ["bot_username", "token_set", "webhook_ok", "webhook_error", "ai_key_set"]

    def update(self, instance, validated_data):
        new_token = validated_data.pop("token", None)
        new_ai_key = validated_data.pop("ai_key", None)

        if new_token is not None:
            raw_token = new_token.strip()
            if raw_token:
                # Проверяем токен в Telegram через getMe
                try:
                    bot_info = telegram_api.get_me(raw_token)
                except telegram_api.TelegramAPIError as exc:
                    raise serializers.ValidationError({"token": [str(exc)]})
                except Exception as exc:
                    raise serializers.ValidationError({"token": [f"Telegram: {exc}"]})

                instance.token = raw_token
                instance.bot_username = bot_info.get("username") or ""

                # Генерируем secret_token и ставим webhook
                sec_token = uuid.uuid4().hex
                instance.secret_token = sec_token

                base_url = getattr(django_settings, "TELEGRAM_WEBHOOK_BASE_URL", "https://app.nurcrm.kg")
                wh_url = f"{base_url.rstrip('/')}/api/telegram/webhook/{instance.bot_uuid}/"

                try:
                    res = telegram_api.set_webhook(raw_token, wh_url, sec_token)
                    if res.get("ok"):
                        instance.webhook_ok = True
                        instance.webhook_error = None
                    else:
                        instance.webhook_ok = False
                        instance.webhook_error = res.get("description", "Failed to set webhook")
                except Exception as exc:
                    instance.webhook_ok = False
                    instance.webhook_error = str(exc)

            else:
                # Пустая строка — удалить токен и webhook (TZ 6)
                if instance.token:
                    try:
                        telegram_api.delete_webhook(instance.token)
                    except Exception:
                        pass
                instance.token = ""
                instance.bot_username = None
                instance.webhook_ok = False
                instance.webhook_error = None
                instance.secret_token = ""

        if new_ai_key is not None:
            instance.ai_key = new_ai_key.strip()

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance


class TelegramInquiryOrderSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    number = serializers.IntegerField(read_only=True)
    total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


class TelegramInquirySerializer(serializers.ModelSerializer):
    order = TelegramInquiryOrderSerializer(read_only=True)

    class Meta:
        model = TelegramInquiry
        fields = [
            "id",
            "created_at",
            "chat_id",
            "name",
            "username",
            "text",
            "reply",
            "is_voice",
            "order",
        ]


class TelegramCustomerSerializer(serializers.Serializer):
    chat_id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    username = serializers.CharField(allow_blank=True)
    messages = serializers.IntegerField()
    orders = serializers.IntegerField()
    last_at = serializers.DateTimeField()
    client_id = serializers.UUIDField(allow_null=True)
