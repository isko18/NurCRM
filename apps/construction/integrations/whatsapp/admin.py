"""
Django admin конфигурация для WhatsApp интеграции.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import WhatsAppConfig, WhatsAppMessage, WhatsAppContact


@admin.register(WhatsAppConfig)
class WhatsAppConfigAdmin(admin.ModelAdmin):
    """Admin для конфигурации WhatsApp."""
    
    list_display = [
        "phone_number",
        "company",
        "branch",
        "is_active_badge",
        "last_webhook_received",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "auto_reply_enabled",
        "created_at",
        "company",
    ]
    search_fields = ["phone_number", "company__name"]
    readonly_fields = [
        "id",
        "webhook_url",
        "created_at",
        "updated_at",
        "last_webhook_received",
    ]
    
    fieldsets = (
        ("Основная информация", {
            "fields": ("id", "company", "branch")
        }),
        ("WhatsApp учетные данные", {
            "fields": (
                "phone_number",
                "business_account_id",
                "phone_number_id",
                "access_token",
            )
        }),
        ("Вебхук", {
            "fields": (
                "webhook_url",
                "webhook_verify_token",
            )
        }),
        ("Параметры", {
            "fields": (
                "is_active",
                "auto_reply_enabled",
                "auto_reply_message",
            )
        }),
        ("История", {
            "fields": (
                "created_at",
                "updated_at",
                "last_webhook_received",
            ),
            "classes": ("collapse",)
        }),
    )
    
    def is_active_badge(self, obj):
        """Иконка статуса активности."""
        if obj.is_active:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Активна</span>'
            )
        return format_html(
            '<span style="color: red; font-weight: bold;">✗ Неактивна</span>'
        )
    is_active_badge.short_description = "Статус"


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    """Admin для сообщений WhatsApp."""
    
    list_display = [
        "whatsapp_message_id",
        "phone_number",
        "direction_badge",
        "message_type",
        "status_badge",
        "created_at",
    ]
    list_filter = [
        "direction",
        "status",
        "message_type",
        "created_at",
        "config__phone_number",
    ]
    search_fields = [
        "whatsapp_message_id",
        "phone_number",
        "content",
    ]
    readonly_fields = [
        "id",
        "whatsapp_message_id",
        "created_at",
        "updated_at",
        "sent_at",
    ]
    
    fieldsets = (
        ("Основная информация", {
            "fields": ("id", "config", "whatsapp_message_id")
        }),
        ("Контакт", {
            "fields": ("phone_number",)
        }),
        ("Содержание", {
            "fields": (
                "message_type",
                "content",
                "media_url",
            )
        }),
        ("Статус", {
            "fields": (
                "direction",
                "status",
                "sent_at",
            )
        }),
        ("Связанный объект", {
            "fields": (
                "content_type",
                "object_id",
            )
        }),
        ("История", {
            "fields": (
                "created_at",
                "updated_at",
            ),
            "classes": ("collapse",)
        }),
        ("Дополнительные данные", {
            "fields": ("metadata",),
            "classes": ("collapse",)
        }),
    )
    
    def direction_badge(self, obj):
        """Иконка направления сообщения."""
        if obj.direction == "inbound":
            return format_html(
                '<span style="color: blue;">← Входящее</span>'
            )
        return format_html(
            '<span style="color: green;">→ Исходящее</span>'
        )
    direction_badge.short_description = "Направление"
    
    def status_badge(self, obj):
        """Иконка статуса."""
        colors = {
            "pending": "gray",
            "sent": "blue",
            "delivered": "green",
            "read": "darkgreen",
            "failed": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            f'<span style="color: {color}; font-weight: bold;">{obj.get_status_display()}</span>'
        )
    status_badge.short_description = "Статус"


@admin.register(WhatsAppContact)
class WhatsAppContactAdmin(admin.ModelAdmin):
    """Admin для контактов WhatsApp."""
    
    list_display = [
        "phone_number",
        "name",
        "is_active_badge",
        "is_blocked_badge",
        "last_message_at",
    ]
    list_filter = [
        "is_active",
        "is_blocked",
        "created_at",
        "config__phone_number",
    ]
    search_fields = [
        "phone_number",
        "name",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
    ]
    
    fieldsets = (
        ("Основная информация", {
            "fields": ("id", "config", "phone_number", "name")
        }),
        ("Статус", {
            "fields": (
                "is_active",
                "is_blocked",
                "last_message_at",
            )
        }),
        ("Связанный объект", {
            "fields": (
                "content_type",
                "object_id",
            )
        }),
        ("История", {
            "fields": (
                "created_at",
                "updated_at",
            ),
            "classes": ("collapse",)
        }),
        ("Дополнительные данные", {
            "fields": ("metadata",),
            "classes": ("collapse",)
        }),
    )
    
    def is_active_badge(self, obj):
        """Иконка статуса активности."""
        if obj.is_active:
            return format_html(
                '<span style="color: green;">✓</span>'
            )
        return format_html(
            '<span style="color: red;">✗</span>'
        )
    is_active_badge.short_description = "Активен"
    
    def is_blocked_badge(self, obj):
        """Иконка статуса блокировки."""
        if obj.is_blocked:
            return format_html(
                '<span style="color: red; font-weight: bold;">🚫 Заблокирован</span>'
            )
        return format_html(
            '<span style="color: green;">✓ Не заблокирован</span>'
        )
    is_blocked_badge.short_description = "Блокировка"
