from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import WazzupAccountViewSet, WazzupMessageViewSet, WazzupWebhookView

router = DefaultRouter()
router.register(r'wazzup-accounts', WazzupAccountViewSet, basename='wazzup-accounts')
router.register(r'wazzup-messages', WazzupMessageViewSet, basename='wazzup-messages')

urlpatterns = [
    path('wazzup/webhook/', WazzupWebhookView.as_alias() if hasattr(WazzupWebhookView, 'as_alias') else WazzupWebhookView.as_view(), name='wazzup-webhook'),
    path('', include(router.urls)),
]
