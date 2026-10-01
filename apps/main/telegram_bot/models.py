import uuid
from django.db import models
from apps.main.telegram_bot.crypto import encrypt_secret, decrypt_secret


class TelegramBotSettings(models.Model):
    class Mode(models.TextChoices):
        SERVER = "server", "Сервер"
        LOCAL = "local", "Касса"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        "users.Company",
        on_delete=models.CASCADE,
        related_name="telegram_bot_settings",
        verbose_name="Компания",
    )
    bot_uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    mode = models.CharField(
        "Режим работы",
        max_length=16,
        choices=Mode.choices,
        default=Mode.SERVER,
    )
    bot_username = models.CharField("Имя бота (@username)", max_length=128, blank=True, null=True)
    encrypted_token = models.TextField("Зашифрованный токен", blank=True, default="")
    webhook_ok = models.BooleanField("Webhook установлен", default=False)
    webhook_error = models.TextField("Ошибка webhook", blank=True, null=True)
    secret_token = models.CharField("Secret token webhook", max_length=128, blank=True, default="")

    owner_chat_id = models.CharField("Chat ID владельца", max_length=64, blank=True, null=True, db_index=True)
    owner_chat_title = models.CharField("Название чата владельца", max_length=255, blank=True, null=True)
    owner_phone = models.CharField("Телефон владельца", max_length=32, blank=True, null=True)

    shift_summary_enabled = models.BooleanField("Сводка при закрытии смены", default=True)
    commands_enabled = models.BooleanField("Команды владельца включены", default=True)
    ai_enabled = models.BooleanField("ИИ включён", default=True)
    encrypted_ai_key = models.TextField("Зашифрованный ключ ИИ", blank=True, default="")
    consultant_enabled = models.BooleanField("ИИ-консультант покупателей включён", default=True)
    customer_limit_per_hour = models.PositiveIntegerField("Лимит запросов покупателя в час", default=20)
    voice_replies_enabled = models.BooleanField("Голосовые ответы включены", default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки Telegram-бота"
        verbose_name_plural = "Настройки Telegram-ботов"

    def __str__(self):
        return f"TelegramBotSettings({self.company_id}, mode={self.mode}, bot=@{self.bot_username})"

    @property
    def token(self) -> str:
        return decrypt_secret(self.encrypted_token)

    @token.setter
    def token(self, val: str):
        self.encrypted_token = encrypt_secret(val.strip()) if val else ""

    @property
    def ai_key(self) -> str:
        return decrypt_secret(self.encrypted_ai_key)

    @ai_key.setter
    def ai_key(self, val: str):
        self.encrypted_ai_key = encrypt_secret(val.strip()) if val else ""

    @property
    def token_set(self) -> bool:
        return bool(self.encrypted_token)

    @property
    def ai_key_set(self) -> bool:
        return bool(self.encrypted_ai_key)


class TelegramInquiry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "users.Company",
        on_delete=models.CASCADE,
        related_name="telegram_inquiries",
        db_index=True,
        verbose_name="Компания",
    )
    chat_id = models.CharField("Chat ID", max_length=64, db_index=True)
    name = models.CharField("Имя", max_length=255, blank=True, default="")
    username = models.CharField("Username", max_length=255, blank=True, default="")
    text = models.TextField("Текст вопроса", blank=True, default="")
    reply = models.TextField("Текст ответа", blank=True, default="")
    is_voice = models.BooleanField("Голосовое сообщение", default=False)
    order = models.ForeignKey(
        "main.ShowcaseOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_inquiries",
        verbose_name="Заказ",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Обращение в Telegram"
        verbose_name_plural = "Обращения в Telegram"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "chat_id"]),
        ]

    def __str__(self):
        return f"TelegramInquiry({self.chat_id}, {self.created_at})"


class TelegramCustomerProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "users.Company",
        on_delete=models.CASCADE,
        related_name="telegram_customer_profiles",
        db_index=True,
        verbose_name="Компания",
    )
    chat_id = models.CharField("Chat ID", max_length=64, db_index=True)
    name = models.CharField("Имя", max_length=255, blank=True, default="")
    username = models.CharField("Username", max_length=255, blank=True, default="")
    messages_count = models.PositiveIntegerField("Количество сообщений", default=0)
    orders_count = models.PositiveIntegerField("Количество заказов", default=0)
    last_at = models.DateTimeField("Последняя активность", auto_now=True)
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_profiles",
        verbose_name="Привязанный клиент CRM",
    )

    class Meta:
        verbose_name = "Профиль покупателя Telegram"
        verbose_name_plural = "Профили покупателей Telegram"
        unique_together = ("company", "chat_id")
        indexes = [
            models.Index(fields=["company", "last_at"]),
        ]

    def __str__(self):
        return f"TelegramCustomerProfile({self.chat_id}, {self.name})"


class TelegramProcessedUpdate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot_settings = models.ForeignKey(
        TelegramBotSettings,
        on_delete=models.CASCADE,
        related_name="processed_updates",
    )
    update_id = models.BigIntegerField("Update ID", db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Обработанный update Telegram"
        verbose_name_plural = "Обработанные update Telegram"
        unique_together = ("bot_settings", "update_id")

    def __str__(self):
        return f"TelegramProcessedUpdate({self.bot_settings_id}, {self.update_id})"


class TelegramMessageLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot_settings = models.ForeignKey(
        TelegramBotSettings,
        on_delete=models.CASCADE,
        related_name="message_logs",
    )
    chat_id = models.CharField("Chat ID", max_length=64, db_index=True)
    chat_title = models.CharField("Название чата", max_length=255, blank=True, default="")
    sender_name = models.CharField("Имя отправителя", max_length=255, blank=True, default="")
    text = models.TextField("Текст сообщения", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Лог входящего сообщения Telegram"
        verbose_name_plural = "Логи входящих сообщений Telegram"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["bot_settings", "created_at"]),
        ]

    def __str__(self):
        return f"TelegramMessageLog({self.chat_id}, {self.created_at})"
