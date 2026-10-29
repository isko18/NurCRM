"""
Фоновые задачи проката (ТЗ ч.7, 4.3):

    apps.main.rentals_tasks.send_overdue_rentals_notifications  — раз в день утром: ставит
        по задаче на каждую компанию с просрочками с разбросом 0–3600 сек;
    apps.main.rentals_tasks.send_company_overdue_rentals        — сообщение владельцу одной компании
        через бота на сервере: «Просроченные прокаты: №…, клиент, телефон, вещь, на сколько дней»;
    apps.main.rentals_tasks.purge_rental_documents_30_days      — очищает deposit_document
        у прокатов, возвращённых более 30 дней назад (персональные данные).
"""
from __future__ import annotations

import hashlib
import html
import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("crm.rentals")

SPREAD_SECONDS = 3600
TG_MESSAGE_LIMIT = 3900


def _spread_countdown(company_id) -> int:
    """Стабильная задержка 0–3600 сек для компании (одна и та же каждый день)."""
    digest = hashlib.sha1(str(company_id).encode()).hexdigest()
    return int(digest[:8], 16) % SPREAD_SECONDS


@shared_task(name="apps.main.rentals_tasks.send_overdue_rentals_notifications")
def send_overdue_rentals_notifications():
    from apps.main.models import Rental
    from apps.main.telegram_bot.models import TelegramBotSettings

    today = timezone.localdate()
    company_ids = set(
        Rental.objects.filter(status=Rental.Status.ACTIVE, date_to__lt=today)
        .values_list("company_id", flat=True)
        .distinct()
    )
    if not company_ids:
        return {"scheduled": 0}

    with_bot = set(
        TelegramBotSettings.objects.filter(company_id__in=company_ids)
        .exclude(owner_chat_id__isnull=True)
        .exclude(owner_chat_id="")
        .exclude(encrypted_token="")
        .values_list("company_id", flat=True)
    )
    scheduled = 0
    for cid in with_bot:
        send_company_overdue_rentals.apply_async(
            args=[str(cid), today.isoformat()],
            countdown=_spread_countdown(cid),
        )
        scheduled += 1
    return {"scheduled": scheduled}


def _rental_line(r, today) -> str | None:
    items = []
    for it in r.items.all():
        remaining = Decimal(str(it.quantity or 0)) - Decimal(str(getattr(it, "returned_quantity", 0) or 0))
        if remaining <= 0:
            continue
        name = it.product.name if it.product_id else "Вещь"
        if it.variant_id and (it.variant.size or it.variant.color):
            name += " (" + ", ".join(p for p in [it.variant.size, it.variant.color] if p) + ")"
        if remaining != 1:
            name += f" × {remaining:g}"
        items.append(name)
    if not items:
        return None
    days = (today - r.date_to).days
    client_name = r.client.full_name if r.client_id else "Клиент"
    phone = getattr(r.client, "phone", "") or "телефон не указан"
    return (
        f"• №{r.number}, {html.escape(client_name)}, {html.escape(phone)}, "
        f"{html.escape(', '.join(items))} — просрочено на {days} дн."
    )


@shared_task(name="apps.main.rentals_tasks.send_company_overdue_rentals")
def send_company_overdue_rentals(company_id: str, day: str | None = None):
    from apps.main.models import Rental
    from apps.main.telegram_bot.models import TelegramBotSettings
    from apps.main.telegram_bot.services import telegram_api

    today = timezone.localdate()
    # Не дублировать сообщение при повторной доставке задачи
    sent_key = f"rentals_overdue_sent:{company_id}:{day or today.isoformat()}"
    try:
        if not cache.add(sent_key, 1, timeout=36 * 3600):
            return {"skipped": "already_sent"}
    except Exception:
        pass

    settings = TelegramBotSettings.objects.filter(company_id=company_id).first()
    if not settings or not settings.owner_chat_id or not settings.token:
        return {"skipped": "no_bot"}

    rentals = (
        Rental.objects.filter(company_id=company_id, status=Rental.Status.ACTIVE, date_to__lt=today)
        .select_related("client")
        .prefetch_related("items__product", "items__variant")
        .order_by("date_to", "number")
    )
    lines = [ln for ln in (_rental_line(r, today) for r in rentals) if ln]
    if not lines:
        return {"sent": 0}

    header = "⚠️ <b>Просроченные прокаты:</b>"
    chunks, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 1 > TG_MESSAGE_LIMIT:
            chunks.append(cur)
            cur = header + " (продолжение)"
        cur += "\n" + ln
    chunks.append(cur)

    sent = 0
    for chunk in chunks:
        try:
            res = telegram_api.send_message(settings.token, settings.owner_chat_id, chunk, parse_mode="HTML")
            if isinstance(res, dict) and res.get("ok"):
                sent += 1
        except Exception as exc:
            logger.warning("overdue rentals notify failed for company %s: %s", company_id, exc)
    return {"sent": sent, "rentals": len(lines)}


@shared_task(name="apps.main.rentals_tasks.purge_rental_documents_30_days")
def purge_rental_documents_30_days():
    from apps.main.models import Rental

    threshold = timezone.now() - timedelta(days=30)
    updated = (
        Rental.objects.filter(
            status=Rental.Status.RETURNED,
            returned_at__lt=threshold,
        )
        .exclude(deposit_document="")
        .update(deposit_document="")
    )
    return {"purged_documents": updated}
