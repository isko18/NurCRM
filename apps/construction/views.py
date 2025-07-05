from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from apps.construction.models import Department, Cashbox, CashFlow
from apps.construction.serializers import (
    DepartmentSerializer,
    CashboxSerializer,
    CashFlowSerializer,
    DepartmentAnalyticsSerializer
)
from apps.construction.permissions import IsOwnerOrAdminOrDepartmentManager


# ===== DEPARTMENTS =====
class DepartmentListCreateView(generics.ListCreateAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Department.objects.filter(company=company)
        return Department.objects.filter(manager=user)

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
    permission_classes = [IsOwnerOrAdminOrDepartmentManager]


# ===== DEPARTMENT ANALYTICS =====
class DepartmentAnalyticsListView(generics.ListAPIView):
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Department.objects.filter(company=company)
        return Department.objects.filter(manager=user)


class DepartmentAnalyticsDetailView(generics.RetrieveAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentAnalyticsSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentManager]


# ===== CASHBOXES =====
class CashboxListView(generics.ListAPIView):
    serializer_class = CashboxSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return Cashbox.objects.filter(department__company=company)
        return Cashbox.objects.filter(department__manager=user)


class CashboxDetailView(generics.RetrieveAPIView):
    queryset = Cashbox.objects.all()
    serializer_class = CashboxSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentManager]


# ===== CASHFLOW (приходы и расходы) =====
class CashFlowListCreateView(generics.ListCreateAPIView):
    serializer_class = CashFlowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or hasattr(user, 'owned_company'):
            company = user.owned_company if hasattr(user, 'owned_company') else user.company
            return CashFlow.objects.filter(cashbox__department__company=company)
        return CashFlow.objects.filter(cashbox__department__manager=user)

    def perform_create(self, serializer):
        user = self.request.user
        cashbox = serializer.validated_data.get('cashbox')
        if user.is_superuser:
            serializer.save()
        elif hasattr(user, 'owned_company') and cashbox.department.company == user.owned_company:
            serializer.save()
        elif cashbox.department.manager == user:
            serializer.save()
        else:
            raise PermissionDenied("У вас нет прав добавлять приход/расход в эту кассу.")


class CashFlowDetailView(generics.RetrieveAPIView):
    queryset = CashFlow.objects.all()
    serializer_class = CashFlowSerializer
    permission_classes = [IsOwnerOrAdminOrDepartmentManager]
