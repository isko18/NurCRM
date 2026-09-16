from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.main.models import Notification, Product
from apps.main.realtime import company_group_name, user_group_name
from apps.users.models import Company

logger = logging.getLogger("crm.product_expiry")


def send_product_expiry_digest_for_company(company, today=None):
    """
    Проверяет товары компании на истечение срока годности (expired, critical, warning)
    и создаёт одно агрегированное уведомление (market.product.expiring) в день.
    Идемпотентно: повторный запуск в тот же день не создаёт дубликат.
    """
    if today is None:
        today = timezone.localdate()

    # 1. Проверка на дублирование в этот день
    already_sent = Notification.objects.filter(
        company=company,
        type="market.product.expiring",
        created_at__date=today,
    ).exists()
    if already_sent:
        logger.info("Expiry digest already sent today for company %s", company.id)
        return None

    # 2. Поиск истекающих / просроченных товаров
    max_date = today + timedelta(days=14)
    exp_products = list(
        Product.objects.filter(
            company=company,
            expiration_date__isnull=False,
            expiration_date__lte=max_date,
        )
    )

    expired_count = 0
    critical_count = 0
    warning_count = 0

    for p in exp_products:
        days_left = (p.expiration_date - today).days
        if days_left < 0:
            expired_count += 1
        elif 0 <= days_left <= 3:
            critical_count += 1
        elif 3 < days_left <= 14:
            warning_count += 1

    total_count = expired_count + critical_count + warning_count
    if total_count == 0:
        # У компании нет истекающих товаров — ничего не публикуем
        return None

    # 3. Формирование заголовка и текста
    title = f"Истекает срок годности: {total_count} тов."
    if expired_count > 0 and (critical_count + warning_count) > 0:
        message = f"{expired_count} тов. уже просрочено, ещё {critical_count + warning_count} — в ближайшие 14 дней."
    elif expired_count > 0:
        message = f"{expired_count} тов. уже просрочено."
    else:
        message = f"{critical_count + warning_count} тов. истекают в ближайшие 14 дней."

    level = (
        Notification.Level.CRITICAL
        if expired_count > 0
        else Notification.Level.WARNING
    )

    recipient = getattr(company, "owner", None)
    if not recipient:
        recipient = getattr(company, "users", None)
        if recipient and hasattr(recipient, "filter"):
            recipient = recipient.filter(is_active=True).first()
    if not recipient:
        logger.warning("No recipient user found for company %s", company.id)
        return None

    meta = {
        "company_id": str(company.id),
        "expired_count": expired_count,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "source_kind": "product_expiry_digest",
        "cta_label": "Открыть",
    }

    notif = Notification.objects.create(
        company=company,
        user=recipient,
        title=title,
        message=message,
        category=Notification.Category.SYSTEM,
        type="market.product.expiring",
        level=level,
        url="/crm/market/analytics#products-section-expiringProducts",
        data=meta,
    )

    # Публикация в Channels
    def _publish_ws():
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            layer = get_channel_layer()
            if not layer:
                return

            ws_data = {
                "id": str(notif.id),
                "type": "market.product.expiring",
                "title": notif.title,
                "message": notif.message,
                "category": notif.category,
                "level": notif.level,
                "is_read": False,
                "created_at": notif.created_at.isoformat(),
                "url": notif.url,
                "cta_label": "Открыть",
                "meta": meta,
            }

            groups = [
                company_group_name(company.id),
                user_group_name(recipient.id),
            ]
            for g in groups:
                async_to_sync(layer.group_send)(
                    g,
                    {
                        "type": "notify",
                        "data": ws_data,
                    },
                )
        except Exception as e:
            logger.warning("Failed to broadcast market.product.expiring ws: %s", e)

    try:
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(_publish_ws)
        else:
            _publish_ws()
    except Exception:
        _publish_ws()

    return notif


def send_product_expiry_digest_for_all_companies():
    """
    Ночной cron-запуск дайджеста по всем компаниям.
    """
    today = timezone.localdate()
    companies = Company.objects.all()
    created = []
    for company in companies:
        try:
            n = send_product_expiry_digest_for_company(company, today=today)
            if n:
                created.append(n)
        except Exception as e:
            logger.error("Error sending expiry digest for company %s: %s", company.id, e, exc_info=True)
    return len(created)
