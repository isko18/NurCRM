# views.py
from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
)
from .serializers import (
    ServicesConsaltingSerializer,
    SaleConsaltingSerializer,
    SalaryConsaltingSerializer,
    RequestsConsaltingSerializer,
)
from apps.users.models import Company


def get_company_from_user(user):
    """
    Попробовать извлечь company из user (безопасно).
    Возвращает None если user анонимный или company не найдена.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None)
    if company is None:
        profile = getattr(user, "profile", None)
        if profile is not None:
            company = getattr(profile, "company", None)
    return company


class CompanyScopedMixin:
    """
    Миксин для generic views:
    - фильтрует queryset по текущей компании;
    - безопасно работает при генерации схемы drf-yasg (swagger_fake_view)
      и при AnonymousUser (возвращает пустой queryset вместо исключения).
    """
    company_field_name = "company"  # при необходимости переопредели в наследниках

    def is_schema_generation(self):
        """
        drf-yasg устанавливает swagger_fake_view=True на поддельных view'ах,
        используемых для генерации схемы. Это помогает избежать обращения
        к request.user.company в момент генерации схемы.
        """
        return getattr(self, "swagger_fake_view", False)

    def get_company_or_raise(self):
        """
        Вернёт company (object) или бросит PermissionDenied для реального запроса,
        но при генерации схемы вернёт None.
        """
        # Если мы генерируем схему — не бросаем исключение
        if self.is_schema_generation():
            return None

        company = get_company_from_user(getattr(self.request, "user", None))
        if not company:
            # Для реального запроса — нельзя продолжать без компании
            raise PermissionDenied("У пользователя не настроена компания.")
        return company

    def filter_queryset_by_company(self, queryset):
        """
        Отфильтрует queryset по компании. При генерации схемы вернёт пустой queryset.
        """
        if self.is_schema_generation():
            return queryset.none()

        company = self.get_company_or_raise()  # для реального запроса гарантированно company или PermissionDenied
        return queryset.filter(**{self.company_field_name: company})


# ServicesConsalting
class ServicesConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        serializer.save(company=company)


class ServicesConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)


# SaleConsalting
class SaleConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = SaleConsalting.objects.select_related('services', 'client', 'user', 'company').all()
    serializer_class = SaleConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        # CurrentUserDefault в сериализаторе подставит user, но сохраняем явно
        try:
            serializer.save(company=company, user=self.request.user)
        except TypeError:
            serializer.save(company=company)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related('services', 'client', 'user', 'company').all()
    serializer_class = SaleConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)


# SalaryConsalting
class SalaryConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = SalaryConsalting.objects.select_related('user', 'company').all()
    serializer_class = SalaryConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        try:
            serializer.save(company=company, user=self.request.user)
        except TypeError:
            serializer.save(company=company)


class SalaryConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SalaryConsalting.objects.select_related('user', 'company').all()
    serializer_class = SalaryConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)


# RequestsConsalting
class RequestsConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related('client', 'company').all()
    serializer_class = RequestsConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        try:
            serializer.save(company=company, user=self.request.user)
        except TypeError:
            serializer.save(company=company)


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related('client', 'company').all()
    serializer_class = RequestsConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)
