"""Правила доступа/видимости лидов consalting.

Модель видимости канбана:
  * лид без владельца (owner=None) — общий пул, виден всем сотрудникам
    компании/филиала;
  * взятый лид (owner назначен) — виден только своему владельцу и
    руководителям (owner/admin компании);

Тот же фильтр применяется и в REST (board / list / detail / move / claim),
и в WebSocket-консьюмере при доставке событий.
"""
from __future__ import annotations

from django.db.models import Q

_OWNER_LIKE_ROLES = {"owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"}


def is_owner_like(user) -> bool:
    """Руководитель компании: видит все лиды независимо от владельца."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    # владелец компании (OneToOne Company.owner -> related_name=owned_company)
    if getattr(user, "owned_company", None):
        return True
    if getattr(user, "is_admin", False):
        return True
    if getattr(user, "role", None) in _OWNER_LIKE_ROLES:
        return True
    return False


def apply_lead_visibility(queryset, user):
    """Ограничивает queryset лидов по правилу видимости (поверх company/branch)."""
    if is_owner_like(user):
        return queryset
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(Q(owner__isnull=True) | Q(owner=user))


def apply_client_visibility(queryset, user):
    """Видимость клиентов: продавец видит ничьих (salesperson=None) + своих,
    руководитель — всех (поверх company/branch). Аналогично видимости лидов."""
    if is_owner_like(user):
        return queryset
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(Q(salesperson__isnull=True) | Q(salesperson=user))


def can_see_lead_owner(viewer, owner_id) -> bool:
    """Может ли viewer видеть карточку с данным owner_id (для WS-фильтра)."""
    if not owner_id:
        return True  # пул — виден всем
    if is_owner_like(viewer):
        return True
    return str(getattr(viewer, "id", "")) == str(owner_id)
