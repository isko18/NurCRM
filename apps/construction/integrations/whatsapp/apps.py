"""
Конфигурация приложения WhatsApp интеграции.
"""
from django.apps import AppConfig


class WhatsAppIntegrationConfig(AppConfig):
    """Конфигурация приложения WhatsApp интеграции."""
    
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.construction.integrations.whatsapp"
    verbose_name = "WhatsApp Интеграция"
