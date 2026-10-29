"""/api/v1/ — API приложения клиентов. Адреса принимаются со слэшем на конце и без."""
from django.urls import re_path

from . import views

urlpatterns = [
    re_path(r"^shops/?$", views.ShopsView.as_view(), name="capp-shops"),
    re_path(r"^shops/(?P<shop_id>[0-9a-fA-F-]{36})/promos/?$", views.ShopPromosView.as_view(), name="capp-shop-promos"),
    re_path(r"^auth/telegram/start/?$", views.TelegramAuthStartView.as_view(), name="capp-auth-start"),
    re_path(r"^auth/telegram/status/?$", views.TelegramAuthStatusView.as_view(), name="capp-auth-status"),
    re_path(r"^auth/telegram/webhook/?$", views.TelegramWebhookView.as_view(), name="capp-auth-webhook"),
    re_path(r"^auth/logout/?$", views.LogoutView.as_view(), name="capp-logout"),
    re_path(r"^me/?$", views.MeView.as_view(), name="capp-me"),
    re_path(r"^me/balance/?$", views.BalanceView.as_view(), name="capp-balance"),
    re_path(r"^me/purchases/?$", views.PurchasesView.as_view(), name="capp-purchases"),
    re_path(r"^me/purchases/(?P<purchase_id>[0-9a-zA-Z-]{1,64})/?$", views.PurchaseDetailView.as_view(),
            name="capp-purchase-detail"),
    re_path(r"^me/push-token/?$", views.PushTokenView.as_view(), name="capp-push-token"),
    re_path(r"^me/referral/?$", views.ReferralView.as_view(), name="capp-referral"),
    re_path(r"^me/referral/apply/?$", views.ReferralApplyView.as_view(), name="capp-referral-apply"),
    re_path(r"^me/qr-token/?$", views.QrTokenView.as_view(), name="capp-qr-token"),
]
