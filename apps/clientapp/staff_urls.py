"""Подключается из apps/main/urls.py → /api/main/…"""
from django.urls import path

from . import staff_views

urlpatterns = [
    path("clients/by-phone/", staff_views.ClientByPhoneAPIView.as_view(), name="client-by-phone"),
    path("clients/resolve-qr/", staff_views.ClientResolveQrAPIView.as_view(), name="client-resolve-qr"),
    path("clients/bonus/import/", staff_views.ClientBonusImportAPIView.as_view(), name="client-bonus-import"),
    path("app-shop-settings/geocode/", staff_views.AppShopGeocodeAPIView.as_view(), name="app-shop-geocode"),
    path("app-shop-settings/points/", staff_views.AppShopPointsAPIView.as_view(), name="app-shop-points"),
    path("app-shop-settings/", staff_views.AppShopSettingsAPIView.as_view(), name="app-shop-settings"),
    path("referral-rules/", staff_views.ReferralRuleAPIView.as_view(), name="app-referral-rules"),
]
