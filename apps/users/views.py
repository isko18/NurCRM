from rest_framework import generics, permissions, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework_simplejwt.views import TokenObtainPairView
from django.http import Http404
from django.db.models import Q  # для фильтра по филиалам

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
    CompanySubscriptionSerializer 
)
# сериализаторы филиала (read + write)
from .serializers import BranchSerializer, BranchCreateUpdateSerializer

from .permissions import IsCompanyOwner, IsCompanyOwnerOrAdmin


# ===== Общие helpers для company / branch / ролей =====

def _get_company(user):
    """
    Компания текущего пользователя:
      - owned_company (владелец)
      - company (сотрудник)
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "owned_company", None) or getattr(user, "company", None)


def _is_owner_like(user) -> bool:
    """
    Владелец / админ / суперюзер – тем разрешаем видеть всю компанию,
    а не только свой филиал.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    if getattr(user, "owned_company", None):
        return True
    return False


def _fixed_branch_from_user(user, company):
    """
    «Жёстко» назначенный филиал сотрудника (который нельзя переключать ?branch):
      1) user.primary_branch() / user.primary_branch
      2) user.branch
      3) единственный id в user.branch_ids
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) primary_branch как метод
    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    # 1b) primary_branch как свойство
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    # 2) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # 3) единственный филиал из branch_ids
    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def _get_active_branch(request, company):
    """
    Активный филиал для ФИЛЬТРАЦИИ СОТРУДНИКОВ.

    Логика:
      - если пользователь не owner-like и у него есть «жёсткий» филиал → всегда этот филиал,
        ?branch игнорируем;
      - иначе:
          0) ?branch=<uuid> (если филиал принадлежит компании)
          1) request.branch (если кто-то уже поставил и он от этой компании)
          2) иначе None (вся компания).
    """
    user = getattr(request, "user", None)
    if not company or not user or not getattr(user, "is_authenticated", False):
        return None

    # 1) для обычного сотрудника сначала фиксируем его филиал
    fixed = _fixed_branch_from_user(user, company)
    if fixed is not None and not _is_owner_like(user):
        # продавец/сотрудник с назначенным филиалом – жёстко сидит в нём
        setattr(request, "branch", fixed)
        return fixed

    # 2) owner/admin/сотрудник без фиксированного филиала – можно выбирать ?branch
    branch_id = None
    if hasattr(request, "query_params"):
        branch_id = request.query_params.get("branch")
    elif hasattr(request, "GET"):
        branch_id = request.GET.get("branch")

    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company=company)
            setattr(request, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            # если id кривой/чужой – игнорируем
            pass

    # 3) request.branch, если уже стоит и принадлежит компании
    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == getattr(company, "id", None):
            return b

    # 4) глобальный режим (вся компания)
    setattr(request, "branch", None)
    return None


def _apply_branch_filter_to_users(request, base_qs):
    """
    Фильтр сотрудников по активному филиалу:
      - если branch не определён → возвращаем base_qs как есть (вся компания);
      - если branch определён:
          сотрудники, у которых есть membership в этот филиал
          ИЛИ сотрудники без membership (глобальные).
    """
    user = getattr(request, "user", None)
    company = _get_company(user)
    if not company:
        return base_qs.none()

    branch = _get_active_branch(request, company)
    if not branch:
        # нет активного филиала → весь список сотрудников компании
        return base_qs

    return (
        base_qs.filter(
            Q(branch_memberships__branch=branch) |
            Q(branch_memberships__isnull=True)
        )
        .distinct()
    )


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
        company = _get_company(user)
        if not company:
            return User.objects.none()

        base_qs = company.employees.all()
        # применяем фильтрацию по филиалу:
        #  - для обычных сотрудников → их фиксированный филиал (если есть),
        #  - для owner/admin → опционально ?branch=<uuid>
        return _apply_branch_filter_to_users(self.request, base_qs)


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

        user = self.request.user
        company = _get_company(user)
        if not company:
            return User.objects.none()

        base_qs = company.employees.all()
        # учитываем branch-ограничения так же, как в списке
        return _apply_branch_filter_to_users(self.request, base_qs)

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

        user = self.request.user
        company = _get_company(user)
        if not company:
            return User.objects.none()

        base_qs = company.employees.exclude(id=user.id)
        # учитываем branch-ограничения
        return _apply_branch_filter_to_users(self.request, base_qs)

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
        company = _get_company(user)
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
        company = _get_company(user)
        return Branch.objects.filter(company=company) if company else Branch.objects.none()

    def perform_create(self, serializer):
        # company подставит сам сериализатор (из request.user)
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
        company = _get_company(user)
        return Branch.objects.filter(company=company) if company else Branch.objects.none()

    def get_serializer_class(self):
        # чтение — read-only, запись — create/update
        if self.request.method in ("GET", "HEAD"):
            return BranchSerializer
        return BranchCreateUpdateSerializer

    def perform_update(self, serializer):
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", None) in ("owner", "admin")):
            raise PermissionDenied("Недостаточно прав для изменения филиала.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", None) in ("owner", "admin")):
            raise PermissionDenied("Недостаточно прав для удаления филиала.")
        instance.delete()

# Список компаний (для свагера / фронта)
class CompanyListAPIView(generics.ListAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Company.objects.none()

        user = self.request.user

        # 🔓 superuser видит все компании
        if user.is_superuser:
            return Company.objects.all()

        # 👑 системный админ (role = admin) — тоже все
        if getattr(user, "role", None) == "admin":
            return Company.objects.all()

        # 👔 владелец компании — только свою
        if getattr(user, "owned_company_id", None):
            return Company.objects.filter(id=user.owned_company_id)

        # 👷 сотрудник — только свою
        if getattr(user, "company_id", None):
            return Company.objects.filter(id=user.company_id)

        # прочие — ничего
        return Company.objects.none()


class CompanySubscriptionAdminAPIView(generics.RetrieveUpdateAPIView):
    """
    GET   /api/users/companies/<uuid:pk>/subscription/
    PATCH /api/users/companies/<uuid:pk>/subscription/
    """
    serializer_class = CompanySubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        # суперюзер и системный admin видят/меняют подписку любой компании
        if user.is_superuser or getattr(user, "role", None) == "admin":
            return Company.objects.all()

        # владелец компании может менять только свою
        if getattr(user, "owned_company_id", None):
            return Company.objects.filter(id=user.owned_company_id)

        # обычный сотрудник — лучше запретить вообще:
        return Company.objects.none()

    def perform_update(self, serializer):
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", None) == "admin" or getattr(user, "owned_company_id", None)):
            # если хочешь — можешь оставить только superuser/admin
            raise PermissionDenied("У вас нет прав изменять подписку компании.")
        serializer.save()
