"""
URLs для WhatsApp интеграции.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WhatsAppConfigViewSet,
    WhatsAppMessageViewSet,
    WhatsAppContactViewSet,
    SendMessageView,
    WebhookView,
)

router = DefaultRouter()
router.register(r"configs", WhatsAppConfigViewSet, basename="whatsapp-config")
router.register(r"messages", WhatsAppMessageViewSet, basename="whatsapp-message")
router.register(r"contacts", WhatsAppContactViewSet, basename="whatsapp-contact")

urlpatterns = [
    path("", include(router.urls)),
    path("send-message/", SendMessageView.as_view(), name="whatsapp-send-message"),
    path("webhook/", WebhookView.as_view(), name="whatsapp-webhook"),
]
