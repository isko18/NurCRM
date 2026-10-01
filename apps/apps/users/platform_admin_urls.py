from django.urls import path
from .platform_admin_views import (
    PlatformAdminMetaAPIView,
    PlatformAdminCompanyListAPIView,
    PlatformAdminCompanyDetailAPIView,
    PlatformAdminCompanySubscriptionAPIView,
    PlatformAdminCompanyUserListCreateAPIView,
    PlatformAdminUserDetailAPIView,
    PlatformAdminUserResetPasswordAPIView,
    PlatformAdminUserImpersonateAPIView,
)

urlpatterns = [
    path('meta/', PlatformAdminMetaAPIView.as_view(), name='platform-admin-meta'),
    path('companies/', PlatformAdminCompanyListAPIView.as_view(), name='platform-admin-companies-list'),
    path('companies/<str:pk>/subscription/', PlatformAdminCompanySubscriptionAPIView.as_view(), name='platform-admin-companies-subscription'),
    path('companies/<str:company_id>/users/', PlatformAdminCompanyUserListCreateAPIView.as_view(), name='platform-admin-companies-users'),
    path('companies/<str:pk>/', PlatformAdminCompanyDetailAPIView.as_view(), name='platform-admin-companies-detail'),
    path('users/<str:pk>/reset-password/', PlatformAdminUserResetPasswordAPIView.as_view(), name='platform-admin-users-reset-password'),
    path('users/<str:pk>/impersonate/', PlatformAdminUserImpersonateAPIView.as_view(), name='platform-admin-users-impersonate'),
    path('users/<str:pk>/', PlatformAdminUserDetailAPIView.as_view(), name='platform-admin-users-detail'),
]
