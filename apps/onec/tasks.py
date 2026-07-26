from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .client import OneCHttpClient
from .exceptions import OneCAPIError
from .models import OneCIntegration, OneCSyncRecord

logger = logging.getLogger(__name__)


def _backoff(retries: int) -> int:
    """Экспоненциальный backoff: 30с, 60, 120, ... с потолком в 1 час."""
    return min(30 * (2 ** max(retries, 0)), 3600)


@shared_task(bind=True, max_retries=6, default_retry_delay=30)
def push_to_1c(self, sync_id: str):
    """
    Идемпотентно отправить одну запись outbox в 1С.

    Ретраи (сеть / 5xx / 401 / 429) — экспоненциальный backoff силами Celery.
    Бизнес-ошибка 1С (4xx с телом) → сразу failed без бессмысленных ретраев.
    """
    rec = OneCSyncRecord.objects.filter(id=sync_id).first()
    if not rec:
        return
    if rec.status in (OneCSyncRecord.Status.SENT, OneCSyncRecord.Status.POSTED, OneCSyncRecord.Status.SKIPPED):
        return

    integration = OneCIntegration.objects.filter(company_id=rec.company_id, is_enabled=True).first()
    if not integration or not integration.is_ready():
        rec.status = OneCSyncRecord.Status.SKIPPED
        rec.last_error = "Интеграция с 1С выключена или не настроена."
        rec.save(update_fields=["status", "last_error", "updated_at"])
        return

    rec.status = OneCSyncRecord.Status.SENDING
    rec.attempts = (rec.attempts or 0) + 1
    rec.save(update_fields=["status", "attempts", "updated_at"])

    try:
        client = OneCHttpClient(integration)
        data = client.request_json("POST", rec.endpoint or "/documents/generic", json_body=rec.request_payload)
    except OneCAPIError as exc:
        rec.last_error = f"{exc} | {exc.payload}"
        if exc.is_business_error:
            rec.status = OneCSyncRecord.Status.FAILED
            rec.save(update_fields=["status", "last_error", "updated_at"])
            return
        rec.status = OneCSyncRecord.Status.PENDING
        rec.save(update_fields=["status", "last_error", "updated_at"])
        raise self.retry(exc=exc, countdown=_backoff(self.request.retries))
    except Exception as exc:  # сеть/таймаут
        rec.last_error = str(exc)
        rec.status = OneCSyncRecord.Status.PENDING
        rec.save(update_fields=["status", "last_error", "updated_at"])
        raise self.retry(exc=exc, countdown=_backoff(self.request.retries))

    rec.response_payload = data if isinstance(data, dict) else {"raw": data}
    rec.onec_external_id = str(data.get("onec_id") or data.get("external_id") or "").strip()
    rec.onec_number = str(data.get("number") or "").strip()
    if data.get("posted"):
        rec.status = OneCSyncRecord.Status.POSTED
        rec.onec_posted_at = timezone.now()
    else:
        rec.status = OneCSyncRecord.Status.SENT
    rec.last_error = ""
    rec.save(
        update_fields=[
            "response_payload", "onec_external_id", "onec_number",
            "status", "onec_posted_at", "last_error", "updated_at",
        ]
    )
