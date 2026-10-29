"""
Регистрирует вебхук технического бота поддержки в Telegram.

    python manage.py setup_support_webhook --url https://api.nurcrm.example
    python manage.py setup_support_webhook --delete
    python manage.py setup_support_webhook --info

Секрет берётся из SupportBotConfig.webhook_secret или SUPPORT_TELEGRAM_WEBHOOK_SECRET;
если не задан — генерируется и сохраняется в SupportBotConfig.
"""
import secrets

import httpx
from django.core.management.base import BaseCommand, CommandError

from apps.support.bot import TELEGRAM_API_BASE, get_support_config
from apps.support.models import SupportBotConfig

WEBHOOK_PATH = "/api/support/bot/webhook/"


class Command(BaseCommand):
    help = "Регистрирует (или удаляет) вебхук технического Telegram-бота поддержки."

    def add_arguments(self, parser):
        parser.add_argument("--url", help="Базовый публичный URL сервера, например https://api.example.com")
        parser.add_argument("--delete", action="store_true", help="Удалить вебхук")
        parser.add_argument("--info", action="store_true", help="Показать getWebhookInfo")

    def handle(self, *args, **opts):
        cfg = get_support_config(force=True)
        if not cfg.token:
            raise CommandError("Токен бота не задан (SupportBotConfig.token или SUPPORT_TELEGRAM_BOT_TOKEN).")
        api = f"{TELEGRAM_API_BASE}/bot{cfg.token}"

        with httpx.Client(timeout=20.0) as client:
            if opts["info"]:
                self.stdout.write(str(client.get(f"{api}/getWebhookInfo").json()))
                return
            if opts["delete"]:
                self.stdout.write(str(client.post(f"{api}/deleteWebhook").json()))
                return
            base = (opts.get("url") or "").rstrip("/")
            if not base.startswith("https://"):
                raise CommandError("Укажите --url https://… (Telegram требует HTTPS).")

            secret = cfg.webhook_secret
            if not secret:
                secret = secrets.token_urlsafe(32)
                row = SupportBotConfig.objects.order_by("id").first() or SupportBotConfig()
                row.webhook_secret = secret
                row.save()
                self.stdout.write("Сгенерирован секрет вебхука и сохранён в SupportBotConfig.")

            resp = client.post(f"{api}/setWebhook", json={
                "url": base + WEBHOOK_PATH,
                "secret_token": secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": True,
            }).json()
        if not resp.get("ok"):
            raise CommandError(f"setWebhook failed: {resp}")
        self.stdout.write(self.style.SUCCESS(f"Вебхук установлен: {base + WEBHOOK_PATH}"))
