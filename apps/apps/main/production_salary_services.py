# apps/main/production_salary_services.py
"""
Зарплата в производстве: почасовые и сдельные начисления, выплаты (FIFO-закрытие).

Сдельное начисление создаётся из `models.record_production` — единственной точки,
через которую проходит любое производство. Почасовое — при внесении табеля.
Ставки фиксируются снимком: изменение ставки не пересчитывает прошлые начисления.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum

from apps.main.models import (
    ProductionEmployeeRate,
    ProductionPieceRate,
    ProductionSalaryAccrual,
    ProductionSalaryPayout,
    _user_display_name,
)

CENT = Decimal("0.01")


class SalaryBalanceError(Exception):
    """Сумма выплаты превышает баланс сотрудника."""

    def __init__(self, balance: Decimal):
        self.balance = _q2(balance)
        super().__init__(str(self.balance))


class AccrualPaidError(Exception):
    """Начисление уже выплачено — менять/отменять нельзя."""


def _q2(x) -> Decimal:
    return Decimal(x or 0).quantize(CENT, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────
# Сдельно: начисление за произведённую продукцию
# ─────────────────────────────────────────────────────────────
@transaction.atomic
def create_piece_accrual(record):
    """
    Начисляет сдельную зарплату автору производства.
    Ставка не задана или равна нулю → начисление не создаётся.
    """
    if record is None or not record.produced_by_id or not record.product_id:
        return None

    rate = (
        ProductionPieceRate.objects
        .filter(company_id=record.company_id, product_id=record.product_id)
        .first()
    )
    amount_per_unit = _q2(rate.amount_per_unit) if rate else Decimal("0.00")
    if amount_per_unit <= 0:
        return None

    quantity = Decimal(str(record.quantity or 0))
    amount = _q2(quantity * amount_per_unit)
    if amount <= 0:
        return None

    return ProductionSalaryAccrual.objects.create(
        company_id=record.company_id,
        employee_id=record.produced_by_id,
        kind=ProductionSalaryAccrual.Kind.PIECE,
        production_record=record,
        product_id=record.product_id,
        quantity=quantity,
        amount_per_unit=amount_per_unit,
        amount=amount,
    )


# Отмены/удаления производства в API пока нет (в /production/report/ только чтение),
# поэтому хука снятия сдельных начислений тоже нет — добавим вместе с таким потоком.


# ─────────────────────────────────────────────────────────────
# Почасово: начисление по табелю
# ─────────────────────────────────────────────────────────────
@transaction.atomic
def upsert_hourly_accrual(session):
    """
    Создаёт/обновляет почасовое начисление по табелю (одно на сессию).
    Правка табеля пересчитывает начисление, пока оно не оплачено;
    оплаченное трогать нельзя (AccrualPaidError).
    Ставка не задана → начисление не создаётся.
    """
    existing = (
        ProductionSalaryAccrual.objects
        .select_for_update()
        .filter(work_session=session)
        .exclude(status=ProductionSalaryAccrual.Status.CANCELED)
        .first()
    )
    if existing and existing.status == ProductionSalaryAccrual.Status.PAID:
        raise AccrualPaidError()

    rate_row = (
        ProductionEmployeeRate.objects
        .filter(company_id=session.company_id, employee_id=session.employee_id)
        .first()
    )
    rate = _q2(rate_row.hourly_rate) if rate_row else Decimal("0.00")
    hours = Decimal(str(session.hours or 0))
    amount = _q2(hours * rate)

    if rate <= 0 or amount <= 0:
        # Ставки нет — начислять нечего; ранее созданное начисление снимаем.
        if existing:
            existing.status = ProductionSalaryAccrual.Status.CANCELED
            existing.save(update_fields=["status", "updated_at"])
        return None

    if existing:
        existing.hours = hours
        existing.rate = rate
        existing.amount = amount
        existing.save(update_fields=["hours", "rate", "amount", "updated_at"])
        return existing

    return ProductionSalaryAccrual.objects.create(
        company_id=session.company_id,
        employee_id=session.employee_id,
        kind=ProductionSalaryAccrual.Kind.HOURLY,
        work_session=session,
        hours=hours,
        rate=rate,
        amount=amount,
    )


@transaction.atomic
def cancel_session_accruals(session) -> int:
    """Удаление табеля → снять неоплаченное начисление (оплаченное → AccrualPaidError)."""
    paid = (
        ProductionSalaryAccrual.objects
        .filter(work_session=session, status=ProductionSalaryAccrual.Status.PAID)
        .exists()
    )
    if paid:
        raise AccrualPaidError()

    return (
        ProductionSalaryAccrual.objects
        .filter(work_session=session, status=ProductionSalaryAccrual.Status.ACCRUED)
        .update(status=ProductionSalaryAccrual.Status.CANCELED)
    )


# ─────────────────────────────────────────────────────────────
# Баланс и выплаты
# ─────────────────────────────────────────────────────────────
def employee_balance(company_id, employee_id) -> Decimal:
    """Долг перед сотрудником = Σ начислений в статусе accrued."""
    total = (
        ProductionSalaryAccrual.objects
        .filter(
            company_id=company_id,
            employee_id=employee_id,
            status=ProductionSalaryAccrual.Status.ACCRUED,
        )
        .aggregate(s=Sum("amount"))["s"]
    )
    return _q2(total or 0)


def _create_cash_expense(payout):
    """Выплата → один расход кассы «Зарплата: <сотрудник>» (прочий расход в аналитике)."""
    from apps.construction.models import CashFlow

    return CashFlow.objects.create(
        company=payout.company,
        branch=payout.cashbox.branch,
        cashbox=payout.cashbox,
        type=CashFlow.Type.EXPENSE,
        name=f"Зарплата: {_user_display_name(payout.employee)}".strip(),
        amount=payout.amount,
        status=CashFlow.Status.APPROVED,
        cashier=payout.created_by,
        source_business_operation_id=str(payout.id),
    )


@transaction.atomic
def create_payout(*, company, employee, amount, cashbox, comment: str = "", created_by=None):
    """
    Создаёт выплату, закрывает начисления (accrued) FIFO — от старых к новым —
    и проводит расход по кассе. Частично покрытое начисление разбивается:
    закрытая часть (paid) остаётся на записи, остаток выносится новым accrued.
    """
    amount = _q2(amount)
    if amount <= 0:
        raise ValueError("amount_not_positive")

    accruals = list(
        ProductionSalaryAccrual.objects
        .select_for_update()
        .filter(
            company=company,
            employee=employee,
            status=ProductionSalaryAccrual.Status.ACCRUED,
        )
        .order_by("created_at", "id")
    )
    balance = _q2(sum((Decimal(a.amount) for a in accruals), Decimal("0.00")))
    if amount > balance:
        raise SalaryBalanceError(balance)

    payout = ProductionSalaryPayout.objects.create(
        company=company,
        employee=employee,
        amount=amount,
        cashbox=cashbox,
        comment=(comment or "").strip(),
        created_by=created_by,
    )

    remaining = amount
    for acc in accruals:
        if remaining <= 0:
            break
        acc_amount = Decimal(acc.amount)
        if acc_amount <= 0:
            continue

        if acc_amount <= remaining:
            acc.status = ProductionSalaryAccrual.Status.PAID
            acc.payout = payout
            acc.save(update_fields=["status", "payout", "updated_at"])
            remaining -= acc_amount
        else:
            # Частичное покрытие: остаток выносим отдельным accrued-начислением.
            ProductionSalaryAccrual.objects.create(
                company_id=acc.company_id,
                employee_id=acc.employee_id,
                kind=acc.kind,
                work_session_id=acc.work_session_id,
                hours=acc.hours,
                rate=acc.rate,
                production_record_id=acc.production_record_id,
                product_id=acc.product_id,
                quantity=acc.quantity,
                amount_per_unit=acc.amount_per_unit,
                amount=acc_amount - remaining,
                status=ProductionSalaryAccrual.Status.ACCRUED,
            )
            acc.status = ProductionSalaryAccrual.Status.PAID
            acc.payout = payout
            acc.amount = remaining
            acc.save(update_fields=["status", "payout", "amount", "updated_at"])
            remaining = Decimal("0.00")

    _create_cash_expense(payout)
    return payout
