from rest_framework import generics, permissions, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework_simplejwt.views import TokenObtainPairView
from django.http import Http404

from .models import User, Industry, SubscriptionPlan, Feature, Sector, CustomRole, Company, Branch
from .serializers import (
    UserSerializer,
    OwnerRegisterSerializer,
    UserListSerializer,
    EmployeeCreateSerializer,
    CustomTokenObtainPairSerializer,
    IndustrySerializer,
    SubscriptionPlanSerializer,
    FeatureSerializer,
    CompanySerializer,
    SectorSerializer,
    EmployeeUpdateSerializer,
    ChangePasswordSerializer,
    CompanyUpdateSerializer,
    CustomRoleSerializer,
)
# сериализаторы филиала (read + write)
from .serializers import BranchSerializer, BranchCreateUpdateSerializer

from .permissions import IsCompanyOwner, IsCompanyOwnerOrAdmin


# 👤 Регистрация владельца компании
class RegisterAPIView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = OwnerRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        return serializer.save()


# 🔐 JWT логин с дополнительной информацией о пользователе
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]


# 📋 Список сотрудников своей компании
class EmployeeListAPIView(generics.ListAPIView):
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):  # 👉 фиксим Swagger
            return User.objects.none()

        user = self.request.user
        company = getattr(user, "owned_company", None) or user.company
        if not company:
            return User.objects.none()
        return company.employees.all()


# 👤 Текущий пользователь
class CurrentUserAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# ➕ Создание сотрудника
class EmployeeCreateAPIView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = EmployeeCreateSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def perform_create(self, serializer):
        serializer.save()


# 🔎 Справочники
class SectorListAPIView(generics.ListAPIView):
    queryset = Sector.objects.all()
    serializer_class = SectorSerializer
    permission_classes = [permissions.AllowAny]


class IndustryListAPIView(generics.ListAPIView):
    queryset = Industry.objects.all()
    serializer_class = IndustrySerializer
    permission_classes = [AllowAny]


class SubscriptionPlanListAPIView(generics.ListAPIView):
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [AllowAny]


class FeatureListAPIView(generics.ListAPIView):
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer
    permission_classes = [AllowAny]


# ❌ Удаление сотрудника
class EmployeeDestroyAPIView(generics.DestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return User.objects.none()

        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        return company.employees.all() if company else User.objects.none()

    def delete(self, request, *args, **kwargs):
        employee = self.get_object()
        if employee == request.user:
            return Response({'detail': 'Вы не можете удалить самого себя.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().delete(request, *args, **kwargs)


# 🏢 Детали компании
class CompanyDetailAPIView(generics.RetrieveAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        if getattr(self, 'swagger_fake_view', False):
            return None
        company = getattr(self.request.user, 'company', None)
        if company is None:
            raise NotFound("Вы не принадлежите ни к одной компании.")
        return company


# 👨‍💼 Детали/редактирование сотрудника
class EmployeeDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = EmployeeUpdateSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return User.objects.none()

        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        return company.employees.exclude(id=self.request.user.id) if company else User.objects.none()

    def delete(self, request, *args, **kwargs):
        employee = self.get_object()
        if employee == request.user:
            return Response({'detail': 'Вы не можете удалить самого себя.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().delete(request, *args, **kwargs)


# 🔑 Смена пароля
class ChangePasswordView(generics.UpdateAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Пароль успешно изменён."}, status=status.HTTP_200_OK)


# 🏢 Обновление компании
class CompanyUpdateAPIView(generics.RetrieveUpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CompanyUpdateSerializer
    # queryset DRF требует, но объект будем доставать вручную
    queryset = Company.objects.none()

    def get_object(self):
        user = self.request.user
        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        if not company:
            raise Http404("Компания для текущего пользователя не найдена.")
        return company


# ====================
# 🎭 Управление кастомными ролями
# ====================

# 📋 Список всех ролей (системные + кастомные)
class RoleListAPIView(generics.ListAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        # системные роли
        system_roles = [
            {"id": None, "name": "Владелец", "code": "owner"},
            {"id": None, "name": "Администратор", "code": "admin"},
        ]
        if getattr(self, 'swagger_fake_view', False):
            return Response(system_roles)

        company = getattr(request.user, "company", None)
        custom_roles = CustomRole.objects.filter(company=company) if company else []
        data = system_roles + CustomRoleSerializer(custom_roles, many=True).data
        return Response(data)


# ➕ Создание кастомной роли
class CustomRoleCreateAPIView(generics.CreateAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def perform_create(self, serializer):
        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        serializer.save(company=company)


# ❌ Детали/удаление кастомной роли
class CustomRoleDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return CustomRole.objects.none()

        user = self.request.user
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if not company:
            return CustomRole.objects.none()

        return CustomRole.objects.filter(company=company)


# =========================================
# 🌿 Филиалы: список/создание/детали/редактирование/удаление
# =========================================

class BranchListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = BranchSerializer
    permission_classes = [permissions.IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Branch.objects.none()
        user = self.request.user
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        return Branch.objects.filter(company=company) if company else Branch.objects.none()

    def perform_create(self, serializer):
        # ВАЖНО: НЕ передаём company сюда — сериализатор сам подставит
        serializer.save()

class BranchDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/branches/<id>/
    PATCH  /api/branches/<id>/   — править может owner/admin
    DELETE /api/branches/<id>/   — удалять может owner/admin
    """
    permission_classes = [IsAuthenticated]
    queryset = Branch.objects.none()  # будет заменён в get_queryset

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Branch.objects.none()
        user = self.request.user
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        return Branch.objects.filter(company=company) if company else Branch.objects.none()

    def get_serializer_class(self):
        # чтение — read-only, запись — create/update
        if self.request.method in ("GET", "HEAD"):
            return BranchSerializer
        return BranchCreateUpdateSerializer

    def perform_update(self, serializer):
        user = self.request.user
        if not (user.is_superuser or user.role in ("owner", "admin")):
            raise PermissionDenied("Недостаточно прав для изменения филиала.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.is_superuser or user.role in ("owner", "admin")):
            raise PermissionDenied("Недостаточно прав для удаления филиала.")
        instance.delete()
