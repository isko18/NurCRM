# apps/warehouse/salary_services.py
"""
Зарплата агентов: начисление процента с продаж по складам-источникам,
переходы статусов и выплаты (FIFO-закрытие).

Источник продаж — документ склада `Document` (doc_type=SALE) с `agent`.
Склад-источник строки берётся через `services.resolve_item_warehouse`
(мультисклад: product.warehouse с откатом на document.warehouse_from).
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction, IntegrityError
from django.db.models import Sum

from apps.warehouse import models as m

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class SalaryBalanceError(Exception):
    """Сумма выплаты превышает баланс агента."""

    def __init__(self, balance: Decimal):
        self.balance = _q2(balance)
        super().__init__(str(self.balance))


def _q2(x) -> Decimal:
    return Decimal(x or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _is_credit(document) -> bool:
    return (getattr(document, "payment_kind", None) or "cash").strip().lower() == "credit"


def _rate_for(company_id, warehouse):
    return (
        m.WarehouseSalaryRate.objects
        .filter(company_id=company_id, warehouse=warehouse)
        .first()
    )


def _is_agent_sale(document) -> bool:
    return (
        document is not None
        and document.doc_type == m.Document.DocType.SALE
        and document.status == m.Document.Status.POSTED
        and bool(document.agent_id)
    )


# ─────────────────────────────────────────────────────────────
# Создание начислений при проведении продажи
# ─────────────────────────────────────────────────────────────
@transaction.atomic
def create_accruals_for_document(document) -> list:
    """
    Создаёт начисления по проведённому документу продажи агента.

    Группирует строки по складу-источнику, применяет снимок ставки склада
    (retail/wholesale по document.is_wholesale). Ставка 0 / нулевая сумма →
    начисление не создаётся. Идемпотентно: при наличии базовых начислений
    по документу повторно не создаёт.
    """
    if not _is_agent_sale(document):
        return []

    # Идемпотентность: есть активное (не отменённое) базовое начисление → не дублируем.
    # Отменённые (после распроведения) не блокируют повторное проведение.
    if (
        m.AgentSalaryAccrual.objects
        .filter(sale=document, is_correction=False)
        .exclude(status=m.AgentSalaryAccrual.Status.CANCELED)
        .exists()
    ):
        return []

    is_wholesale = bool(getattr(document, "is_wholesale", False))
    sale_type = (
        m.AgentSalaryAccrual.SaleType.WHOLESALE if is_wholesale
        else m.AgentSalaryAccrual.SaleType.RETAIL
    )
    status = (
        m.AgentSalaryAccrual.Status.PENDING if _is_credit(document)
        else m.AgentSalaryAccrual.Status.ACCRUED
    )

    # Импорт внутри функции — избегаем циклической зависимости services ↔ salary_services.
    from apps.warehouse.services import resolve_item_warehouse

    grouped: dict = {}
    for item in document.items.select_related("product", "product__warehouse").all():
        wh = resolve_item_warehouse(document, item)
        if wh is None:
            logger.warning(
                "salary: не удалось определить склад-источник для doc=%s item=%s — начисление пропущено",
                document.id, item.id,
            )
            continue
        entry = grouped.setdefault(wh.id, {"wh": wh, "base": Decimal("0.00")})
        entry["base"] += Decimal(item.line_total or 0)

    created = []
    for entry in grouped.values():
        wh = entry["wh"]
        base = _q2(entry["base"])
        if base <= 0:
            continue
        rate = _rate_for(wh.company_id, wh)
        percent = Decimal(rate.percent_for(is_wholesale=is_wholesale)) if rate else Decimal("0.00")
        if percent <= 0:
            continue
        amount = (base * percent / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
        if amount == 0:
            continue
        try:
            with transaction.atomic():
                created.append(m.AgentSalaryAccrual.objects.create(
                    company_id=wh.company_id,
                    agent_id=document.agent_id,
                    sale=document,
                    warehouse=wh,
                    sale_type=sale_type,
                    sale_amount=base,
                    percent=percent,
                    amount=amount,
                    status=status,
                ))
        except IntegrityError:
            # Параллельное создание базового начисления по (sale, warehouse) — пропускаем.
            logger.info("salary: базовое начисление для doc=%s wh=%s уже существует", document.id, wh.id)
    return created


# ─────────────────────────────────────────────────────────────
# Переходы статусов
# ─────────────────────────────────────────────────────────────
@transaction.atomic
def settle_document_accruals(document) -> int:
    """Долг погашен (продажа стала оплаченной): pending → accrued."""
    if not _is_agent_sale(document):
        return 0
    return (
        m.AgentSalaryAccrual.objects
        .filter(sale=document, is_correction=False, status=m.AgentSalaryAccrual.Status.PENDING)
        .update(status=m.AgentSalaryAccrual.Status.ACCRUED)
    )


@transaction.atomic
def cancel_accruals_for_document(document) -> None:
    """
    Возврат/отмена продажи (распроведение документа):
      - pending/accrued начисления → canceled;
      - уже выплаченные (paid) → создаём отрицательную корректировку (accrued),
        чтобы уменьшить будущий баланс агента; сам paid оставляем как есть.
    """
    if not _is_agent_sale(document):
        return
    accruals = list(
        m.AgentSalaryAccrual.objects
        .select_for_update()
        .filter(sale=document, is_correction=False)
    )
    for acc in accruals:
        if acc.status in (
            m.AgentSalaryAccrual.Status.PENDING,
            m.AgentSalaryAccrual.Status.ACCRUED,
        ):
            acc.status = m.AgentSalaryAccrual.Status.CANCELED
            acc.save(update_fields=["status", "updated_at"])
        elif acc.status == m.AgentSalaryAccrual.Status.PAID:
            already = m.AgentSalaryAccrual.objects.filter(
                sale=document, warehouse_id=acc.warehouse_id, is_correction=True,
            ).exists()
            if already:
                continue
            m.AgentSalaryAccrual.objects.create(
                company_id=acc.company_id,
                agent_id=acc.agent_id,
                sale=document,
                warehouse_id=acc.warehouse_id,
                sale_type=acc.sale_type,
                sale_amount=-acc.sale_amount,
                percent=acc.percent,
                amount=-acc.amount,
                status=m.AgentSalaryAccrual.Status.ACCRUED,
                is_correction=True,
            )


# ─────────────────────────────────────────────────────────────
# Баланс и выплаты
# ─────────────────────────────────────────────────────────────
def agent_balance(company_id, agent_id) -> Decimal:
    """Текущий долг перед агентом = Σ amount начислений в статусе accrued (net)."""
    total = (
        m.AgentSalaryAccrual.objects
        .filter(company_id=company_id, agent_id=agent_id, status=m.AgentSalaryAccrual.Status.ACCRUED)
        .aggregate(s=Sum("amount"))["s"]
    )
    return _q2(total or 0)


@transaction.atomic
def create_payout(*, company, agent, amount, comment: str = "", created_by=None):
    """
    Создаёт выплату и закрывает начисления агента (accrued) FIFO — от старых к новым.
    Частично покрытое начисление разбивается: закрытая часть (paid, привязана к
    выплате) + остаток (accrued). Всё в одной транзакции.
    """
    amount = _q2(amount)
    if amount <= 0:
        raise ValueError("amount_not_positive")

    accruals = list(
        m.AgentSalaryAccrual.objects
        .select_for_update()
        .filter(company=company, agent=agent, status=m.AgentSalaryAccrual.Status.ACCRUED)
        .order_by("created_at", "id")
    )
    balance = _q2(sum((Decimal(a.amount) for a in accruals), Decimal("0.00")))
    if amount > balance:
        raise SalaryBalanceError(balance)

    payout = m.AgentSalaryPayout.objects.create(
        company=company,
        agent=agent,
        amount=amount,
        comment=(comment or "").strip(),
        created_by=created_by,
    )

    remaining = amount
    for acc in accruals:
        if remaining <= 0:
            break
        acc_amount = Decimal(acc.amount)
        if acc_amount <= 0:
            # Отрицательные корректировки уменьшают баланс, но их нельзя «закрыть» выплатой.
            continue

        if acc_amount <= remaining:
            acc.status = m.AgentSalaryAccrual.Status.PAID
            acc.payout = payout
            acc.save(update_fields=["status", "payout", "updated_at"])
            remaining -= acc_amount
        else:
            # Частичное покрытие: закрытая часть остаётся на этой записи (paid),
            # остаток выносим в корректирующую запись (accrued).
            closed = remaining
            closed_sale = _q2(Decimal(acc.sale_amount) * closed / acc_amount)
            rem_sale = _q2(Decimal(acc.sale_amount) - closed_sale)
            rem_amount = acc_amount - closed

            m.AgentSalaryAccrual.objects.create(
                company_id=acc.company_id,
                agent_id=acc.agent_id,
                sale_id=acc.sale_id,
                warehouse_id=acc.warehouse_id,
                sale_type=acc.sale_type,
                sale_amount=rem_sale,
                percent=acc.percent,
                amount=rem_amount,
                status=m.AgentSalaryAccrual.Status.ACCRUED,
                is_correction=True,
            )

            acc.status = m.AgentSalaryAccrual.Status.PAID
            acc.payout = payout
            acc.sale_amount = closed_sale
            acc.amount = closed
            acc.save(update_fields=["status", "payout", "sale_amount", "amount", "updated_at"])
            remaining = Decimal("0.00")

    return payout
