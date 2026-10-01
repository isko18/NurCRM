import logging
import time
from decimal import Decimal
from celery import shared_task

from apps.main.telegram_bot.services import (
    telegram_api,
    ai_service,
    owner_handler,
    customer_handler,
    events_handler,
)

logger = logging.getLogger("telegram_bot.tasks")


@shared_task(name="apps.main.telegram_bot.tasks.process_telegram_update")
def process_telegram_update(settings_id: str, update_data: dict):
    """Фоновая обработка вебхука от Telegram."""
    from apps.main.telegram_bot.models import (
        TelegramBotSettings,
        TelegramProcessedUpdate,
        TelegramMessageLog,
    )

    try:
        settings = (
            TelegramBotSettings.objects.select_related("company")
            .filter(id=settings_id)
            .first()
        )
        if not settings:
            logger.warning("TelegramBotSettings %s not found", settings_id)
            return

        if settings.mode != TelegramBotSettings.Mode.SERVER:
            logger.info("Bot %s is in local mode, skipping server processing", settings_id)
            return

        update_id = update_data.get("update_id")
        if not update_id:
            return

        # Идемпотентность на уровне БД
        _obj, created = TelegramProcessedUpdate.objects.get_or_create(
            bot_settings=settings,
            update_id=update_id,
        )
        if not created:
            logger.info("Duplicate update_id %s for bot %s, skipping", update_id, settings_id)
            return

        message = update_data.get("message")
        if not message:
            return

        # Проверка возраста сообщения: старше 15 минут не обрабатывать (TZ 5.1)
        msg_date = message.get("date")
        if msg_date:
            age = int(time.time()) - int(msg_date)
            if age > 900:  # 15 минут
                logger.info("Dropping stale message (age %ds > 900s) for bot %s", age, settings_id)
                return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        from_user = message.get("from", {})
        chat_title = chat.get("title") or ""
        sender_name = f"{(from_user.get('first_name') or '').strip()} {(from_user.get('last_name') or '').strip()}".strip() or (from_user.get("username") or "")

        text = message.get("text") or ""
        voice = message.get("voice")
        is_voice = bool(voice)

        # Логируем для detect-owner-chat
        TelegramMessageLog.objects.create(
            bot_settings=settings,
            chat_id=chat_id,
            chat_title=chat_title,
            sender_name=sender_name,
            text=text if not is_voice else "[Голосовое сообщение]",
        )

        # Если входящее голосовое сообщение
        if is_voice and voice:
            file_id = voice.get("file_id")
            token = settings.token
            if token and file_id:
                file_info = telegram_api.get_file(token, file_id)
                file_path = file_info.get("file_path")
                if file_path:
                    audio_bytes = telegram_api.download_file(token, file_path)
                    ai_key = ai_service.get_effective_ai_key(settings.ai_key)
                    if audio_bytes and ai_key:
                        transcribed = ai_service.transcribe_voice(ai_key, audio_bytes)
                        if transcribed:
                            text = transcribed

        if not text:
            return

        # Проверяем: владелец или покупатель (TZ 5.1)
        is_owner = (settings.owner_chat_id and str(settings.owner_chat_id) == str(chat_id))

        if is_owner:
            owner_handler.handle_owner_message(
                settings=settings,
                chat_id=chat_id,
                text=text,
                is_voice=is_voice,
            )
        else:
            customer_handler.handle_customer_message(
                settings=settings,
                chat_id=chat_id,
                from_user=from_user,
                text=text,
                is_voice=is_voice,
            )

    except Exception as exc:
        logger.exception("Error processing telegram update %s: %s", update_data.get("update_id"), exc)


@shared_task(name="apps.main.telegram_bot.tasks.send_telegram_shift_summary")
def send_telegram_shift_summary(company_id: str, shift_id: str):
    """Отправка сводки смены владельцу."""
    try:
        events_handler.send_shift_closed_notification(company_id, shift_id)
    except Exception as exc:
        logger.error("send_telegram_shift_summary task failed for shift %s: %s", shift_id, exc)


@shared_task(name="apps.main.telegram_bot.tasks.send_telegram_debt_reminders")
def send_telegram_debt_reminders(company_id: str):
    """Рассылка напоминаний о долге подписанным покупателям."""
    from apps.main.telegram_bot.models import TelegramBotSettings
    from apps.main.models import Client, Sale
    from django.db.models import Sum

    try:
        settings = TelegramBotSettings.objects.filter(company_id=company_id).first()
        if not settings or not settings.token:
            return

        clients = Client.objects.filter(
            company_id=company_id,
            telegram_chat_id__isnull=False,
        ).exclude(telegram_chat_id="")

        sent_count = 0
        total_amount = Decimal("0.00")
        company_name = getattr(settings.company, "name", "наш магазин")

        for client in clients:
            chat_id = client.telegram_chat_id
            debt = (
                Sale.objects.filter(
                    company_id=company_id,
                    client=client,
                ).aggregate(s=Sum("debt_remaining"))["s"]
                or Decimal("0.00")
            )

            if debt > Decimal("0.00"):
                msg = (
                    f"Здравствуйте, {client.full_name}!\n"
                    f"Напоминаем, что в магазине «{company_name}» у вас есть задолженность: "
                    f"<b>{debt:,.2f} сом</b>.\n"
                    f"Будем рады вашему визиту!"
                )
                res = telegram_api.send_message(settings.token, chat_id, msg, parse_mode="HTML")
                if res.get("ok"):
                    sent_count += 1
                    total_amount += debt

        if settings.owner_chat_id:
            summary = (
                f"✅ <b>Рассылка напоминаний о долгах завершена!</b>\n"
                f"Отправлено сообщений: <b>{sent_count}</b>\n"
                f"На общую сумму: <b>{total_amount:,.2f} сом</b>."
            )
            telegram_api.send_message(settings.token, settings.owner_chat_id, summary, parse_mode="HTML")

    except Exception as exc:
        logger.exception("send_telegram_debt_reminders failed for company %s: %s", company_id, exc)


@shared_task(name="apps.main.telegram_bot.tasks.send_telegram_notification")
def send_telegram_notification(token: str, chat_id: str, text: str, parse_mode: str = "HTML"):
    """Асинхронная отправка произвольного сообщения."""
    try:
        telegram_api.send_message(token, chat_id, text, parse_mode=parse_mode)
    except Exception as exc:
        logger.error("send_telegram_notification failed: %s", exc)
