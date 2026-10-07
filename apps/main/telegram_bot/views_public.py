import logging
import time
from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.main.telegram_bot.models import TelegramBotSettings
from apps.main.telegram_bot.tasks import process_telegram_update

logger = logging.getLogger("telegram_bot.webhook")


class TelegramWebhookPerBotThrottle(SimpleRateThrottle):
    """
    Лимит на один бот, а не на IP: все вебхуки приходят с немногих адресов Telegram,
    и общий лимит по IP при тысячах ботов отвечал бы Telegram 429 (ТЗ-07 1.1).
    Один бот Telegram не шлёт больше ~30 обновлений в секунду.
    """
    scope = "telegram_webhook"
    rate = "1800/minute"

    def get_cache_key(self, request, view):
        bot_uuid = view.kwargs.get("bot_uuid")
        ident = str(bot_uuid) if bot_uuid else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class TelegramWebhookPublicView(APIView):
    """
    POST /api/telegram/webhook/{bot_uuid}/
    Публичный эндпоинт приёма вебхуков от Telegram Bot API.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []  # Без авторизации CRM
    throttle_classes = [TelegramWebhookPerBotThrottle]

    def get(self, request, bot_uuid=None):
        if bot_uuid:
            settings = TelegramBotSettings.objects.filter(bot_uuid=bot_uuid).first()
            if not settings:
                return Response({"detail": "Bot webhook not found"}, status=status.HTTP_404_NOT_FOUND)
            return Response(
                {
                    "ok": True,
                    "detail": "Telegram webhook endpoint active.",
                    "bot_username": settings.bot_username,
                    "mode": settings.mode,
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "ok": True,
                "detail": "Telegram webhook service active. Use POST /api/telegram/webhook/{bot_uuid}/ for updates.",
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, bot_uuid=None):
        if not bot_uuid:
            return Response({"detail": "bot_uuid required in path"}, status=status.HTTP_400_BAD_REQUEST)
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

        from django.utils import timezone
        from apps.main.telegram_bot.models import TelegramProcessedUpdate

        # Дедупликация через Redis и БД (защита от повторной доставки Telegram)
        dedup_key = f"tg_dedup:{bot_uuid}:{update_id}"
        try:
            is_new = cache.add(dedup_key, 1, timeout=86400)
            if not is_new:
                return Response({"ok": True, "status": "duplicate"}, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.warning("Redis dedup check error: %s", exc)

        # Проверка дубликата в БД
        if TelegramProcessedUpdate.objects.filter(bot_settings=settings, update_id=update_id).exists():
            return Response({"ok": True, "status": "duplicate"}, status=status.HTTP_200_OK)

        # Фиксируем время последнего входящего сообщения
        now = timezone.now()
        TelegramBotSettings.objects.filter(id=settings.id).update(last_update_at=now)

        # Отправляем в фоновую очередь Celery
        try:
            process_telegram_update.delay(str(settings.id), data, time.time())
        except Exception as exc:
            # Очередь недоступна: снимаем метку дубля и просим Telegram повторить,
            # иначе обновление потеряется молча.
            logger.error("Failed to enqueue process_telegram_update: %s", exc)
            try:
                cache.delete(dedup_key)
            except Exception:
                pass
            return Response({"ok": False}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Немедленный ответ Telegram 200 OK (<= 100 мс)
        return Response({"ok": True}, status=status.HTTP_200_OK)
