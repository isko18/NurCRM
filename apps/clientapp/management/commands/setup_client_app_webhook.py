from django.core.management.base import BaseCommand, CommandError

from apps.clientapp import telegram


class Command(BaseCommand):
    help = "Ставит вебхук глобального бота входа приложения клиентов: <url>/api/v1/auth/telegram/webhook/"

    def add_arguments(self, parser):
        parser.add_argument("--url", default="https://app.nurcrm.kg", help="Базовый HTTPS-адрес сервера")
        parser.add_argument("--info", action="store_true", help="Только показать getWebhookInfo")

    def handle(self, *args, **opts):
        if not telegram.bot_token():
            raise CommandError("CLIENT_APP_TELEGRAM_BOT_TOKEN не задан.")
        if opts["info"]:
            self.stdout.write(str(telegram.api_call("getWebhookInfo", {})))
            return
        if not telegram.webhook_secret():
            raise CommandError("CLIENT_APP_TELEGRAM_WEBHOOK_SECRET не задан (A-Z, a-z, 0-9, _ и -, до 256 символов).")
        base = opts["url"].rstrip("/")
        if not base.startswith("https://"):
            raise CommandError("Telegram принимает только HTTPS-вебхуки.")
        url = f"{base}/api/v1/auth/telegram/webhook/"
        result = telegram.set_webhook(url)
        if not result or not result.get("ok"):
            raise CommandError(f"setWebhook не удался: {result}")
        me = telegram.api_call("getMe", {}) or {}
        username = ((me.get("result") or {}).get("username")) or "?"
        self.stdout.write(self.style.SUCCESS(f"Вебхук установлен: {url} (бот @{username})"))
        if telegram.bot_username() and telegram.bot_username().lower() != username.lower():
            self.stdout.write(self.style.WARNING(
                f"CLIENT_APP_BOT_USERNAME={telegram.bot_username()} не совпадает с @{username}"
            ))
