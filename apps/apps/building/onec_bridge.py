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


def _treaty_company_id(treaty):
    if getattr(treaty, "company_id", None):
        return treaty.company_id
    rc = getattr(treaty, "residential_complex", None)
    return getattr(rc, "company_id", None)


def sync_treaty(treaty, *, operation: str = "create"):
    """
    Договор → документ «Договор/Реализация» в 1С.

    Возвращает OneCSyncRecord (или None, если интеграция выключена) — вызывающий код
    использует это, чтобы выставить treaty.erp_sync_status (REQUESTED / NOT_CONFIGURED).
    """
    try:
        from apps.onec.services import enqueue_push, get_active_integration
    except Exception:
        return None

    company_id = _treaty_company_id(treaty)
    integration = get_active_integration(company_id)
    if not integration:
        return None

    rc = getattr(treaty, "residential_complex", None)
    apartment = getattr(treaty, "apartment", None)
    client = getattr(treaty, "client", None)
    counterparty_name = (
        getattr(client, "name", None) if treaty.client_id else (treaty.client_name or "")
    )

    payload = {
        "external_id": str(treaty.id),
        "source_type": "treaty",
        "operation": operation,
        "occurred_at": _iso(getattr(treaty, "signed_at", None) or treaty.created_at),
        "number": treaty.number or "",
        "title": treaty.title or "",
        "description": treaty.description or "",
        "amount": str(treaty.amount),
        "currency": integration.currency,
        "status": treaty.status,
        "operation_type": treaty.operation_type,
        "payment_type": treaty.payment_type,
        "payment_mode": treaty.payment_mode,
        "down_payment": str(treaty.down_payment),
        "company": {"id": str(company_id) if company_id else None,
                    "name": getattr(getattr(treaty, "company", None), "name", "")},
        "residential_complex": (
            {"id": str(treaty.residential_complex_id), "name": getattr(rc, "name", "")}
            if treaty.residential_complex_id else None
        ),
        "apartment": (
            {"id": str(treaty.apartment_id), "number": getattr(apartment, "number", "")}
            if treaty.apartment_id else None
        ),
        "counterparty": {
            "type": "client",
            "id": str(treaty.client_id) if treaty.client_id else None,
            "name": counterparty_name or "",
        },
    }

    return enqueue_push(
        company_id=company_id,
        source_type="treaty",
        source_id=treaty.id,
        payload=payload,
        endpoint="/documents/treaty",
        operation=operation,
        onec_doc_type="Договор",
    )


def sync_debt(entry, *, operation: str = "create"):
    """
    Запись реестра долгов → «КорректировкаДолга» в 1С.

    Покрывает и долги, и бартер (подтверждение бартерного зачёта создаёт запись
    реестра с entry_type=barter). Оплаты (entry_type=payment) НЕ выгружаются: они
    уже уходят в 1С как ПКО/РКО через кассу — иначе задвоили бы уменьшение долга.
    """
    try:
        from apps.onec.services import enqueue_push, get_active_integration
        from .models import BuildingDebtLedgerEntry
    except Exception:
        return None

    if entry.status != BuildingDebtLedgerEntry.Status.APPROVED:
        return None
    if entry.entry_type == BuildingDebtLedgerEntry.EntryType.PAYMENT:
        return None  # оплаты покрыты кассой (ПКО/РКО), не дублируем

    integration = get_active_integration(entry.company_id)
    if not integration:
        return None

    rc = getattr(entry, "residential_complex", None)
    payload = {
        "external_id": str(entry.id),
        "source_type": "debt",
        "operation": operation,
        "occurred_at": _iso(entry.occurred_at),
        "direction": entry.direction,          # payable | receivable
        "entry_type": entry.entry_type,        # charge | barter | adjustment | writeoff
        "counterparty": {
            "type": entry.counterparty_type,   # client | supplier | contractor
            "id": str(entry.counterparty_id),
        },
        "amount": str(entry.amount),
        "currency": entry.currency or integration.currency,
        "company": {"id": str(entry.company_id),
                    "name": getattr(getattr(entry, "company", None), "name", "")},
        "residential_complex": (
            {"id": str(entry.residential_complex_id), "name": getattr(rc, "name", "")}
            if entry.residential_complex_id else None
        ),
        "source": {"type": entry.source_type or "", "id": str(entry.source_id) if entry.source_id else None},
        "comment": entry.comment or "",
    }

    return enqueue_push(
        company_id=entry.company_id,
        source_type="debt",
        source_id=entry.id,
        payload=payload,
        endpoint="/documents/debt-adjustment",
        operation=operation,
        onec_doc_type="КорректировкаДолга",
    )


def on_document_posted(sender, sync_record=None, **kwargs):
    """
    Обработчик сигнала onec.document_posted: 1С подтвердила проведение документа.
    Пишем результат обратно в building-объект (пока — договоры: erp_* → SYNCED).
    """
    if not sync_record:
        return
    if sync_record.source_type != "treaty":
        return
    try:
        from .models import BuildingTreaty
    except Exception:
        return
    treaty = BuildingTreaty.objects.filter(id=sync_record.source_id).first()
    if not treaty:
        return
    treaty.erp_sync_status = BuildingTreaty.ErpSyncStatus.SYNCED
    treaty.erp_external_id = sync_record.onec_external_id or treaty.erp_external_id
    treaty.erp_synced_at = sync_record.onec_posted_at
    treaty.erp_last_error = ""
    treaty.save(update_fields=["erp_sync_status", "erp_external_id", "erp_synced_at", "erp_last_error", "updated_at"])
