from rest_framework.permissions import BasePermission
from apps.construction.models import Department, Cashbox

class IsOwnerOrAdminOrDepartmentManager(BasePermission):
    def has_object_permission(self, request, view, obj):
        user = request.user

        # Админ
        if user.is_superuser:
            return True

        # Владелец компании
        if hasattr(user, 'owned_company'):
            if isinstance(obj, Department):
                return obj.company == user.owned_company
            if isinstance(obj, Cashbox):
                return obj.department.company == user.owned_company

        # Менеджер
        if isinstance(obj, Department):
            return obj.manager == user
        if isinstance(obj, Cashbox):
            return obj.department.manager == user

        return False
