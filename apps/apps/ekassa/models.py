import uuid

from django.db import models

from apps.users.models import Company


class EkassaIntegration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="ekassa_integration",
        verbose_name="Компания",
    )
    is_enabled = models.BooleanField(
        default=False,
        verbose_name="Включить eKassa",
        help_text="Если выключено, запросы к API eKassa для этой компании не выполняются.",
    )
    api_base_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Базовый URL API",
        help_text="Пусто — используется значение из настроек сервера (тест/бой).",
    )
    login_email = models.EmailField(
        max_length=254,
        blank=True,
        default="",
        verbose_name="Логин eKassa (email)",
    )
    password_cipher = models.TextField(
        blank=True,
        default="",
        verbose_name="Пароль (зашифровано)",
    )
    fiscal_number = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="РН ККМ (fiscal_number)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Интеграция eKassa"
        verbose_name_plural = "Интеграции eKassa"

    def __str__(self):
        return f"eKassa · {self.company.name}"

    @property
    def has_stored_password(self) -> bool:
        return bool(self.password_cipher and self.password_cipher.strip())

    def is_ready(self) -> bool:
        return (
            self.is_enabled
            and bool(self.fiscal_number.strip())
            and bool(self.login_email.strip())
            and self.has_stored_password
        )

    def effective_base_url(self) -> str:
        from django.conf import settings as dj_settings

        u = (self.api_base_url or "").strip().rstrip("/")
        if u:
            return u
        return (getattr(dj_settings, "EKASSA_DEFAULT_BASE_URL", "") or "").strip().rstrip("/")
