from __future__ import annotations

import logging

from django.db import transaction

from .models import OneCIntegration, OneCSyncRecord

logger = logging.getLogger(__name__)


def get_active_integration(company_id) -> OneCIntegration | None:
    if not company_id:
        return None
    return OneCIntegration.objects.filter(company_id=company_id, is_enabled=True).first()


def enqueue_push(
    *,
    company_id,
    source_type: str,
    source_id,
    payload: dict,
    endpoint: str,
    operation: str = OneCSyncRecord.Operation.CREATE,
    onec_doc_type: str = "",
) -> OneCSyncRecord | None:
    """
    Поставить денежную операцию в очередь на выгрузку в 1С.

    Вызывать ВНУТРИ транзакции бизнес-операции. Реальная отправка — после commit
    (transaction.on_commit), поэтому незакоммиченные данные в 1С не уходят.

    Идемпотентно по ключу {source_type}:{source_id}:{operation} — повторный вызов
    не создаёт дубль документа в 1С, а обновляет payload и пере-ставит в очередь
    (если запись ещё не отправлена).
    """
    integration = get_active_integration(company_id)
    if not integration or not integration.source_enabled(source_type):
        return None

    key = f"{source_type}:{source_id}:{operation}"
    rec, created = OneCSyncRecord.objects.get_or_create(
        idempotency_key=key,
        defaults=dict(
            company_id=company_id,
            source_type=source_type,
            source_id=str(source_id),
            operation=operation,
            endpoint=endpoint,
            onec_doc_type=onec_doc_type,
            request_payload=payload,
            status=OneCSyncRecord.Status.PENDING,
        ),
    )

    if not created:
        # Уже успешно отправлено — не трогаем (защита от повторной выгрузки).
        if rec.status in (OneCSyncRecord.Status.SENT, OneCSyncRecord.Status.POSTED):
            return rec
        # Иначе освежаем снапшот и пере-ставим в очередь.
        rec.request_payload = payload
        rec.endpoint = endpoint
        rec.onec_doc_type = onec_doc_type
        rec.status = OneCSyncRecord.Status.PENDING
        rec.last_error = ""
        rec.save(update_fields=["request_payload", "endpoint", "onec_doc_type", "status", "last_error", "updated_at"])

    transaction.on_commit(lambda: _dispatch(rec.id))
    return rec


def _dispatch(sync_id):
    """Поставить Celery-задачу. Если брокер недоступен — запись остаётся pending (ручной/повторный retry)."""
    try:
        from .tasks import push_to_1c

        push_to_1c.delay(str(sync_id))
    except Exception:
        logger.exception("onec: не удалось поставить задачу push_to_1c для %s", sync_id)
