"""Общие вспомогательные функции, перенесённые из монолита (apps.utils)."""


def _is_owner_like(user) -> bool:
    """
    Кто имеет право одобрять:
    - суперюзер
    - staff
    - роль owner или admin
    """
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "is_staff", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    return False
