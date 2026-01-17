from rest_framework.permissions import BasePermission
from apps.construction.models import Cashbox, CashFlow

# Department был удален, но оставляем проверки для совместимости
try:
    from apps.construction.models import Department
except ImportError:
    Department = None


def _get_obj_company(obj):
    """
    Аккуратно достаём company из разных типов объектов.
    """
    if Department and isinstance(obj, Department):
        return obj.company

    if isinstance(obj, Cashbox):
        # у кассы может быть company напрямую или через department
        if getattr(obj, "company", None):
            return obj.company
        if getattr(obj, "department", None):
            return obj.department.company
        return None

    if isinstance(obj, CashFlow):
        # у CashFlow обычно есть cashbox, у cashbox — company/department
        cashbox = getattr(obj, "cashbox", None)
        if cashbox:
            if getattr(cashbox, "company", None):
                return cashbox.company
            if getattr(cashbox, "department", None):
                return cashbox.department.company
        # fallback, если вдруг у самой записи есть company
        return getattr(obj, "company", None)

    # на всякий случай для других моделей
    return getattr(obj, "company", None)


def _get_obj_department(obj):
    """
    Отдел, к которому привязан объект (если есть):
      - Department → сам отдел
      - Cashbox → cashbox.department
      - CashFlow → cashflow.cashbox.department
    """
    if Department and isinstance(obj, Department):
        return obj

    if isinstance(obj, Cashbox):
        return getattr(obj, "department", None)

    if isinstance(obj, CashFlow):
        cashbox = getattr(obj, "cashbox", None)
        if cashbox is not None:
            return getattr(cashbox, "department", None)

    return None


class IsOwnerOrAdminOrDepartmentEmployee(BasePermission):
    """
    Пускаем:
      - superuser
      - owner/admin компании, которой принадлежит объект
      - сотрудника отдела, к которому привязан объект
        * Department   → user ∈ department.employees
        * Cashbox      → user ∈ cashbox.department.employees (если department есть)
        * CashFlow     → user ∈ cashflow.cashbox.department.employees (если department есть)

    Глобальные кассы/кэшфлоу без department:
      - только superuser/owner/admin своей компании.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # 1) superuser
        if user.is_superuser:
            return True

        user_company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        obj_company = _get_obj_company(obj)
        role = getattr(user, "role", None)

        # 2) владелец компании
        owner_company = getattr(user, "owned_company", None)
        if owner_company is not None and obj_company is not None and owner_company == obj_company:
            return True

        # 3) админ компании
        if role == "admin" and user_company is not None and obj_company is not None and user_company == obj_company:
            return True

        # 4) сотрудник отдела
        dept = _get_obj_department(obj)
        if dept is None:
            # объект не привязан к отделу → для обычного сотрудника доступ запрещён
            return False

        # проверяем принадлежность к отделу через exists(), чтобы не тянуть весь queryset в память
        return dept.employees.filter(id=user.id).exists()
