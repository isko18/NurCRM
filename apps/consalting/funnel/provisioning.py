"""Создание системных воронок consalting: основной и ролевых.

- Основная воронка компании (`is_main=True`) — одна на компанию.
- Воронка роли (`custom_role != null`) — одна на каждую кастомную роль.

Обе — статичные (`is_static=True`, `is_protected`): их нельзя удалять/переименовывать
через обычный API. Каждая получает ровно 3 системные стадии (intake / in_progress /
completed), которые нельзя менять (`is_system=True`).
"""
from django.db import transaction

from ..models import FunnelConsalting, FunnelStageConsalting

T = FunnelStageConsalting.StageType

# Системные стадии (раздел 1.2 спецификации). Порядок важен: 0,1,2.
SYSTEM_STAGES = [
    {"system_key": "intake",      "name": "Новые заявки", "order": 0, "stage_type": T.NEW_LEAD, "color": "#3b82f6"},
    {"system_key": "in_progress", "name": "В работе",      "order": 1, "stage_type": T.NURTURE,  "color": "#f59e0b"},
    {"system_key": "completed",   "name": "Завершено",     "order": 2, "stage_type": T.WON,      "color": "#16a34a"},
]


def _create_system_stages(funnel):
    """Создаёт 3 системные стадии для воронки (идемпотентно по system_key)."""
    existing = set(
        funnel.stages.filter(is_system=True).values_list("system_key", flat=True)
    )
    to_create = [
        FunnelStageConsalting(
            company_id=funnel.company_id,
            branch_id=funnel.branch_id,
            funnel=funnel,
            name=s["name"],
            order=s["order"],
            color=s["color"],
            stage_type=s["stage_type"],
            is_system=True,
            system_key=s["system_key"],
        )
        for s in SYSTEM_STAGES
        if s["system_key"] not in existing
    ]
    # bulk_create нельзя: save() синхронизирует is_final/is_success из stage_type
    for stage in to_create:
        stage.save()
    return funnel.stages.filter(is_system=True).order_by("order")


@transaction.atomic
def provision_main_funnel(company):
    """Создаёт (или возвращает существующую) основную воронку компании."""
    funnel = FunnelConsalting.objects.filter(company=company, is_main=True).first()
    if funnel:
        return funnel, False
    funnel = FunnelConsalting.objects.create(
        company=company,
        branch=None,
        name="Основная воронка",
        description="Основная воронка продаж компании",
        funnel_kind=FunnelConsalting.FunnelKind.MAIN,
        is_main=True,
        is_static=True,
        is_active=True,
    )
    _create_system_stages(funnel)
    return funnel, True


@transaction.atomic
def provision_funnel_for_role(role, name=None):
    """Создаёт (или возвращает существующую) воронку для кастомной роли.

    Возвращает кортеж (funnel, created).
    """
    company = role.company
    if not company:
        return None, False

    funnel = FunnelConsalting.objects.filter(company=company, custom_role=role).first()
    if funnel:
        return funnel, False

    funnel = FunnelConsalting.objects.create(
        company=company,
        branch=None,
        name=name or role.name,
        description=f"Воронка продаж для роли «{role.name}»",
        funnel_kind=FunnelConsalting.FunnelKind.ROLE,
        is_static=True,
        is_active=True,
        custom_role=role,
    )
    _create_system_stages(funnel)
    return funnel, True
