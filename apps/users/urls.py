from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterAPIView,
    CustomTokenObtainPairView,
    EmployeeListAPIView,
    EmployeeCreateAPIView,
    CurrentUserAPIView,
)

urlpatterns = [
    # Регистрация владельца компании
    path('auth/register/', RegisterAPIView.as_view(), name='user-register'),

    # JWT авторизация
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Работа со своими сотрудниками
    path('employees/', EmployeeListAPIView.as_view(), name='employee-list'),
    path('employees/create/', EmployeeCreateAPIView.as_view(), name='employee-create'),

    # Личный кабинет
    path('profile/', CurrentUserAPIView.as_view(), name='user-me'),
]
