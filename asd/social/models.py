# apps/instagram/models.py
import uuid
from django.db import models
from django.utils import timezone
from apps.users.models import Company

class InstagramAccount(models.Model):
    """Аккаунт Instagram, привязанный к компании"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="instagram_account",
        verbose_name="Компания"
    )
    username = models.CharField(max_length=255, verbose_name="Instagram логин")
    session_data = models.JSONField(blank=True, null=True, verbose_name="Сессия MCP")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    connected_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата подключения")

    def __str__(self):
        return f"{self.company.name} ({self.username})"

    class Meta:
        verbose_name = "Instagram аккаунт"
        verbose_name_plural = "Instagram аккаунты"


class InstagramThread(models.Model):
    """Диалог в Direct"""
    id = models.CharField(primary_key=True, max_length=128, verbose_name="ID чата в Instagram")
    account = models.ForeignKey(
        InstagramAccount,
        on_delete=models.CASCADE,
        related_name="threads",
        verbose_name="Instagram аккаунт"
    )
    title = models.CharField(max_length=255, blank=True, null=True, verbose_name="Название чата")
    participants = models.JSONField(blank=True, null=True, verbose_name="Участники")
    last_message_at = models.DateTimeField(blank=True, null=True, verbose_name="Последнее сообщение")

    def __str__(self):
        return f"{self.title or self.id}"

    class Meta:
        verbose_name = "Чат Instagram"
        verbose_name_plural = "Чаты Instagram"


class InstagramMessage(models.Model):
    """Сообщения в Direct"""
    id = models.CharField(primary_key=True, max_length=128, verbose_name="ID сообщения")
    thread = models.ForeignKey(
        InstagramThread,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Чат"
    )
    sender = models.CharField(max_length=255, verbose_name="Отправитель")
    text = models.TextField(blank=True, null=True, verbose_name="Текст")
    attachments = models.JSONField(blank=True, null=True, verbose_name="Медиа/вложения")
    created_at = models.DateTimeField(verbose_name="Время в Instagram")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Когда получено")
    is_from_client = models.BooleanField(default=True, verbose_name="От клиента?")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")

    def __str__(self):
        return f"{self.sender}: {self.text[:20] if self.text else '[медиа]'}"

    class Meta:
        verbose_name = "Сообщение Instagram"
        verbose_name_plural = "Сообщения Instagram"
