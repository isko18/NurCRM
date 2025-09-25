from django.db.models import Q
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


# ─────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────
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
            return Department.objects.all().select_related('company').prefetch_related('employees')

        company = _get_company(user)
        if company:
            return Department.objects.filter(company=company).select_related('company').prefetch_related('employees')

        # Пользователь без company — видит отделы, где состоит
        return Department.objects.filter(employees=user).select_related('company').prefetch_related('employees')

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser:
            serializer.save()
        else:
            company = _get_company(user)
            if not company:
                raise PermissionDenied("У вас нет прав создавать отделы.")
            serializer.save(company=company)


class DepartmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Department.objects.all()
        company = _get_company(user)
        if company:
            return Department.objects.filter(company=company)
        return Department.objects.filter(employees=user)


# ===== DEPARTMENT ANALYTICS =====================================
class DepartmentAnalyticsListView(generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Department.objects.all()

        company = _get_company(user)
        if company:
            return Department.objects.filter(company=company)

        return Department.objects.filter(employees=user)


class DepartmentAnalyticsDetailView(generics.RetrieveAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Department.objects.all()
        company = _get_company(user)
        if company:
            return Department.objects.filter(company=company)
        return Department.objects.filter(employees=user)


# ===== CASHBOXES ================================================
class CashboxListCreateView(generics.ListCreateAPIView):
    serializer_class = CashboxSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Cashbox.objects.all().select_related('company', 'department')

        company = _get_company(user)
        if company:
            # теперь у кассы есть company → фильтруем строго по ней
            return Cashbox.objects.filter(company=company).select_related('company', 'department')

        # без company — кассы отделов, где пользователь состоит
        return Cashbox.objects.filter(department__employees=user).select_related('company', 'department')

    def perform_create(self, serializer):
        user = self.request.user
        company = _get_company(user)
        department = serializer.validated_data.get("department")

        if department:  # касса для отдела
            if not (user.is_superuser or (company and department.company_id == company.id)):
                raise PermissionDenied("Нет прав для создания кассы у этого отдела.")
            if hasattr(department, "cashbox"):
                raise PermissionDenied("У отдела уже есть касса.")
            # company кассы = company отдела
            serializer.save(company=department.company)
        else:  # свободная касса
            if not (user.is_superuser or company):
                raise PermissionDenied("Нет прав для создания свободной кассы.")
            serializer.save(company=company)


class CashboxDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CashboxWithFlowsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Cashbox.objects.all()
        company = _get_company(user)
        if company:
            return Cashbox.objects.filter(company=company)
        return Cashbox.objects.filter(department__employees=user)


# ===== CASHFLOWS ================================================
class CashFlowListCreateView(generics.ListCreateAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return CashFlow.objects.all().select_related('company', 'cashbox', 'cashbox__department')

        company = _get_company(user)
        if company:
            # у движений теперь есть company → фильтруем по ней (жёстче и проще)
            return CashFlow.objects.filter(company=company).select_related('company', 'cashbox', 'cashbox__department')

        # без company — движения касс отделов, где пользователь состоит
        return CashFlow.objects.filter(cashbox__department__employees=user).select_related('company', 'cashbox', 'cashbox__department')

    def perform_create(self, serializer):
        user = self.request.user
        company = _get_company(user)
        department = serializer.validated_data.get("department")

        if department:  # касса для отдела
            if not (user.is_superuser or (company and department.company_id == company.id)):
                raise PermissionDenied("Нет прав для создания кассы у этого отдела.")

            # 🚫 вместо hasattr(...)
            if Cashbox.objects.filter(department=department).exists():
                raise PermissionDenied("У отдела уже есть касса.")

            # company кассы = company отдела
            serializer.save(company=department.company)

        else:  # свободная касса
            if not (user.is_superuser or company):
                raise PermissionDenied("Нет прав для создания свободной кассы.")
            serializer.save(company=company)


class CashFlowDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return CashFlow.objects.all()
        company = _get_company(user)
        if company:
            return CashFlow.objects.filter(company=company)
        return CashFlow.objects.filter(cashbox__department__employees=user)


# ===== ASSIGN / REMOVE EMPLOYEES =================================
class AssignEmployeeToDepartmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        data = request.data
        employee_id = data.get("employee_id")
        department = get_object_or_404(Department, id=department_id)

        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company) or
            getattr(user, "is_admin", False) and department.company_id == getattr(_get_company(user), 'id', None)
        ):
            return Response({"detail": "Недостаточно прав для изменения сотрудников отдела."}, status=403)

        employee = get_object_or_404(User, id=employee_id)

        if employee.company != department.company:
            return Response({"detail": "Нельзя добавить сотрудника из другой компании."}, status=400)

        if employee.departments.exists():
            return Response({"detail": "Пользователь уже прикреплён к другому отделу."}, status=400)

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
            (hasattr(user, "owned_company") and department.company == user.owned_company) or
            getattr(user, "is_admin", False) and department.company_id == getattr(_get_company(user), 'id', None)
        ):
            return Response({"detail": "Недостаточно прав для удаления сотрудников из отдела."}, status=403)

        employee = get_object_or_404(User, id=employee_id)

        if employee not in department.employees.all():
            return Response({"detail": "Сотрудник не состоит в этом отделе."}, status=400)

        department.employees.remove(employee)
        return Response({"detail": "Сотрудник успешно удалён из отдела."}, status=200)


# ===== COMPANY-WIDE ANALYTICS ===================================
class CompanyDepartmentAnalyticsView(generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Department.objects.all()

        company = _get_company(user)
        if company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False)):
            return Department.objects.filter(company=company)

        raise PermissionDenied("Вы не являетесь владельцем компании или администратором.")


# ===== CASHBOX DETAIL WITH FLOWS ================================
class CashboxOwnerDetailView(generics.ListAPIView):
    serializer_class = CashboxWithFlowsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return Cashbox.objects.all()

        company = _get_company(user)
        if (getattr(user, "owned_company", None) or getattr(user, "is_admin", False)) and company:
            # все кассы компании: и отделов, и свободные
            return Cashbox.objects.filter(company=company)

        raise PermissionDenied("Только владельцы компании или администраторы могут просматривать кассы.")


class CashboxOwnerDetailSingleView(generics.RetrieveAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxWithFlowsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Cashbox.objects.all()
        company = _get_company(user)
        if (getattr(user, 'owned_company', None) or getattr(user, 'is_admin', False)) and company:
            return Cashbox.objects.filter(company=company)
        # никому больше не показываем ничего
        return Cashbox.objects.none()
