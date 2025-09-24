# views.py
from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied
from .models import ServicesConsalting, SaleConsalting, SalaryConsalting, RequestsConsalting
from .serializers import (
    ServicesConsaltingSerializer,
    SaleConsaltingSerializer,
    SalaryConsaltingSerializer,
    RequestsConsaltingSerializer,
)


def get_company_from_user(user):
    """Безопасно получить company из user"""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "company", None)


class CompanyScopedMixin:
    """
    Миксин для generic views:
    - фильтрует queryset по текущей компании
    - безопасно для Swagger/AnonymousUser
    """
    company_field_name = "company"

    def is_schema_generation(self):
        return getattr(self, "swagger_fake_view", False)

    def get_company_or_raise(self):
        if self.is_schema_generation():
            return None
        company = get_company_from_user(getattr(self.request, "user", None))
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        return company

    def filter_queryset_by_company(self, queryset):
        if self.is_schema_generation():
            return queryset.none()
        company = self.get_company_or_raise()
        return queryset.filter(**{self.company_field_name: company})


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        serializer.save(company=company)


class ServicesConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = SaleConsalting.objects.select_related('services', 'client', 'user', 'company').all()
    serializer_class = SaleConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        serializer.save(company=company, user=self.request.user)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related('services', 'client', 'user', 'company').all()
    serializer_class = SaleConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = SalaryConsalting.objects.select_related('user', 'company').all()
    serializer_class = SalaryConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

    def perform_create(self, serializer):
        # сохраняем только company; user передаётся из payload
        company = self.get_company_or_raise()
        serializer.save(company=company)


class SalaryConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SalaryConsalting.objects.select_related('user', 'company').all()
    serializer_class = SalaryConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        return self.filter_queryset_by_company(qs)

# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingListCreateView(CompanyScopedMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related('client', 'company').all()
    serializer_class = RequestsConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())

    def perform_create(self, serializer):
        company = self.get_company_or_raise()
        serializer.save(company=company)


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related('client', 'company').all()
    serializer_class = RequestsConsaltingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_by_company(super().get_queryset())
