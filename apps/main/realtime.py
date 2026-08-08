"""
Real-time доставка уведомлений через Django Channels (doc 08).

Группы (channel groups) — без двоеточий (ограничение Channels):
  notif_user_<user_id>      — личные уведомления получателя
  notif_company_<id>        — общая компания
  notif_branch_<id>         — филиал/подразделение
  notif_role_<role>         — роль
  notif_agent_<id>          — агент

Сейчас доставка идёт в личную группу получателя (минимально необходимый набор —
broadcast запрещён). company/role/branch группы зарезервированы для будущей
широковещательной маршрутизации.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger("nurcrm.websocket.notifications")


def user_group_name(user_id) -> str:
    return f"notif_user_{user_id}"


def company_group_name(company_id) -> str:
    return f"notif_company_{company_id}"


def role_group_name(role) -> str:
    return f"notif_role_{role}"


def branch_group_name(branch_id) -> str:
    return f"notif_branch_{branch_id}"


def agent_group_name(agent_id) -> str:
    return f"notif_agent_{agent_id}"


def notification_payload(notification) -> dict:
    """Сериализация уведомления для WS и REST (единый контракт)."""
    actor = getattr(notification, "actor", None)
    actor_name = ""
    if actor is not None:
        actor_name = (
            f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip()
            or getattr(actor, "email", "") or ""
        )
    return {
        "id": str(notification.id),
        "category": getattr(notification, "category", "other"),
        "type": notification.type,
        "title": notification.title or "",
        "message": notification.message or "",
        "url": notification.url or "",
        "level": notification.level,
        "is_read": bool(notification.is_read),
        "actor_name": actor_name,
        "data": notification.data or {},
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


def publish_notification(notification) -> None:
    """Публикует уведомление в личную группу получателя (best-effort)."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        layer = get_channel_layer()
        if layer is None:
            logger.warning("Channel layer is None, cannot publish notification %s", notification.id)
            return

        group = user_group_name(notification.user_id)
        logger.info(
            "[REALTIME NOTIFICATION] Sending WS notification id=%s to group=%s for user_id=%s title='%s'",
            notification.id, group, notification.user_id, notification.title
        )

        async_to_sync(layer.group_send)(
            group,
            {"type": "notify", "data": notification_payload(notification)},
        )
    except Exception:
        logger.error("Failed to publish notification id=%s over WS", getattr(notification, "id", None), exc_info=True)


def create_and_publish_notification(*, company, user, message, title="", category="other", type="system",
                                    level="info", url="", actor=None, branch=None, data=None):
    """
    Создаёт Notification и публикует его в WS после коммита транзакции.
    Возвращает созданный объект (или None при ошибке создания — best-effort у вызывающих).
    """
    from apps.main.models import Notification

    cat = category if category != "other" else (type if type in ("tariff", "system", "news") else "other")

    notification = Notification.objects.create(
        company=company,
        branch=branch,
        user=user,
        message=message,
        title=title or "",
        category=cat,
        type=type or "system",
        level=level or "info",
        url=url or "",
        actor=actor,
        data=data or {},
    )

    def _publish():
        publish_notification(notification)

    try:
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(_publish)
        else:
            _publish()
    except Exception:
        _publish()

    return notification


def check_and_create_tariff_notifications():
    """
    Проверяет даты окончания подписок компаний (Company.end_date)
    и создаёт уведомления категории 'tariff' за 7, 3 и 1 день до окончания.
    """
    from apps.users.models import Company
    from apps.main.models import Notification
    from django.utils import timezone

    now = timezone.now()
    today = now.date()

    companies = Company.objects.filter(end_date__isnull=False, owner__isnull=False)
    created_list = []
    for company in companies:
        days_left = (company.end_date.date() - today).days
        if days_left in (7, 3, 1):
            title = f"Срок подписки истекает через {days_left} дн."
            already_sent = Notification.objects.filter(
                company=company,
                user=company.owner,
                category="tariff",
                created_at__date=today,
                title=title,
            ).exists()
            if not already_sent:
                notif = create_and_publish_notification(
                    company=company,
                    user=company.owner,
                    title=title,
                    message=f"До окончания подписки компании '{company.name}' осталось {days_left} дн. Пожалуйста, продлите тариф.",
                    category="tariff",
                    type="tariff",
                    level=Notification.Level.HIGH if days_left <= 3 else Notification.Level.WARNING,
                    url="/crm/subscription",
                    data={
                        "days_left": days_left,
                        "cta_label": "Продлить",
                        "cta_url": "/crm/subscription",
                    },
                )
                if notif:
                    created_list.append(notif)
    return created_list
