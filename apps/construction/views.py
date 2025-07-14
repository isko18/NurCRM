from rest_framework import generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.models import User
from apps.construction.serializers import (
    DepartmentSerializer,
    CashboxSerializer,
    CashFlowSerializer,
    DepartmentAnalyticsSerializer,
)
from apps.construction.permissions import IsOwnerOrAdminOrDepartmentEmployee


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────
def _get_company(user):
    """
    Определяем компанию, «контекст» которой нужен:
      • superuser → None  (у него доступ ко всему);
      • владелец   → user.owned_company;
      • сотрудник  → user.company.
    """
    if user.is_superuser:
        return None
    return getattr(user, "owned_company", None) or user.company


# ===== DEPARTMENTS ==========================================================
class DepartmentListCreateView(generics.ListCreateAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    # --- GET ---------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)

        if user.is_superuser:
            return Department.objects.all()
        if company:
            return Department.objects.filter(company=company)
        # обычный сотрудник — только свои отделы
        return Department.objects.filter(employees=user)

    # --- POST --------------------------------------------------------------
    def perform_create(self, serializer):
        user = self.request.user

        # право создавать отделы: суперпользователь или владелец
        if user.is_superuser:
            serializer.save()
        elif hasattr(user, "owned_company"):
            # явно передаём company, чтобы гарантированно записалась owned_company
            serializer.save(company=user.owned_company)
        else:
            raise PermissionDenied("У вас нет прав создавать отделы.")


class DepartmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== DEPARTMENT ANALYTICS =================================================
class DepartmentAnalyticsListView(generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)

        if user.is_superuser:
            return Department.objects.all()
        if company:
            return Department.objects.filter(company=company)
        return Department.objects.none()  # не владелец и не superuser


class DepartmentAnalyticsDetailView(generics.RetrieveAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== CASHBOXES ============================================================
class CashboxListView(generics.ListAPIView):
    serializer_class = CashboxSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)

        if user.is_superuser:
            return Cashbox.objects.all()
        if company:
            return Cashbox.objects.filter(department__company=company)
        return Cashbox.objects.filter(department__employees=user)


class CashboxDetailView(generics.RetrieveAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== CASHFLOWS ============================================================
class CashFlowListCreateView(generics.ListCreateAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)
        cashbox_id = self.kwargs.get('cashbox_id')

        queryset = CashFlow.objects.filter(cashbox__id=cashbox_id)

        if user.is_superuser:
            return queryset
        if company:
            return queryset.filter(cashbox__department__company=company)
        return queryset.filter(cashbox__department__employees=user)

    def perform_create(self, serializer):
        user = self.request.user
        cashbox_id = self.kwargs.get('cashbox_id')
        cashbox = get_object_or_404(Cashbox, id=cashbox_id)

        # проверка доступа
        if user.is_superuser:
            pass
        elif hasattr(user, "owned_company") and cashbox.department.company == user.owned_company:
            pass
        elif user in cashbox.department.employees.all():
            pass
        else:
            raise PermissionDenied("У вас нет прав добавлять приход/расход в эту кассу.")

        serializer.save(cashbox=cashbox)


class CashFlowDetailView(generics.RetrieveAPIView):
    queryset = CashFlow.objects.all()
    serializer_class = CashFlowSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== ASSIGN / REMOVE EMPLOYEE =============================================
class AssignEmployeeToDepartmentView(APIView):
    """Добавить сотрудника в отдел (только владелец компании или суперпользователь)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        employee_id = request.data.get("employee_id")
        department = get_object_or_404(Department, id=department_id)

        # проверка прав
        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company)
        ):
            return Response({"detail": "Недостаточно прав для изменения сотрудников отдела."}, status=403)

        employee = get_object_or_404(User, id=employee_id)

        # ⛔ сотрудник должен принадлежать той же компании
        if employee.company != department.company:
            return Response({"detail": "Нельзя добавить сотрудника из другой компании."}, status=400)

        department.employees.add(employee)
        return Response({"detail": "Сотрудник успешно добавлен в отдел."}, status=200)


class RemoveEmployeeFromDepartmentView(APIView):
    """Удалить сотрудника из отдела (только владелец компании или суперпользователь)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        employee_id = request.data.get("employee_id")
        department = get_object_or_404(Department, id=department_id)

        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company)
        ):
            return Response({"detail": "Недостаточно прав для удаления сотрудников из отдела."}, status=403)

        employee = get_object_or_404(User, id=employee_id)

        if employee not in department.employees.all():
            return Response({"detail": "Сотрудник не состоит в этом отделе."}, status=400)

        department.employees.remove(employee)
        return Response({"detail": "Сотрудник успешно удалён из отдела."}, status=200)


# ===== COMPANY-WIDE ANALYTICS ===============================================
class CompanyDepartmentAnalyticsView(generics.ListAPIView):
    """
    Владелец компании / суперюзер: аналитика всех отделов своей компании.
    """
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)

        if user.is_superuser:
            return Department.objects.all()
        if company:
            return Department.objects.filter(company=company)

        raise PermissionDenied("Вы не являетесь владельцем компании или администратором.")
