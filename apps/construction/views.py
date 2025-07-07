from rest_framework import generics, status
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
    DepartmentAnalyticsSerializer
)
from apps.construction.permissions import IsOwnerOrAdminOrDepartmentEmployee


# ===== DEPARTMENTS =====
class DepartmentListCreateView(generics.ListCreateAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Department.objects.filter(company=company)
        return Department.objects.filter(employees=user)

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser:
            serializer.save()
        elif hasattr(user, 'owned_company'):
            serializer.save(company=user.owned_company)
        else:
            raise PermissionDenied("У вас нет прав создавать отделы.")


class DepartmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== DEPARTMENT ANALYTICS =====
class DepartmentAnalyticsListView(generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Department.objects.filter(company=company)
        return Department.objects.filter(employees=user)


class DepartmentAnalyticsDetailView(generics.RetrieveAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== CASHBOXES =====
class CashboxListView(generics.ListAPIView):
    serializer_class = CashboxSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Cashbox.objects.filter(department__company=company)
        return Cashbox.objects.filter(department__employees=user)


class CashboxDetailView(generics.RetrieveAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== CASHFLOWS =====
class CashFlowListCreateView(generics.ListCreateAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return CashFlow.objects.filter(cashbox__department__company=company)
        return CashFlow.objects.filter(cashbox__department__employees=user)

    def perform_create(self, serializer):
        user = self.request.user
        cashbox = serializer.validated_data.get('cashbox')

        if user.is_superuser:
            serializer.save()
        elif hasattr(user, 'owned_company') and cashbox.department.company == user.owned_company:
            serializer.save()
        elif user in cashbox.department.employees.all():
            serializer.save()
        else:
            raise PermissionDenied("У вас нет прав добавлять приход/расход в эту кассу.")


class CashFlowDetailView(generics.RetrieveAPIView):
    queryset = CashFlow.objects.all()
    serializer_class = CashFlowSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentEmployee]


# ===== ASSIGN EMPLOYEE TO DEPARTMENT =====
class AssignEmployeeToDepartmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        employee_id = request.data.get("employee_id")

        department = get_object_or_404(Department, id=department_id)

        # Проверка доступа: superuser или владелец компании
        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company)
        ):
            return Response({"detail": "Недостаточно прав для изменения сотрудников отдела."}, status=403)

        try:
            employee = User.objects.get(id=employee_id)
        except User.DoesNotExist:
            return Response({"detail": "Сотрудник не найден."}, status=404)

        department.employees.add(employee)
        return Response({"detail": "Сотрудник успешно добавлен в отдел."}, status=200)


class CompanyDepartmentAnalyticsView(generics.ListAPIView):
    """
    Только для владельцев компании и суперпользователей.
    Возвращает список всех отделов компании с их аналитикой.
    """
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Department.objects.filter(company=company)

        raise PermissionDenied("Вы не являетесь владельцем компании или администратором.")
    
    
class RemoveEmployeeFromDepartmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id):
        user = request.user
        employee_id = request.data.get("employee_id")

        department = get_object_or_404(Department, id=department_id)

        # ✅ Доступ разрешён владельцу компании или суперпользователю
        if not (
            user.is_superuser or
            (hasattr(user, "owned_company") and department.company == user.owned_company)
        ):
            return Response({"detail": "Недостаточно прав для удаления сотрудников из отдела."}, status=403)

        try:
            employee = User.objects.get(id=employee_id)
        except User.DoesNotExist:
            return Response({"detail": "Сотрудник не найден."}, status=404)

        if employee not in department.employees.all():
            return Response({"detail": "Сотрудник не состоит в этом отделе."}, status=400)

        department.employees.remove(employee)
        return Response({"detail": "Сотрудник успешно удалён из отдела."}, status=200)
