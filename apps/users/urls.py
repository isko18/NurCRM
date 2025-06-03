from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterAPIView,
    CustomTokenObtainPairView,
    UserListAPIView,
    CurrentUserAPIView,
)

urlpatterns = [
    path('register/', RegisterAPIView.as_view(), name='user-register'),
    path('login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('users/', UserListAPIView.as_view(), name='user-list'),
    path('me/', CurrentUserAPIView.as_view(), name='user-me'),
]
