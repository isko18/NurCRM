import uuid

from django.db import models

from apps.users.models import Company


class OneCIntegration(models.Model):
    """Пер-компанийные настройки интеграции с 1С (мультитенантность)."""

    class AuthType(models.TextChoices):
        BASIC = "basic", "Basic (логин/пароль)"
        TOKEN = "token", "Token (Bearer)"

    class Currency(models.TextChoices):
        KGS = "KGS", "Сом (KGS)"
        USD = "USD", "Доллар (USD)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="onec_integration",
        verbose_name="Компания",
    )
    is_enabled = models.BooleanField(
        default=False,
        verbose_name="Включить интеграцию с 1С",
        help_text="Если выключено, денежные операции этой компании в 1С не выгружаются.",
    )
    base_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Базовый URL HTTP-сервисов 1С",
        help_text="Например: https://1c.host/base/hs/nurcrm",
    )
    auth_type = models.CharField(
        max_length=10,
        choices=AuthType.choices,
        default=AuthType.BASIC,
        verbose_name="Тип авторизации",
    )
    login = models.CharField(max_length=150, blank=True, default="", verbose_name="Логин 1С")
    password_cipher = models.TextField(blank=True, default="", verbose_name="Пароль/токен (зашифровано)")
    inbound_secret_cipher = models.TextField(
        blank=True,
        default="",
        verbose_name="Секрет HMAC для входящих callback'ов (зашифровано)",
    )
    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.KGS,
        verbose_name="Валюта документов 1С",
    )
    enabled_sources = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Какие операции выгружать",
        help_text='Флаги по типам, напр. {"cashflow": true, "treaty": false}. Пусто — выгружать все.',
    )
    request_timeout = models.PositiveIntegerField(default=30, verbose_name="Таймаут запроса, сек")
    last_pull_at = models.DateTimeField(null=True, blank=True, verbose_name="Последняя выгрузка из 1С")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Интеграция с 1С"
        verbose_name_plural = "Интеграции с 1С"

    def __str__(self):
        return f"1С · {self.company.name}"

    @property
    def has_stored_password(self) -> bool:
        return bool(self.password_cipher and self.password_cipher.strip())

    def is_ready(self) -> bool:
        return bool(self.is_enabled and self.base_url.strip() and self.has_stored_password)

    def source_enabled(self, source_type: str) -> bool:
        """Пусто/нет ключа → включено по умолчанию; явный False → выключено."""
        flags = self.enabled_sources or {}
        return bool(flags.get(source_type, True))

    def effective_base_url(self) -> str:
        return (self.base_url or "").strip().rstrip("/")


class OneCSyncRecord(models.Model):
    """Outbox / журнал синхронизации. Одна строка = одна выгрузка документа в 1С."""

    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Исходящий (nurCRM → 1С)"
        INBOUND = "inbound", "Входящий (1С → nurCRM)"

    class Operation(models.TextChoices):
        CREATE = "create", "Создание"
        UPDATE = "update", "Изменение"
        CANCEL = "cancel", "Отмена / сторно"

    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        SENDING = "sending", "Отправляется"
        SENT = "sent", "Отправлено"
        POSTED = "posted", "Проведено в 1С"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="onec_sync_records",
        verbose_name="Компания",
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
        db_index=True,
        verbose_name="Направление",
    )
    source_type = models.CharField(max_length=32, db_index=True, verbose_name="Тип операции")
    source_id = models.CharField(max_length=64, db_index=True, verbose_name="ID объекта nurCRM")
    operation = models.CharField(
        max_length=10,
        choices=Operation.choices,
        default=Operation.CREATE,
        verbose_name="Действие",
    )
    idempotency_key = models.CharField(
        max_length=160,
        unique=True,
        verbose_name="Ключ идемпотентности",
        help_text="{source_type}:{source_id}:{operation} — гарантия отсутствия дублей в 1С.",
    )
    endpoint = models.CharField(max_length=255, blank=True, default="", verbose_name="Эндпоинт 1С")
    onec_doc_type = models.CharField(max_length=64, blank=True, default="", verbose_name="Тип документа 1С")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Статус",
    )
    attempts = models.PositiveIntegerField(default=0, verbose_name="Попыток")
    last_error = models.TextField(blank=True, default="", verbose_name="Последняя ошибка")
    onec_external_id = models.CharField(max_length=128, blank=True, default="", verbose_name="GUID документа 1С")
    onec_number = models.CharField(max_length=64, blank=True, default="", verbose_name="Номер документа 1С")
    onec_posted_at = models.DateTimeField(null=True, blank=True, verbose_name="Проведён в 1С")
    request_payload = models.JSONField(default=dict, blank=True, verbose_name="Отправленный payload")
    response_payload = models.JSONField(default=dict, blank=True, verbose_name="Ответ 1С")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Запись синхронизации с 1С"
        verbose_name_plural = "Журнал синхронизации с 1С"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} [{self.status}]"
