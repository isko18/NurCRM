"""
Модели для WhatsApp интеграции.
"""
import uuid
import json
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.users.models import Company, Branch


class WhatsAppConfig(models.Model):
    """Конфигурация подключения к WhatsApp Business API."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_configs",
        verbose_name="Компания"
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="whatsapp_configs",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал"
    )
    
    # WhatsApp Business Account credentials
    phone_number = models.CharField(
        max_length=20,
        verbose_name="Номер телефона WhatsApp",
        help_text="Номер в формате: +7XXXXXXXXXX"
    )
    business_account_id = models.CharField(
        max_length=255,
        verbose_name="ID бизнес-аккаунта WhatsApp"
    )
    access_token = models.TextField(
        verbose_name="Access Token",
        help_text="Bearer token для доступа к WhatsApp API"
    )
    phone_number_id = models.CharField(
        max_length=255,
        verbose_name="ID номера телефона в WhatsApp"
    )
    
    # Webhook configuration
    webhook_url = models.URLField(
        verbose_name="URL вебхука",
        blank=True,
        null=True,
        help_text="Автоматически генерируется при сохранении"
    )
    webhook_verify_token = models.CharField(
        max_length=255,
        default="NurCRM_WhatsApp_Webhook",
        verbose_name="Токен верификации вебхука"
    )
    
    # Settings
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна"
    )
    auto_reply_enabled = models.BooleanField(
        default=True,
        verbose_name="Автоответы включены"
    )
    auto_reply_message = models.TextField(
        blank=True,
        null=True,
        verbose_name="Текст автоответа",
        help_text="Сообщение, которое будет отправлено при получении сообщения"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")
    last_webhook_received = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последний полученный вебхук"
    )
    
    class Meta:
        verbose_name = "Конфигурация WhatsApp"
        verbose_name_plural = "Конфигурации WhatsApp"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("company", "branch", "phone_number"),
                condition=models.Q(branch__isnull=False),
                name="uq_whatsapp_config_branch_phone"
            ),
            models.UniqueConstraint(
                fields=("company", "phone_number"),
                condition=models.Q(branch__isnull=True),
                name="uq_whatsapp_config_company_phone"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["phone_number"]),
        ]
    
    def __str__(self):
        return f"{self.phone_number} ({self.company.name})"


class WhatsAppMessage(models.Model):
    """История сообщений WhatsApp."""
    
    MESSAGE_STATUS_CHOICES = [
        ("pending", "Ожидает отправки"),
        ("sent", "Отправлено"),
        ("delivered", "Доставлено"),
        ("read", "Прочитано"),
        ("failed", "Ошибка отправки"),
    ]
    
    MESSAGE_TYPE_CHOICES = [
        ("text", "Текст"),
        ("image", "Изображение"),
        ("document", "Документ"),
        ("audio", "Аудио"),
        ("video", "Видео"),
        ("location", "Локация"),
    ]
    
    DIRECTION_CHOICES = [
        ("inbound", "Входящее"),
        ("outbound", "Исходящее"),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    config = models.ForeignKey(
        WhatsAppConfig,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Конфигурация WhatsApp"
    )
    
    # Message metadata
    whatsapp_message_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="ID сообщения в WhatsApp",
        db_index=True
    )
    phone_number = models.CharField(
        max_length=20,
        verbose_name="Номер телефона",
        db_index=True
    )
    
    # Message content
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default="text",
        verbose_name="Тип сообщения"
    )
    content = models.TextField(
        verbose_name="Содержание сообщения"
    )
    media_url = models.URLField(
        blank=True,
        null=True,
        verbose_name="URL медиафайла"
    )
    
    # Message status
    direction = models.CharField(
        max_length=20,
        choices=DIRECTION_CHOICES,
        verbose_name="Направление"
    )
    status = models.CharField(
        max_length=20,
        choices=MESSAGE_STATUS_CHOICES,
        default="pending",
        verbose_name="Статус"
    )
    
    # Additional data
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Дополнительные данные",
        help_text="JSON с дополнительной информацией о сообщении"
    )
    
    # Related object (для связи с контактом, лидом и т.д.)
    content_type = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Тип связанного объекта"
    )
    object_id = models.UUIDField(
        blank=True,
        null=True,
        verbose_name="ID связанного объекта"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Время отправки"
    )
    
    class Meta:
        verbose_name = "Сообщение WhatsApp"
        verbose_name_plural = "Сообщения WhatsApp"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["config", "phone_number"]),
            models.Index(fields=["config", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
    
    def __str__(self):
        return f"{self.direction} - {self.phone_number} ({self.message_type})"


class WhatsAppContact(models.Model):
    """Контакты для отправки сообщений через WhatsApp."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    config = models.ForeignKey(
        WhatsAppConfig,
        on_delete=models.CASCADE,
        related_name="contacts",
        verbose_name="Конфигурация WhatsApp"
    )
    
    phone_number = models.CharField(
        max_length=20,
        verbose_name="Номер телефона",
        db_index=True
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Имя контакта"
    )
    
    # Status tracking
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен"
    )
    is_blocked = models.BooleanField(
        default=False,
        verbose_name="Заблокирован"
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Последнее сообщение"
    )
    
    # Related object (для связи с контактом из CRM, лидом и т.д.)
    content_type = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Тип связанного объекта"
    )
    object_id = models.UUIDField(
        blank=True,
        null=True,
        verbose_name="ID связанного объекта"
    )
    
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Дополнительные данные"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")
    
    class Meta:
        verbose_name = "Контакт WhatsApp"
        verbose_name_plural = "Контакты WhatsApp"
        ordering = ["-last_message_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("config", "phone_number"),
                name="uq_whatsapp_contact_per_config"
            ),
        ]
        indexes = [
            models.Index(fields=["config", "is_active"]),
            models.Index(fields=["phone_number"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
    
    def __str__(self):
        return f"{self.name or 'Неизвестный'} ({self.phone_number})"
