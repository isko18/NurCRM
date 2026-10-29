"""
Фоновые задачи умной допродажи (ТЗ, часть 7, раздел 2).

Beat (core/settings.py):
- apps.main.recommendations_tasks.dispatch_recommendation_pairs   — 00:00 ежедневно
- apps.main.recommendations_tasks.purge_old_recommendation_events — раз в сутки (ночью)
"""
import hashlib
import logging
from datetime import timedelta

from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.utils import timezone

logger = logging.getLogger("main.recommendations")

# Разброс расчёта пар по компаниям: 00:00–06:00 (dispatcher стартует в 00:00).
PAIRS_SPREAD_SECONDS = 6 * 60 * 60
RETENTION_MONTHS = 13
PURGE_CHUNK = 5000


def company_pairs_countdown(company_id) -> int:
    """Детерминированная задержка 0..21599 с: каждая компания каждую ночь в своё время."""
    digest = hashlib.md5(str(company_id).encode()).hexdigest()
    return int(digest, 16) % PAIRS_SPREAD_SECONDS


@shared_task(name="apps.main.recommendations_tasks.dispatch_recommendation_pairs")
def dispatch_recommendation_pairs():
    """
    Ставит расчёт пар для каждой компании с продажами за 90 дней — с разбросом 00:00–06:00,
    а не всеми сразу.
    """
    from apps.main.models import RecommendationPairsCache, Sale
    from apps.main.recommendations import PAIRS_DAYS

    since = timezone.now() - timedelta(days=PAIRS_DAYS)
    company_ids = set(
        Sale.objects.filter(created_at__gte=since, company__is_active=True)
        .values_list("company_id", flat=True)
        .distinct()
    )
    # У кого уже есть результат — пересчитать тоже (продажи могли выпасть из окна 90 дней).
    company_ids |= set(
        RecommendationPairsCache.objects.filter(company__is_active=True).values_list("company_id", flat=True)
    )
    scheduled = 0
    for cid in company_ids:
        compute_company_recommendation_pairs_task.apply_async(
            args=[str(cid)], countdown=company_pairs_countdown(cid)
        )
        scheduled += 1
    return {"scheduled_companies": scheduled}


@shared_task(
    name="apps.main.recommendations_tasks.compute_company_recommendation_pairs_task",
    soft_time_limit=600,
    time_limit=900,
)
def compute_company_recommendation_pairs_task(company_id):
    """Расчёт и сохранение пар одной компании + досвязка событий с поздно пришедшими продажами."""
    from apps.main.recommendations import resolve_pending_sales, store_company_recommendation_pairs

    try:
        resolve_pending_sales(company_id)
    except Exception:
        logger.exception("resolve_pending_sales failed for company %s", company_id)
    try:
        n = store_company_recommendation_pairs(company_id)
    except Exception:
        logger.exception("Failed to compute recommendation pairs for company %s", company_id)
        raise
    return {"company_id": str(company_id), "products_with_pairs": n}


@shared_task(name="apps.main.recommendations_tasks.purge_old_recommendation_events")
def purge_old_recommendation_events():
    """Журнал событий хранится 13 месяцев; удаляем старше — пачками, без долгих блокировок."""
    from apps.main.models import RecommendationEvent

    cutoff = timezone.now() - relativedelta(months=RETENTION_MONTHS)
    deleted = 0
    while True:
        ids = list(
            RecommendationEvent.objects.filter(occurred_at__lt=cutoff)
            .values_list("id", flat=True)[:PURGE_CHUNK]
        )
        if not ids:
            break
        deleted += RecommendationEvent.objects.filter(id__in=ids).delete()[0]
    return {"deleted": deleted}
