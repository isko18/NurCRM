from rest_framework import generics, permissions, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied

from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User, Industry, SubscriptionPlan, Feature, Sector, CustomRole
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
)
from .permissions import IsCompanyOwner, IsCompanyOwnerOrAdmin


# 👤 Регистрация владельца компании
class RegisterAPIView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = OwnerRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        user = serializer.save()
        return user


# 🔐 JWT логин с дополнительной информацией о пользователе
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]


# 📋 Список сотрудников своей компании
class EmployeeListAPIView(generics.ListAPIView):
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
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


class FeatureListAPIView(generics.ListAPIView):
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer


class EmployeeDestroyAPIView(generics.DestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [permissions.IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        return company.employees.all() if company else User.objects.none()

    def delete(self, request, *args, **kwargs):
        employee = self.get_object()
        if employee == request.user:
            return Response({'detail': 'Вы не можете удалить самого себя.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().delete(request, *args, **kwargs)


class CompanyDetailAPIView(generics.RetrieveAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        company = getattr(self.request.user, 'company', None)
        if company is None:
            raise NotFound("Вы не принадлежите ни к одной компании.")
        return company


class EmployeeDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = EmployeeUpdateSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get_queryset(self):
        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        return company.employees.exclude(id=self.request.user.id) if company else User.objects.none()

    def delete(self, request, *args, **kwargs):
        employee = self.get_object()
        if employee == request.user:
            return Response({'detail': 'Вы не можете удалить самого себя.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().delete(request, *args, **kwargs)


class ChangePasswordView(generics.UpdateAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Пароль успешно изменён."}, status=status.HTTP_200_OK)


class CompanyUpdateView(generics.UpdateAPIView):
    serializer_class = CompanyUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        user = self.request.user
        company = getattr(user, "owned_company", None)
        if not company:
            raise PermissionDenied("Только владелец компании может изменять её настройки.")
        return company


# ====================
# 🎭 Управление кастомными ролями
# ====================
from .serializers import CustomRoleSerializer

# 📋 Список всех ролей (системные + кастомные)
class RoleListAPIView(generics.ListAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        # Системные роли
        system_roles = [
            {"id": None, "name": "Владелец", "code": "owner"},
            {"id": None, "name": "Администратор", "code": "admin"},
        ]
        # Кастомные роли компании
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


# ❌ Удаление кастомной роли
class CustomRoleDestroyAPIView(generics.DestroyAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        company = getattr(self.request.user, "owned_company", None) or self.request.user.company
        return CustomRole.objects.filter(company=company)
