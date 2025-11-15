from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.models import User, Branch
from apps.construction.serializers import (
    DepartmentSerializer,
    CashboxSerializer,
    CashFlowSerializer,
    DepartmentAnalyticsSerializer,
    CashboxWithFlowsSerializer,
)


# ─────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ
# ─────────────────────────────────────────────────────────────
def _get_company(user):
    """Компания текущего пользователя (owner/company)."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    # fallback: если нет company, но есть branch с company
    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)

    return None


def _fixed_branch_from_user(user, company):
    """
    «Жёстко» назначенный филиал сотрудника (который нельзя поменять ?branch):
      - user.primary_branch() / user.primary_branch
      - user.branch
      - единственный branch из user.branch_ids (если есть такое поле)
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) primary_branch: метод или атрибут
    primary = getattr(user, "primary_branch", None)

    # 1a) как метод
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    # 1b) как свойство
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    # 2) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # 3) единственный филиал из branch_ids (как в JSON пользователя)
    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def _get_active_branch(request):
    """
    Активный филиал:
      1) «жёсткий» филиал пользователя (primary / branch / branch_ids)
      2) ?branch=<uuid> в запросе (если филиал принадлежит компании пользователя
         и жёсткого филиала нет)
      3) request.branch (если мидлварь ставит и филиал той же компании)
      4) None (нет филиала → глобальный режим по всей компании)
    """
    user = getattr(request, "user", None)
    company = _get_company(user)
    if not company:
        setattr(request, "branch", None)
        return None

    company_id = getattr(company, "id", None)

    # 1) жёстко назначенный филиал
    fixed = _fixed_branch_from_user(user, company)
    if fixed is not None:
        setattr(request, "branch", fixed)
        return fixed

    # 2) ?branch=<uuid> — только если нет жёсткого филиала
    branch_id = None
    if hasattr(request, "query_params"):
        branch_id = request.query_params.get("branch")
    else:
        branch_id = request.GET.get("branch")

    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(request, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            # некорректный или чужой филиал — игнорируем
            pass

    # 3) request.branch (если уже поставили и он той же компании)
    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # 4) нет филиала
    setattr(request, "branch", None)
    return None


# ─────────────────────────────────────────────────────────────
# Базовый mixin для company + branch scope
# ─────────────────────────────────────────────────────────────
class CompanyBranchScopedMixin:
    """
    Видимость данных:
      - всегда ограничиваемся компанией пользователя
      - если у модели есть поле branch:
          * при привязке сотрудника к филиалу → только записи этого филиала
          * без филиала → все записи компании (без фильтра по branch)

    Активный филиал:
      1) «жёсткий» филиал пользователя (primary / branch / branch_ids)
      2) ?branch=<uuid>, если жёсткого нет
      3) request.branch (middleware)
      4) None

    Создание:
      - company берём из пользователя
      - branch ставим только если активный филиал определён
        (если филиала нет — branch не трогаем)

    Обновление:
      - company фиксируем
      - branch не трогаем (не переносим между филиалами)
    """

    permission_classes = [permissions.IsAuthenticated]

    def _company(self):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        return _get_company(user)

    def _active_branch(self):
        return _get_active_branch(self.request)

    def _model_has_field(self, queryset, field_name: str) -> bool:
        # учитываем только реальные поля модели
        return field_name in {f.name for f in queryset.model._meta.concrete_fields}

    def _scoped_queryset(self, base_qs):
        """
        company: строго компания пользователя,
        branch (если у модели есть поле):
          - если у пользователя есть активный филиал → только этот филиал
          - если филиала нет → не фильтруем по branch вообще (все филиалы компании)
        """
        if getattr(self, "swagger_fake_view", False):
            return base_qs.none()

        company = self._company()
        if not company:
            return base_qs.none()

        qs = base_qs
        if self._model_has_field(qs, "company"):
            qs = qs.filter(company=company)

        if self._model_has_field(qs, "branch"):
            br = self._active_branch()
            if br is not None:
                qs = qs.filter(branch=br)
            # br is None → не добавляем фильтр по branch, видим все филиалы компании

        return qs

    # На create/update подставляем company всегда,
    # branch — только если активный филиал определён
    def _inject_company_branch_on_save(self, serializer):
        company = self._company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        br = self._active_branch()

        model = getattr(getattr(serializer, "Meta", None), "model", None)
        kwargs = {}
        if model:
            model_fields = {f.name for f in model._meta.concrete_fields}
            if "company" in model_fields:
                kwargs["company"] = company
            if "branch" in model_fields and br is not None:
                # если филиал найден — жёстко ставим его
                kwargs["branch"] = br
        else:
            # на всякий случай подставим company
            kwargs["company"] = company

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        """
        company фиксируем, branch не трогаем (не переносим запись между филиалами).
        """
        company = self._company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        model = getattr(getattr(serializer, "Meta", None), "model", None)
        kwargs = {}
        if model:
            model_fields = {f.name for f in model._meta.concrete_fields}
            if "company" in model_fields:
                kwargs["company"] = company
        else:
            kwargs["company"] = company

        serializer.save(**kwargs)


# ===== DEPARTMENTS ==========================================================
class DepartmentListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = Department.objects.select_related("company", "branch").prefetch_related("employees")
    serializer_class = DepartmentSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())

    def perform_create(self, serializer):
        self._inject_company_branch_on_save(serializer)


class DepartmentDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.select_related("company", "branch").prefetch_related("employees")
    serializer_class = DepartmentSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ===== DEPARTMENT ANALYTICS ================================================
class DepartmentAnalyticsListView(CompanyBranchScopedMixin, generics.ListAPIView):
    queryset = Department.objects.select_related("company", "branch")
    serializer_class = DepartmentAnalyticsSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


class DepartmentAnalyticsDetailView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    queryset = Department.objects.select_related("company", "branch")
    serializer_class = DepartmentAnalyticsSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ===== CASHBOXES ============================================================
class CashboxListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = Cashbox.objects.select_related("company", "branch", "department", "department__branch")
    serializer_class = CashboxSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())

    def perform_create(self, serializer):
        """
        company/branch проставляем из контекста.
        Валидация согласованности с department:
          - department.company == company
          - department.branch согласован с branch
        Это уже проверяет сериализатор/модель.
        """
        self._inject_company_branch_on_save(serializer)


class CashboxDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Cashbox.objects.select_related("company", "branch", "department", "department__branch")
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ===== CASHFLOWS ============================================================
class CashFlowListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch", "cashbox", "cashbox__department", "cashbox__branch"
    )
    serializer_class = CashFlowSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())

    def perform_create(self, serializer):
        """
        Проставляем company/branch из контекста (как и у кассы).
        Модель/сериализатор проверят, что выбранная cashbox принадлежит той же company.
        """
        self._inject_company_branch_on_save(serializer)


class CashFlowDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch", "cashbox", "cashbox__department", "cashbox__branch"
    )
    serializer_class = CashFlowSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ===== ASSIGN / REMOVE EMPLOYEES ===========================================
class AssignEmployeeToDepartmentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        company = _get_company(user)
        if not company:
            return Response({"detail": "Нет компании у пользователя."}, status=403)

        dept = get_object_or_404(
            Department.objects.select_related("company", "branch"),
            id=department_id, company=company
        )
        employee_id = request.data.get("employee_id")
        employee = get_object_or_404(User, id=employee_id, company=company)

        # Запрет на пересечение филиалов:
        # отдел должен быть глобальным или филиала, видимого для текущего пользователя
        br = _get_active_branch(request)
        if br is not None and dept.branch_id not in (None, br.id):
            return Response({"detail": "Отдел принадлежит другому филиалу."}, status=400)

        # Если нужно запретить множественные отделы — раскомментируйте:
        # if employee.departments.exists():
        #     return Response({"detail": "Пользователь уже прикреплён к другому отделу."}, status=400)

        dept.employees.add(employee)

        # Проставляем упрощённый набор прав (если есть в payload)
        access_fields = [
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking'
        ]
        updated = False
        for field in access_fields:
            if field in request.data:
                setattr(employee, field, request.data[field])
                updated = True
        if updated:
            employee.save()

        return Response({"detail": "Сотрудник добавлен в отдел, права обновлены."}, status=200)


class RemoveEmployeeFromDepartmentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        company = _get_company(user)
        if not company:
            return Response({"detail": "Нет компании у пользователя."}, status=403)

        dept = get_object_or_404(
            Department.objects.select_related("company", "branch"),
            id=department_id, company=company
        )
        employee_id = request.data.get("employee_id")
        employee = get_object_or_404(User, id=employee_id, company=company)

        if employee not in dept.employees.all():
            return Response({"detail": "Сотрудник не состоит в этом отделе."}, status=400)

        dept.employees.remove(employee)
        return Response({"detail": "Сотрудник удалён из отдела."}, status=200)


# ===== COMPANY-WIDE ANALYTICS (owner/admin) ================================
class CompanyDepartmentAnalyticsView(CompanyBranchScopedMixin, generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)
        if user.is_superuser:
            qs = Department.objects.select_related("company", "branch")
        elif company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False)):
            qs = Department.objects.filter(company=company).select_related("company", "branch")
        else:
            raise PermissionDenied("Вы не являетесь владельцем компании или администратором.")
        # Даже для owner/admin соблюдаем правило «филиал или вся компания»
        return self._scoped_queryset(qs)


# ===== CASHBOX DETAIL WITH FLOWS (owner/admin) =============================
class CashboxOwnerDetailView(CompanyBranchScopedMixin, generics.ListAPIView):
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = Cashbox.objects.select_related("company", "branch", "department", "department__branch")
        else:
            company = _get_company(user)
            if not (company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False))):
                raise PermissionDenied("Только владельцы компании или администраторы могут просматривать кассы.")
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch", "department", "department__branch")
        return self._scoped_queryset(qs)


class CashboxOwnerDetailSingleView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = Cashbox.objects.select_related("company", "branch", "department", "department__branch")
        else:
            company = _get_company(user)
            if not (company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False))):
                return Cashbox.objects.none()
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch", "department", "department__branch")
        return self._scoped_queryset(qs)
