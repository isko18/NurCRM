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
    CashboxWithFlowsSerializer
)
from apps.construction.permissions import IsOwnerOrAdminOrDepartmentEmployee


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────
def _get_company(user):
    if user.is_superuser:
        return None
    return getattr(user, "owned_company", None) or user.company


# ===== DEPARTMENTS ==========================================================
class DepartmentListCreateView(generics.ListCreateAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return Department.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return Department.objects.filter(company=user.owned_company)

        return Department.objects.filter(employees=user)

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
        elif hasattr(user, "owned_company") and user.owned_company:
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

        if user.is_superuser:
            return Department.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return Department.objects.filter(company=user.owned_company)

        return Department.objects.filter(employees=user)


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

        if user.is_superuser:
            return Cashbox.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return Cashbox.objects.filter(department__company=user.owned_company)

        return Cashbox.objects.filter(department__employees=user)


class CashboxDetailView(generics.RetrieveDestroyAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== CASHFLOWS ============================================================
class CashFlowListCreateView(generics.ListCreateAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return CashFlow.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return CashFlow.objects.filter(cashbox__department__company=user.owned_company)

        return CashFlow.objects.filter(cashbox__department__employees=user)

    def perform_create(self, serializer):
        user = self.request.user

        department = user.departments.first()
        if not department:
            raise PermissionDenied("Пользователь не прикреплён ни к одному отделу.")

        try:
            cashbox = department.cashbox
        except Cashbox.DoesNotExist:
            raise PermissionDenied("У отдела нет кассы.")

        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company) or
            (user in department.employees.all())
        ):
            raise PermissionDenied("У вас нет прав добавлять приход/расход в эту кассу.")

        serializer.save(cashbox=cashbox)


class CashFlowDetailView(generics.RetrieveDestroyAPIView):
    queryset = CashFlow.objects.all()
    serializer_class = CashFlowSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]

class AssignEmployeeToDepartmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        data = request.data
        employee_id = data.get("employee_id")
        department = get_object_or_404(Department, id=department_id)

        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company)
        ):
            return Response({"detail": "Недостаточно прав для изменения сотрудников отдела."}, status=403)

        employee = get_object_or_404(User, id=employee_id)

        if employee.company != department.company:
            return Response({"detail": "Нельзя добавить сотрудника из другой компании."}, status=400)

        if employee.departments.exists():
            return Response({"detail": "Пользователь уже прикреплён к другому отделу."}, status=400)

        # Добавляем сотрудника в отдел
        department.employees.add(employee)

        # Проставляем права (если есть в запросе)
        access_fields = [
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking'
        ]
        updated = False
        for field in access_fields:
            if field in data:
                setattr(employee, field, data[field])
                updated = True

        if updated:
            employee.save()

        return Response({"detail": "Сотрудник успешно добавлен в отдел и права обновлены."}, status=200)


class RemoveEmployeeFromDepartmentView(APIView):
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
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return Department.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return Department.objects.filter(company=user.owned_company)

        raise PermissionDenied("Вы не являетесь владельцем компании или администратором.")


# ===== CASHBOX DETAIL WITH FLOWS ============================================
class CashboxOwnerDetailView(generics.ListAPIView):
    serializer_class = CashboxWithFlowsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return Cashbox.objects.all()

        if hasattr(user, "owned_company") and user.owned_company:
            return Cashbox.objects.filter(department__company=user.owned_company)

        raise PermissionDenied("Только владельцы компании или администраторы могут просматривать кассы.")


class CashboxOwnerDetailSingleView(generics.RetrieveAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxWithFlowsSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        user = self.request.user
        cashbox = super().get_object()

        if user.is_superuser:
            return cashbox

        if hasattr(user, 'owned_company') and cashbox.department.company == user.owned_company:
            return cashbox

        raise PermissionDenied("Нет доступа к этой кассе.")
    