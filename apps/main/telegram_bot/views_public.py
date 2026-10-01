import logging
from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.main.telegram_bot.models import TelegramBotSettings
from apps.main.telegram_bot.tasks import process_telegram_update

logger = logging.getLogger("telegram_bot.webhook")


class TelegramWebhookPublicView(APIView):
    """
    POST /api/telegram/webhook/{bot_uuid}/
    Публичный эндпоинт приёма вебхуков от Telegram Bot API.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []  # Без авторизации CRM

    def post(self, request, bot_uuid):
        settings = (
            TelegramBotSettings.objects.filter(bot_uuid=bot_uuid)
            .only("id", "mode", "secret_token", "bot_uuid")
            .first()
        )
        if not settings:
            return Response({"detail": "Bot webhook not found"}, status=status.HTTP_404_NOT_FOUND)

        # Проверка secret_token заголовка X-Telegram-Bot-Api-Secret-Token
        if settings.secret_token:
            sent_token = request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
            if sent_token != settings.secret_token:
                logger.warning("Secret token mismatch for bot %s", bot_uuid)
                return Response({"detail": "Invalid secret token"}, status=status.HTTP_403_FORBIDDEN)

        # Если бот в режиме "local", сервер не обрабатывает сообщения
        if settings.mode != TelegramBotSettings.Mode.SERVER:
            return Response({"ok": True, "status": "ignored_local_mode"}, status=status.HTTP_200_OK)

        data = request.data or {}
        update_id = data.get("update_id")
        if not update_id:
            return Response({"ok": True}, status=status.HTTP_200_OK)

        # Дедупликация через Redis (быстрая защита от повторной доставки Telegram)
        dedup_key = f"tg_dedup:{bot_uuid}:{update_id}"
        try:
            # Если ключ уже был установлен — значит этот update_id уже в обработке/обработан
            is_new = cache.add(dedup_key, 1, timeout=86400)
            if not is_new:
                return Response({"ok": True, "status": "duplicate"}, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.warning("Redis dedup check error: %s", exc)

        # Отправляем в фоновую очередь Celery
        try:
            process_telegram_update.delay(str(settings.id), data)
        except Exception as exc:
            logger.error("Failed to enqueue process_telegram_update: %s", exc)

        # Немедленный ответ Telegram 200 OK
        return Response({"ok": True}, status=status.HTTP_200_OK)
