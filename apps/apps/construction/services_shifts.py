"""
Обслуживание кассовых смен: системное автозакрытие.

Используется:
- при удалении сотрудника (закрытие в той же транзакции — ошибка откатывает удаление);
- management-командой `close_stale_shifts` (ретро-закрытие смен удалённых кассиров / зависших);
- периодической celery-задачей (смена открыта дольше суток → закрыть).
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import CashShift

logger = logging.getLogger(__name__)

# Причины автозакрытия (пишутся в CashShift.close_reason).
REASON_EMPLOYEE_DELETED = "employee_deleted"
REASON_TIMEOUT_24H = "auto_timeout_24h"

DEFAULT_MAX_AGE_HOURS = 24


def close_shift_as_system(shift: CashShift, reason: str, *, by_user=None) -> CashShift:
    """
    Закрыть одну открытую смену «по ожидаемому остатку» (расхождение по кассе = 0),
    штатным образом (итоги/время/статус). Ошибки НЕ подавляются — пусть решает вызывающий.
    """
    expected_cash = shift.calc_live_totals()["expected_cash"]
    shift.close(closing_cash=expected_cash, close_reason=reason)
    logger.info(
        "Автозакрытие смены %s (кассир=%s, касса=%s), причина=%s, инициатор=%s",
        shift.pk, shift.cashier_id, shift.cashbox_id, reason, getattr(by_user, "pk", None),
    )
    return shift


def close_open_shifts(shifts_qs, reason: str, *, by_user=None, dry_run=False) -> dict:
    """
    Пакетное закрытие открытых смен из queryset. Каждая смена — в своей транзакции,
    ошибка одной не прерывает остальные (best-effort, для команд/периодических задач).

    Возвращает {"closed": [pk...], "failed": [(pk, error)...]}.
    """
    ids = list(shifts_qs.filter(status=CashShift.Status.OPEN).values_list("pk", flat=True))
    result = {"closed": [], "failed": []}

    if dry_run:
        result["closed"] = ids
        return result

    for pk in ids:
        try:
            with transaction.atomic():
                shift = CashShift.objects.select_for_update().get(pk=pk)
                if shift.status != CashShift.Status.OPEN:
                    continue  # уже закрыта параллельно
                close_shift_as_system(shift, reason, by_user=by_user)
            result["closed"].append(pk)
        except Exception as e:  # noqa: BLE001 — одна плохая смена не должна валить весь батч
            logger.exception("Не удалось автозакрыть смену %s: %s", pk, e)
            result["failed"].append((pk, str(e)))

    return result


def close_shifts_for_deleted_cashiers(*, by_user=None, dry_run=False) -> dict:
    """Закрыть открытые смены всех удалённых (soft-deleted) кассиров."""
    qs = CashShift.objects.filter(
        status=CashShift.Status.OPEN,
        cashier__deleted_at__isnull=False,
    )
    return close_open_shifts(qs, REASON_EMPLOYEE_DELETED, by_user=by_user, dry_run=dry_run)


def close_stale_shifts(*, max_age_hours: int = DEFAULT_MAX_AGE_HOURS, now=None, dry_run=False) -> dict:
    """Закрыть смены, открытые дольше max_age_hours (по умолчанию сутки)."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=max_age_hours)
    qs = CashShift.objects.filter(
        status=CashShift.Status.OPEN,
        opened_at__lte=cutoff,
    )
    return close_open_shifts(qs, REASON_TIMEOUT_24H, dry_run=dry_run)
