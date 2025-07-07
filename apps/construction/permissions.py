from rest_framework.permissions import BasePermission
from apps.construction.models import Department, Cashbox, CashFlow

class IsOwnerOrAdminOrDepartmentEmployee(BasePermission):
    def has_object_permission(self, request, view, obj):
        user = request.user

        # Суперпользователь
        if user.is_superuser:
            return True

        # Пользователь с ролью "admin"
        if getattr(user, 'role', None) == 'admin':
            return True

        # Владелец компании
        if hasattr(user, 'owned_company'):
            if isinstance(obj, Department):
                return obj.company == user.owned_company
            if isinstance(obj, Cashbox):
                return obj.department.company == user.owned_company
            if isinstance(obj, CashFlow):
                return obj.cashbox.department.company == user.owned_company

        # Сотрудник отдела
        if isinstance(obj, Department):
            return user in obj.employees.all()
        if isinstance(obj, Cashbox):
            return user in obj.department.employees.all()
        if isinstance(obj, CashFlow):
            return user in obj.cashbox.department.employees.all()

        return False

