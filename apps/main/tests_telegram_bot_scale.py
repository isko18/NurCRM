"""ТЗ-BE-2026-07, раздел 1: бот на сервере отвечает всегда (приёмка 1.2)."""
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import Company
from apps.main.telegram_bot.models import TelegramBotSettings, TelegramProcessedUpdate
from apps.main.telegram_bot.services import telegram_api
from apps.main.telegram_bot import tasks as tg_tasks

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tg-scale-tests"}}


@override_settings(CACHES=LOCMEM)
class TelegramServerBotScaleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.owner = User.objects.create_user(
            email="owner@scale.bot.test", password="password123", role="owner", is_staff=True,
        )
        self.company = Company.objects.create(name="Scale Bot", slug="scale-bot", owner=self.owner)
        self.owner.company = self.company
        self.owner.owned_company = self.company
        self.owner.save()
        self.api.force_authenticate(user=self.owner)
        self.bot = TelegramBotSettings.objects.create(
            company=self.company,
            secret_token="s3cret",
            mode=TelegramBotSettings.Mode.SERVER,
            token="123456:TEST",
            owner_chat_id="777",
        )
        self.url = f"/api/telegram/webhook/{self.bot.bot_uuid}/"

    def _post(self, update_id, text="Ты работаешь?", chat_id="555"):
        return self.api.post(
            self.url,
            {"update_id": update_id, "message": {"date": int(time.time()), "chat": {"id": chat_id}, "from": {"first_name": "A"}, "text": text}},
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="s3cret",
        )

    def test_enqueue_failure_returns_503_and_retry_is_accepted(self):
        with patch.object(tg_tasks.process_telegram_update, "delay", side_effect=ConnectionError("broker down")):
            resp = self._post(2001)
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # Повтор Telegram после восстановления очереди — принимается, не считается дублем.
        with patch.object(tg_tasks.process_telegram_update, "delay") as delay:
            resp = self._post(2001)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        delay.assert_called_once()
        self.assertEqual(len(delay.call_args.args), 3)  # settings_id, update, enqueued_at

    def test_same_update_processed_once(self):
        update = {"update_id": 3001, "message": {"date": int(time.time()), "chat": {"id": "555"}, "from": {}, "text": "есть хлеб?"}}
        with patch("apps.main.telegram_bot.services.customer_handler.handle_customer_message") as handler:
            tg_tasks.process_telegram_update(str(self.bot.id), update, time.time())
            tg_tasks.process_telegram_update(str(self.bot.id), update, time.time())
        self.assertEqual(handler.call_count, 1)
        self.assertEqual(TelegramProcessedUpdate.objects.filter(bot_settings=self.bot, update_id=3001).count(), 1)

    def test_settings_expose_last_update_and_reply(self):
        with patch.object(tg_tasks.process_telegram_update, "delay"):
            self._post(4001)
        update = {"update_id": 4001, "message": {"date": int(time.time()), "chat": {"id": "777"}, "from": {}, "text": "Как дела"}}
        with patch("apps.main.telegram_bot.services.owner_handler.handle_owner_message"):
            tg_tasks.process_telegram_update(str(self.bot.id), update, time.time())
        data = self.api.get("/api/main/telegram-bot/settings/").data
        self.assertIsNotNone(data["last_update_at"])
        self.assertIsNotNone(data["last_reply_at"])

    def test_handler_crash_sends_fallback_instead_of_silence(self):
        update = {"update_id": 5001, "message": {"date": int(time.time()), "chat": {"id": "555"}, "from": {}, "text": "привет"}}
        with patch("apps.main.telegram_bot.services.customer_handler.handle_customer_message", side_effect=RuntimeError("boom")), \
                patch("apps.main.telegram_bot.services.telegram_api.send_message", return_value={"ok": True}) as send:
            tg_tasks.process_telegram_update(str(self.bot.id), update, time.time())
        send.assert_called_once()
        self.assertEqual(send.call_args.args[2], tg_tasks.FALLBACK_REPLY)

    def test_slow_queue_triggers_team_alert_once(self):
        with patch("apps.support.bot.send_team_alert") as alert:
            tg_tasks._record_queue_latency(45)
            tg_tasks._record_queue_latency(50)
        alert.assert_called_once()

    def test_webhook_check_reinstalls_missing_webhook(self):
        with patch.object(telegram_api, "get_webhook_info", return_value={"url": "", "pending_update_count": 0}), \
                patch.object(telegram_api, "set_webhook", return_value={"ok": True}) as set_wh:
            tg_tasks.check_bot_webhooks_batch([str(self.bot.id)])
        set_wh.assert_called_once()
        self.assertEqual(set_wh.call_args.args[1], telegram_api.build_webhook_url(self.bot.bot_uuid))
        self.bot.refresh_from_db()
        self.assertTrue(self.bot.webhook_ok)
        self.assertIn("переустановлен", self.bot.webhook_error)

    def test_invalid_token_marks_bot_and_stops_polling(self):
        err = telegram_api.TelegramAPIError("Telegram: Unauthorized", status_code=401)
        with patch.object(telegram_api, "get_webhook_info", side_effect=err):
            tg_tasks.check_bot_webhooks_batch([str(self.bot.id)])
        self.bot.refresh_from_db()
        self.assertFalse(self.bot.webhook_ok)
        self.assertEqual(self.bot.webhook_error, tg_tasks.WEBHOOK_TOKEN_INVALID_ERROR)
        with patch.object(tg_tasks.check_bot_webhooks_batch, "apply_async") as dispatch:
            result = tg_tasks.check_server_bots_webhooks()
        self.assertEqual(result["bots"], 0)
        dispatch.assert_not_called()

    def test_mode_switch_sets_and_removes_webhook(self):
        self.bot.mode = TelegramBotSettings.Mode.LOCAL
        self.bot.save()
        with patch.object(telegram_api, "set_webhook", return_value={"ok": True}) as set_wh:
            resp = self.api.patch("/api/main/telegram-bot/settings/", {"mode": "server"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        set_wh.assert_called_once()
        with patch.object(telegram_api, "delete_webhook", return_value={"ok": True}) as del_wh:
            self.api.patch("/api/main/telegram-bot/settings/", {"mode": "local"}, format="json")
        del_wh.assert_called_once()
