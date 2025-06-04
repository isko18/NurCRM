from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterAPIView,
    CustomTokenObtainPairView,
    UserListAPIView,
    CurrentUserAPIView,
)

urlpatterns = [
    path('auth/register/', RegisterAPIView.as_view(), name='user-register'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('users/', UserListAPIView.as_view(), name='user-list'),
    path('profile/', CurrentUserAPIView.as_view(), name='user-me'),
]
