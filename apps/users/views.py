# views.py

from django.db.models import Q, Prefetch
from django.http import Http404
from django.db.utils import ProgrammingError

from rest_framework import generics, permissions, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User, Industry, SubscriptionPlan, Feature, Sector, CustomRole, Company, Branch, BranchMembership
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
    CompanySubscriptionSerializer,
    BranchSerializer,
    BranchCreateUpdateSerializer,
)
from .permissions import IsCompanyOwner, IsCompanyOwnerOrAdmin


# =========================
# helpers: company / branch
# =========================

def _get_company(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "owned_company", None) or getattr(user, "company", None)


def _is_owner_like(user) -> bool:
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
    «Жёстко» назначенный филиал сотрудника:
      1) user.primary_branch (property) -> Branch
      2) user.branch (если вдруг есть)
      3) единственный id в user.allowed_branch_ids -> вернём uuid, БД не трогаем
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) primary_branch (в твоей модели это property, не метод)
    primary = getattr(user, "primary_branch", None)
    if primary and getattr(primary, "company_id", None) == company_id:
        return primary

    # 2) user.branch (если в проекте где-то есть)
    b = getattr(user, "branch", None)
    if b and getattr(b, "company_id", None) == company_id:
        return b

    # 3) единственный филиал по membership — возвращаем uuid, а не делаем Branch.objects.get
    branch_ids = getattr(user, "allowed_branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        return branch_ids[0]

    return None


def _get_active_branch(request, company):
    """
    Активный филиал для фильтрации:
    - обычный сотрудник с фиксированным филиалом -> всегда он (игнорим ?branch)
    - owner/admin -> может выбрать ?branch, иначе None (вся компания)

    Возвращаем:
      - Branch объект (если нашли)
      - None (если вся компания)
    """
    user = getattr(request, "user", None)
    if not company or not user or not getattr(user, "is_authenticated", False):
        return None

    fixed = _fixed_branch_from_user(user, company)

    # сотрудник — фиксируем жестко
    if fixed is not None and not _is_owner_like(user):
        if isinstance(fixed, Branch):
            setattr(request, "branch", fixed)
            return fixed
        # fixed uuid -> пробуем взять из request.branch (если уже ставили), иначе достанем один раз из БД
        try:
            br = Branch.objects.get(id=fixed, company=company)
            setattr(request, "branch", br)
            return br
        except Branch.DoesNotExist:
            setattr(request, "branch", None)
            return None

    # owner/admin — можно выбирать ?branch
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
            pass

    # если где-то ранее уже установили request.branch
    b = getattr(request, "branch", None)
    if b and getattr(b, "company_id", None) == getattr(company, "id", None):
        return b

    setattr(request, "branch", None)
    return None


def _apply_branch_filter_to_users(request, company, base_qs):
    """
    branch=None -> весь base_qs
    branch!=None -> (membership в branch) ИЛИ (нет membership вообще)
    """
    if not company:
        return base_qs.none()

    branch = _get_active_branch(request, company)
    if not branch:
        return base_qs

    # ВАЖНО: distinct нужен, потому что join на memberships
    return (
        base_qs.filter(
            Q(branch_memberships__branch=branch) |
            Q(branch_memberships__isnull=True)
        )
        .distinct()
    )


def _employees_qs(company):
    """
    Базовый QS сотрудников с оптимизацией под сериализаторы:
    - select_related: company, custom_role
    - prefetch_related: memberships + branch
    """
    memberships_qs = BranchMembership.objects.select_related("branch")
    return (
        company.employees
        .select_related("company", "custom_role")
        .prefetch_related(Prefetch("branch_memberships", queryset=memberships_qs))
    )


# =========================
# auth / register
# =========================

class RegisterAPIView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = OwnerRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        return serializer.save()


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


# =========================
# employees
# =========================

class EmployeeListAPIView(generics.ListAPIView):
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()

        user = self.request.user
        company = _get_company(user)
        if not company:
            return User.objects.none()

        base_qs = _employees_qs(company).filter(is_active=True, deleted_at__isnull=True)
        return _apply_branch_filter_to_users(self.request, company, base_qs)


class CurrentUserAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class EmployeeCreateAPIView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = EmployeeCreateSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def perform_create(self, serializer):
        serializer.save()


# ✅ SOFT DELETE
class EmployeeDestroyAPIView(generics.DestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()

        company = _get_company(self.request.user)
        if not company:
            return User.objects.none()

        base_qs = _employees_qs(company).filter(is_active=True, deleted_at__isnull=True)
        return _apply_branch_filter_to_users(self.request, company, base_qs)

    def destroy(self, request, *args, **kwargs):
        employee = self.get_object()

        if employee == request.user:
            return Response({"detail": "Вы не можете удалить самого себя."}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(employee, "role", None) == "owner" and not request.user.is_superuser:
            return Response({"detail": "Нельзя удалить владельца компании."}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(employee, "deleted_at", None):
            return Response(status=status.HTTP_204_NO_CONTENT)

        employee.soft_delete(by_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = EmployeeUpdateSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()

        user = self.request.user
        company = _get_company(user)
        if not company:
            return User.objects.none()

        base_qs = (
            _employees_qs(company)
            .filter(is_active=True, deleted_at__isnull=True)
            .exclude(id=user.id)
        )
        return _apply_branch_filter_to_users(self.request, company, base_qs)

    def destroy(self, request, *args, **kwargs):
        employee = self.get_object()

        if employee == request.user:
            return Response({"detail": "Вы не можете удалить самого себя."}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(employee, "role", None) == "owner" and not request.user.is_superuser:
            return Response({"detail": "Нельзя удалить владельца компании."}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(employee, "deleted_at", None):
            return Response(status=status.HTTP_204_NO_CONTENT)

        employee.soft_delete(by_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# =========================
# dictionaries
# =========================

class SectorListAPIView(generics.ListAPIView):
    queryset = Sector.objects.all()
    serializer_class = SectorSerializer
    permission_classes = [AllowAny]


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


# =========================
# company
# =========================

class CompanyDetailAPIView(generics.RetrieveAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        if getattr(self, "swagger_fake_view", False):
            return None

        company = _get_company(self.request.user)
        if company is None:
            raise NotFound("Вы не принадлежите ни к одной компании.")
        return company


class ChangePasswordView(generics.UpdateAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Пароль успешно изменён."}, status=status.HTTP_200_OK)


class CompanyUpdateAPIView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompanyUpdateSerializer
    queryset = Company.objects.none()

    def get_object(self):
        company = _get_company(self.request.user)
        if not company:
            raise Http404("Компания для текущего пользователя не найдена.")
        return company


# =========================
# roles
# =========================

class RoleListAPIView(generics.ListAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        system_roles = [
            {"id": None, "name": "Владелец", "code": "owner"},
            {"id": None, "name": "Администратор", "code": "admin"},
        ]
        if getattr(self, "swagger_fake_view", False):
            return Response(system_roles)

        company = _get_company(request.user)
        custom_roles = CustomRole.objects.filter(company=company) if company else CustomRole.objects.none()
        data = system_roles + CustomRoleSerializer(custom_roles, many=True).data
        return Response(data)


class CustomRoleCreateAPIView(generics.CreateAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def perform_create(self, serializer):
        company = _get_company(self.request.user)
        serializer.save(company=company)


class CustomRoleDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CustomRole.objects.none()

        company = _get_company(self.request.user)
        if not company:
            return CustomRole.objects.none()

        return CustomRole.objects.filter(company=company)


# =========================
# branches
# =========================

class BranchListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = BranchSerializer
    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get_serializer_class(self):
        # На создание нужен сериализатор, который проставляет company из текущего пользователя
        if self.request.method in ("POST", "PUT", "PATCH"):
            return BranchCreateUpdateSerializer
        return BranchSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Branch.objects.none()

        company = _get_company(self.request.user)
        if not company:
            return Branch.objects.none()

        return Branch.objects.filter(company=company).order_by("created_at")

    def perform_create(self, serializer):
        serializer.save()


class BranchDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Branch.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Branch.objects.none()

        company = _get_company(self.request.user)
        if not company:
            return Branch.objects.none()

        return Branch.objects.filter(company=company)

    def get_serializer_class(self):
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
        try:
            instance.delete()
        except ProgrammingError as e:
            msg = str(e)
            # Частый кейс на проде: в БД не применены миграции одного из apps,
            # который содержит FK на Branch (например apps.crm -> crm_contact).
            if 'relation "crm_contact" does not exist' in msg or "crm_contact" in msg:
                raise ValidationError({
                    "detail": (
                        "Ошибка базы данных: отсутствует таблица crm_contact. "
                        "Это означает, что миграции приложения CRM не применены. "
                        "Примените миграции на сервере (например: `python manage.py migrate crm`) "
                        "и повторите удаление филиала."
                    )
                })
            raise


# =========================
# companies list + subscription admin
# =========================

class CompanyListAPIView(generics.ListAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Company.objects.none()

        user = self.request.user

        if user.is_superuser:
            return Company.objects.all()

        if getattr(user, "role", None) == "admin":
            return Company.objects.all()

        if getattr(user, "owned_company_id", None):
            return Company.objects.filter(id=user.owned_company_id)

        if getattr(user, "company_id", None):
            return Company.objects.filter(id=user.company_id)

        return Company.objects.none()


class CompanySubscriptionAdminAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or getattr(user, "role", None) == "admin":
            return Company.objects.all()

        if getattr(user, "owned_company_id", None):
            return Company.objects.filter(id=user.owned_company_id)

        return Company.objects.none()

    def perform_update(self, serializer):
        user = self.request.user
        if not (user.is_superuser or getattr(user, "role", None) == "admin" or getattr(user, "owned_company_id", None)):
            raise PermissionDenied("У вас нет прав изменять подписку компании.")
        serializer.save()
