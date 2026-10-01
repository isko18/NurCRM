"""Периодические задачи по кассовым сменам."""

import logging

from celery import shared_task

from .services_shifts import close_stale_shifts, close_shifts_for_deleted_cashiers

logger = logging.getLogger(__name__)


@shared_task(name="apps.construction.tasks.auto_close_stale_shifts")
def auto_close_stale_shifts(max_age_hours: int = 24):
    """
    Автозакрытие зависших смен (открыты дольше суток) + подстраховка по удалённым кассирам.
    Запускается celery beat (см. CELERY_BEAT_SCHEDULE). Best-effort: сбои по отдельным
    сменам не роняют задачу.
    """
    stale = close_stale_shifts(max_age_hours=max_age_hours)
    deleted = close_shifts_for_deleted_cashiers()
    summary = {
        "stale_closed": len(stale["closed"]),
        "stale_failed": len(stale["failed"]),
        "deleted_closed": len(deleted["closed"]),
        "deleted_failed": len(deleted["failed"]),
    }
    logger.info("auto_close_stale_shifts: %s", summary)
    return summary
