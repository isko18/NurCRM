"""
Мост Building → 1С.

Здесь живёт building-специфичный маппинг денежных объектов в документы 1С.
Сам транспорт (outbox, HTTP-клиент, Celery, ретраи) — в apps/onec.

Правило: выгружаем деньги в момент проведения (approved/confirmed), а не создания
черновика. Функции безопасны: если интеграция 1С выключена — просто no-op.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _iso(dt):
    return dt.isoformat() if dt else None


def sync_cashflow(cashflow, *, operation: str = "create") -> None:
    """
    Движение по кассе → ПКО (приход) / РКО (расход) в 1С.

    Вызывать внутри транзакции. Отправляется только APPROVED-движение.
    """
    try:
        from apps.onec.services import enqueue_push
        from apps.onec.models import OneCIntegration
    except Exception:
        return  # apps/onec не установлен — интеграция недоступна, тихо выходим

    if cashflow.status != cashflow.Status.APPROVED:
        return

    integration = None
    try:
        integration = OneCIntegration.objects.filter(
            company_id=cashflow.company_id, is_enabled=True
        ).first()
    except Exception:
        return
    if not integration:
        return

    is_income = cashflow.type == cashflow.Type.INCOME
    cashbox = getattr(cashflow, "cashbox", None)

    payload = {
        "external_id": str(cashflow.id),
        "source_type": "cashflow",
        "operation": operation,
        "doc_kind": "pko" if is_income else "rko",
        "occurred_at": _iso(cashflow.created_at),
        "company": {
            "id": str(cashflow.company_id),
            "name": getattr(getattr(cashflow, "company", None), "name", ""),
        },
        "cashbox": {
            "id": str(cashflow.cashbox_id) if cashflow.cashbox_id else None,
            "name": getattr(cashbox, "name", "") or str(cashbox or ""),
        },
        "branch_id": str(cashflow.branch_id) if cashflow.branch_id else None,
        "type": cashflow.type,
        "name": cashflow.name or "",
        "amount": str(cashflow.amount),
        "currency": integration.currency,
        "meta": {
            "source_business_operation_id": cashflow.source_business_operation_id,
            "cashier_id": str(cashflow.cashier_id) if cashflow.cashier_id else None,
        },
    }

    enqueue_push(
        company_id=cashflow.company_id,
        source_type="cashflow",
        source_id=cashflow.id,
        payload=payload,
        endpoint="/documents/cashflow",
        operation=operation,
        onec_doc_type="ПКО" if is_income else "РКО",
    )
