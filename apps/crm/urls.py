from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SalesFunnelViewSet, FunnelStageViewSet,
    ContactViewSet, LeadViewSet, DealViewSet,
    ActivityViewSet,
    MetaBusinessAccountViewSet, WhatsAppBusinessAccountViewSet,
    InstagramBusinessAccountViewSet, ConversationViewSet,
    MessageViewSet, MessageTemplateViewSet,
    MetaWebhookView, MetaOAuthCallbackView, MetaOAuthExchangeView
)

router = DefaultRouter()

# CRM основные сущности
router.register(r'funnels', SalesFunnelViewSet, basename='funnel')
router.register(r'funnel-stages', FunnelStageViewSet, basename='funnel-stage')
router.register(r'contacts', ContactViewSet, basename='contact')
router.register(r'leads', LeadViewSet, basename='lead')
router.register(r'deals', DealViewSet, basename='deal')
router.register(r'activities', ActivityViewSet, basename='activity')

# Meta интеграция
router.register(r'meta-accounts', MetaBusinessAccountViewSet, basename='meta-account')
router.register(r'whatsapp-accounts', WhatsAppBusinessAccountViewSet, basename='whatsapp-account')
router.register(r'instagram-accounts', InstagramBusinessAccountViewSet, basename='instagram-account')

# Переписки и сообщения
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'message-templates', MessageTemplateViewSet, basename='message-template')

urlpatterns = [
    path('', include(router.urls)),
    
    # Meta Webhook endpoint
    # URL: /api/crm/webhook/meta/<business_id>/
    # Этот URL нужно указать в настройках приложения Meta → Webhooks
    path('webhook/meta/<str:business_id>/', MetaWebhookView.as_view(), name='meta-webhook'),
    
    # Meta OAuth endpoints
    # Callback URL для Valid OAuth Redirect URIs в Meta App Settings → Basic
    # Пример: https://nurcrm.kg/api/crm/oauth/meta/callback/
    path('oauth/meta/callback/', MetaOAuthCallbackView.as_view(), name='meta-oauth-callback'),
    
    # Обмен authorization code на access_token
    path('oauth/meta/exchange/', MetaOAuthExchangeView.as_view(), name='meta-oauth-exchange'),
]
