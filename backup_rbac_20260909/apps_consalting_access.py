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

_OWNER_LIKE_ROLES = {"owner", "admin", "rop", "OWNER", "ADMIN", "ROP", "Владелец", "Администратор", "РОП"}


def is_consulting_supervisor(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", None) == "supervisor"


def is_consulting_salesperson(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", None) == "salesperson"


def get_user_region_codes(user) -> list[str]:
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if hasattr(user, "get_consulting_region_codes"):
        return user.get_consulting_region_codes()
    codes = getattr(user, "consulting_region_codes", [])
    if isinstance(codes, list):
        return [str(c).strip().lower() for c in codes if str(c).strip()]
    return []


# ===========================
# Видимость и управление воронками (по ролям + грантам)
# ===========================

def _grant_funnel_ids(user, *, manage_only=False):
    from .models import EmployeeFunnelGrant
    qs = EmployeeFunnelGrant.objects.filter(employee=user)
    if manage_only:
        qs = qs.filter(can_manage_leads=True)
    return list(qs.values_list("funnel_id", flat=True))


def visible_funnels_qs(queryset, user):
    """Фильтрует воронки по правилу видимости (раздел 1.6/1.7 спеки и 12-regional-supervisor-rbac)."""
    if is_owner_like(user):
        return queryset
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()

    if is_consulting_supervisor(user) or is_consulting_salesperson(user):
        user_regions = get_user_region_codes(user)
        grant_ids = _grant_funnel_ids(user)
        return queryset.filter(
            Q(regional_rules__region_code__in=user_regions, regional_rules__is_active=True) |
            Q(id__in=grant_ids)
        ).distinct()

    q = Q(id__in=_grant_funnel_ids(user))
    custom_role_id = getattr(user, "custom_role_id", None)
    if custom_role_id:
        q |= Q(custom_role_id=custom_role_id)
    else:
        q |= Q(is_main=True)
    return queryset.filter(q)


def can_view_funnel(user, funnel) -> bool:
    if is_owner_like(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if is_consulting_supervisor(user) or is_consulting_salesperson(user):
        user_regions = get_user_region_codes(user)
        from .models import EmployeeFunnelGrant
        if EmployeeFunnelGrant.objects.filter(employee=user, funnel=funnel).exists():
            return True
        if funnel.regional_rules.filter(region_code__in=user_regions, is_active=True).exists():
            return True
        return False

    custom_role_id = getattr(user, "custom_role_id", None)
    if custom_role_id and funnel.custom_role_id == custom_role_id:
        return True
    if not custom_role_id and funnel.is_main:
        return True
    from .models import EmployeeFunnelGrant
    return EmployeeFunnelGrant.objects.filter(employee=user, funnel=funnel).exists()


def can_manage_leads(user, funnel) -> bool:
    if is_owner_like(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_consulting_supervisor(user) or is_consulting_salesperson(user):
        user_regions = get_user_region_codes(user)
        if funnel.regional_rules.filter(region_code__in=user_regions, is_active=True).exists():
            return True
        from .models import EmployeeFunnelGrant
        return EmployeeFunnelGrant.objects.filter(employee=user, funnel=funnel, can_manage_leads=True).exists()
    custom_role_id = getattr(user, "custom_role_id", None)
    if (
        getattr(user, "can_manage_funnel_leads", False)
        and custom_role_id
        and funnel.custom_role_id == custom_role_id
    ):
        return True
    from .models import EmployeeFunnelGrant
    return EmployeeFunnelGrant.objects.filter(
        employee=user, funnel=funnel, can_manage_leads=True
    ).exists()


def can_manage_stages(user, funnel) -> bool:
    if is_owner_like(user):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    custom_role_id = getattr(user, "custom_role_id", None)
    if (
        getattr(user, "can_manage_funnel_stages", False)
        and custom_role_id
        and funnel.custom_role_id == custom_role_id
    ):
        return True
    from .models import EmployeeFunnelGrant
    return EmployeeFunnelGrant.objects.filter(
        employee=user, funnel=funnel, can_manage_stages=True
    ).exists()


def is_owner_like(user) -> bool:
    """Руководитель компании (owner/admin/rop): видит все лиды и воронки."""
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
    if is_consulting_supervisor(user):
        user_regions = get_user_region_codes(user)
        return queryset.filter(region_code__in=user_regions)
    if is_consulting_salesperson(user):
        user_regions = get_user_region_codes(user)
        q = Q(owner=user)
        if user_regions:
            q &= Q(region_code__in=user_regions)
        return queryset.filter(q)
    return queryset.filter(Q(owner__isnull=True) | Q(owner=user))


def apply_inbound_lead_visibility(queryset, user):
    """Ограничивает queryset входящих лидов."""
    if is_owner_like(user):
        return queryset
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    if is_consulting_supervisor(user):
        user_regions = get_user_region_codes(user)
        return queryset.filter(region_code__in=user_regions)
    if getattr(user, "can_view_leads_inbox", False):
        return queryset.filter(owner=user)
    return queryset.none()


def apply_client_visibility(queryset, user):
    """Видимость клиентов: продавец видит ничьих (salesperson=None) + своих,
    руководитель — всех (поверх company/branch). Аналогично видимости лидов."""
    if is_owner_like(user):
        return queryset
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(Q(salesperson__isnull=True) | Q(salesperson=user))


def can_see_lead_owner(viewer, lead_or_owner_id) -> bool:
    """Может ли viewer видеть карточку (для WS-фильтра)."""
    if not lead_or_owner_id:
        return True  # пул — виден всем
    if is_owner_like(viewer):
        return True
    if is_consulting_supervisor(viewer):
        user_regions = get_user_region_codes(viewer)
        if hasattr(lead_or_owner_id, "region_code"):
            return getattr(lead_or_owner_id, "region_code", "") in user_regions
        return True
    owner_id = getattr(lead_or_owner_id, "owner_id", lead_or_owner_id)
    return str(getattr(viewer, "id", "")) == str(owner_id)

