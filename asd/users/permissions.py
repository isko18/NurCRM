# apps/cafe/permissions.py
from rest_framework import permissions
from django.contrib.auth import get_user_model

User = get_user_model()


def _company_id_of(obj):
    """Пытаемся извлечь company_id из любого объекта (через поле company_id или company.id)."""
    if obj is None:
        return None
    if hasattr(obj, "company_id"):
        return getattr(obj, "company_id")
    company = getattr(obj, "company", None)
    return getattr(company, "id", None)


class IsCompanyOwner(permissions.BasePermission):
    """
    Пускает владельца компании (или суперпользователя).
    На уровне объекта — владелец должен совпадать по компании с объектом.
    """

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        return bool(
            getattr(u, "is_superuser", False)
            or getattr(u, "role", None) == "owner"
            or getattr(u, "owned_company_id", None)
        )

    def has_object_permission(self, request, view, obj):
        u = request.user
        if getattr(u, "is_superuser", False):
            return True

        is_owner = (getattr(u, "role", None) == "owner") or bool(getattr(u, "owned_company_id", None))
        if not is_owner:
            return False

        obj_company_id = _company_id_of(obj)
        user_company_id = getattr(u, "company_id", None) or getattr(u, "owned_company_id", None)
        return obj_company_id in (None, user_company_id)


class IsCompanyOwnerOrAdmin(permissions.BasePermission):
    """
    Пускает владельца или администратора той же компании (суперпользователь — всегда).
    Для объектов User запрещает редактировать самого себя.
    """

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        if getattr(u, "is_superuser", False):
            return True
        return getattr(u, "role", None) in ("owner", "admin")

    def has_object_permission(self, request, view, obj):
        u = request.user

        # суперюзер — без ограничений
        if getattr(u, "is_superuser", False):
            return True

        # роль должна быть owner|admin
        if getattr(u, "role", None) not in ("owner", "admin"):
            return False

        # проверка компании
        obj_company_id = _company_id_of(obj)
        user_company_id = getattr(u, "company_id", None) or getattr(u, "owned_company_id", None)
        if obj_company_id not in (None, user_company_id):
            return False

        # запрет редактировать себя — только если объект это User
        if request.method not in permissions.SAFE_METHODS and isinstance(obj, User):
            if obj.pk == u.pk:
                return False

        return True
