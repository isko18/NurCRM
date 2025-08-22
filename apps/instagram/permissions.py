from rest_framework.permissions import BasePermission
from .models import CompanyIGAccount

class IsCompanyMember(BasePermission):
    """
    Допуск по компании: пользователь должен принадлежать той же компании.
    Staff/superuser проходят всегда.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user.is_authenticated:
            return False
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True

        company = None
        if isinstance(obj, CompanyIGAccount):
            company = obj.company
        else:
            # у треда/сообщения — через ig_account
            company = getattr(getattr(obj, "ig_account", None), "company", None) or \
                      getattr(getattr(getattr(obj, "thread", None), "ig_account", None), "company", None)
        return company and user.company_id == company.id

    def has_permission(self, request, view):
        return request.user.is_authenticated
# apps/instagram/permissions.py
from rest_framework.permissions import BasePermission

class IsCompanyOwnerOrAdmin(BasePermission):
    """
    Для операций привязки/логина: владелец/админ компании или staff/superuser.
    """
    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if getattr(u, "is_staff", False) or getattr(u, "is_superuser", False):
            return True
        return getattr(u, "role", None) in ("owner","admin") and bool(getattr(u, "company_id", None))
