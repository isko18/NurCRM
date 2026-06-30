import calendar
import os
from decimal import Decimal

import httpx

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from django.db.models import Sum
from django.db.models.functions import Coalesce

from .models import (
    BuildingBarterItem,
    BuildingCashbox,
    BuildingCashFlow,
    BuildingCashRegisterRequest,
    BuildingEmployeeCompensation,
    BuildingPayrollAdjustment,
    BuildingPayrollLine,
    BuildingPayrollPayment,
    BuildingPayrollPeriod,
    BuildingProcurementCashDecision,
    BuildingProcurementRequest,
    BuildingReconciliationAct,
    BuildingTransferItem,
    BuildingTransferRequest,
    BuildingTreatyFile,
    BuildingTreatyNumberSequence,
    BuildingWarehouseRequest,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingWorkEntry,
    BuildingWorkEntryAcceptance,
    BuildingWorkflowEvent,
    BuildingTreaty,
    BuildingDebtLedgerEntry,
    ResidentialComplexWarehouse,
)


def default_currency() -> str:
    return (os.getenv("BUILDING_DEFAULT_CURRENCY") or "KGS").strip() or "KGS"


def next_treaty_number(company_id) -> str:
    seq, _ = BuildingTreatyNumberSequence.objects.select_for_update().get_or_create(company_id=company_id)
    value = int(seq.next_value or 1)
    seq.next_value = value + 1
    seq.save(update_fields=["next_value", "updated_at"])
    return f"ДГ-{value:06d}"


def _treaty_payment_mode(payment_mode: str) -> str:
    valid = {c.value for c in BuildingTreaty.PaymentMode}
    return payment_mode if payment_mode in valid else BuildingTreaty.PaymentMode.CASH


def _barter_total(company_id, source_type: str, source_id) -> Decimal:
    total = (
        BuildingBarterItem.objects.filter(
            company_id=company_id,
            source_type=source_type,
            source_id=source_id,
        ).aggregate(s=Coalesce(Sum("total_price"), Decimal("0.00")))
    ).get("s") or Decimal("0.00")
    return Decimal(total).quantize(Decimal("0.01"))


def _debt_entry_exists(company_id, source_type: str, source_id, entry_type: str) -> bool:
    return BuildingDebtLedgerEntry.objects.filter(
        company_id=company_id,
        source_type=source_type,
        source_id=source_id,
        entry_type=entry_type,
        status=BuildingDebtLedgerEntry.Status.APPROVED,
    ).exists()


def _create_debt_entry(
    *,
    company_id,
    direction,
    counterparty_type,
    counterparty_id,
    entry_type,
    amount: Decimal,
    residential_complex,
    source_type: str,
    source_id,
    comment: str,
    actor,
):
    if amount <= 0:
        return None
    if _debt_entry_exists(company_id, source_type, source_id, entry_type):
        return BuildingDebtLedgerEntry.objects.filter(
            company_id=company_id,
            source_type=source_type,
            source_id=source_id,
            entry_type=entry_type,
            status=BuildingDebtLedgerEntry.Status.APPROVED,
        ).first()
    return BuildingDebtLedgerEntry.objects.create(
        company_id=company_id,
        direction=direction,
        counterparty_type=counterparty_type,
        counterparty_id=counterparty_id,
        entry_type=entry_type,
        amount=amount,
        currency=default_currency(),
        status=BuildingDebtLedgerEntry.Status.APPROVED,
        residential_complex=residential_complex,
        source_type=source_type,
        source_id=source_id,
        comment=comment,
        occurred_at=timezone.now(),
        created_by=actor,
    )


def create_procurement_debt_entries(procurement: BuildingProcurementRequest, actor):
    payment_mode = getattr(procurement, "payment_mode", None) or BuildingProcurementRequest.PaymentMode.CASH
    if payment_mode == BuildingProcurementRequest.PaymentMode.CASH or not procurement.supplier_id:
        return []
    total = Decimal(procurement.total_amount or 0).quantize(Decimal("0.01"))
    if total <= 0:
        return []
    company_id = procurement.residential_complex.company_id
    base_kwargs = {
        "company_id": company_id,
        "direction": BuildingDebtLedgerEntry.Direction.PAYABLE,
        "counterparty_type": BuildingDebtLedgerEntry.CounterpartyType.SUPPLIER,
        "counterparty_id": procurement.supplier_id,
        "residential_complex": procurement.residential_complex,
        "source_type": "procurement",
        "source_id": procurement.id,
        "actor": actor,
    }
    created = []
    if payment_mode == BuildingProcurementRequest.PaymentMode.DEBT:
        entry = _create_debt_entry(
            **base_kwargs,
            entry_type=BuildingDebtLedgerEntry.EntryType.CHARGE,
            amount=total,
            comment=f"Закупка в долг: {procurement.title or procurement.id}",
        )
        if entry:
            created.append(entry)
    elif payment_mode == BuildingProcurementRequest.PaymentMode.BARTER:
        barter_amount = _barter_total(company_id, "procurement", procurement.id) or total
        entry = _create_debt_entry(
            **base_kwargs,
            entry_type=BuildingDebtLedgerEntry.EntryType.BARTER,
            amount=barter_amount,
            comment=f"Закупка (бартер): {procurement.title or procurement.id}",
        )
        if entry:
            created.append(entry)
    elif payment_mode == BuildingProcurementRequest.PaymentMode.MIXED:
        barter_amount = _barter_total(company_id, "procurement", procurement.id)
        if barter_amount > 0:
            entry = _create_debt_entry(
                **base_kwargs,
                entry_type=BuildingDebtLedgerEntry.EntryType.BARTER,
                amount=barter_amount,
                comment=f"Закупка (бартерная часть): {procurement.title or procurement.id}",
            )
            if entry:
                created.append(entry)
        charge_amount = (total - barter_amount).quantize(Decimal("0.01"))
        if charge_amount > 0:
            entry = _create_debt_entry(
                **base_kwargs,
                entry_type=BuildingDebtLedgerEntry.EntryType.CHARGE,
                amount=charge_amount,
                comment=f"Закупка (долговая часть): {procurement.title or procurement.id}",
            )
            if entry:
                created.append(entry)
    if created:
        log_event(
            action="debt_entries_created",
            actor=actor,
            procurement=procurement,
            payload={"entry_ids": [str(e.id) for e in created], "payment_mode": payment_mode},
        )
    return created


def create_work_entry_debt_entries(work_entry: BuildingWorkEntry, actor):
    payment_mode = getattr(work_entry, "payment_mode", None) or BuildingWorkEntry.PaymentMode.CASH
    if payment_mode == BuildingWorkEntry.PaymentMode.CASH or not work_entry.contractor_id:
        return []
    total = Decimal(work_entry.contract_amount or 0).quantize(Decimal("0.01"))
    if total <= 0:
        return []
    rc = work_entry.residential_complex
    company_id = rc.company_id
    base_kwargs = {
        "company_id": company_id,
        "direction": BuildingDebtLedgerEntry.Direction.PAYABLE,
        "counterparty_type": BuildingDebtLedgerEntry.CounterpartyType.CONTRACTOR,
        "counterparty_id": work_entry.contractor_id,
        "residential_complex": rc,
        "source_type": "work_entry",
        "source_id": work_entry.id,
        "actor": actor,
    }
    created = []
    if payment_mode == BuildingWorkEntry.PaymentMode.DEBT:
        entry = _create_debt_entry(
            **base_kwargs,
            entry_type=BuildingDebtLedgerEntry.EntryType.CHARGE,
            amount=total,
            comment=f"Работы в долг: {work_entry.title or work_entry.id}",
        )
        if entry:
            created.append(entry)
    elif payment_mode == BuildingWorkEntry.PaymentMode.BARTER:
        barter_amount = _barter_total(company_id, "work_entry", work_entry.id) or total
        entry = _create_debt_entry(
            **base_kwargs,
            entry_type=BuildingDebtLedgerEntry.EntryType.BARTER,
            amount=barter_amount,
            comment=f"Работы (бартер): {work_entry.title or work_entry.id}",
        )
        if entry:
            created.append(entry)
    elif payment_mode == BuildingWorkEntry.PaymentMode.MIXED:
        barter_amount = _barter_total(company_id, "work_entry", work_entry.id)
        if barter_amount > 0:
            entry = _create_debt_entry(
                **base_kwargs,
                entry_type=BuildingDebtLedgerEntry.EntryType.BARTER,
                amount=barter_amount,
                comment=f"Работы (бартерная часть): {work_entry.title or work_entry.id}",
            )
            if entry:
                created.append(entry)
        charge_amount = (total - barter_amount).quantize(Decimal("0.01"))
        if charge_amount > 0:
            entry = _create_debt_entry(
                **base_kwargs,
                entry_type=BuildingDebtLedgerEntry.EntryType.CHARGE,
                amount=charge_amount,
                comment=f"Работы (долговая часть): {work_entry.title or work_entry.id}",
            )
            if entry:
                created.append(entry)
    if created:
        log_event(
            action="debt_entries_created",
            actor=actor,
            payload={
                "entry_ids": [str(e.id) for e in created],
                "work_entry_id": str(work_entry.id),
                "payment_mode": payment_mode,
            },
        )
    return created


def cash_amount_for_source(company_id, payment_mode: str, total: Decimal, source_type: str, source_id) -> Decimal:
    total = Decimal(total or 0).quantize(Decimal("0.01"))
    if payment_mode in (BuildingWorkEntry.PaymentMode.CASH, BuildingProcurementRequest.PaymentMode.CASH):
        return total
    if payment_mode in (BuildingWorkEntry.PaymentMode.DEBT, BuildingProcurementRequest.PaymentMode.BARTER):
        return Decimal("0.00")
    if payment_mode in (BuildingWorkEntry.PaymentMode.MIXED, BuildingProcurementRequest.PaymentMode.MIXED):
        barter_amount = _barter_total(company_id, source_type, source_id)
        return max(total - barter_amount, Decimal("0.00")).quantize(Decimal("0.01"))
    return total


@transaction.atomic
def ensure_treaty_from_procurement_file(procurement: BuildingProcurementRequest, pf, actor) -> dict:
    result = {"treaty_id": None, "treaty_created": False, "treaty_error": None}
    try:
        if getattr(procurement, "treaty_auto_create", False) and not procurement.treaty_id:
            rc = procurement.residential_complex
            t_type = (getattr(procurement, "treaty_type", "") or "").strip() or BuildingTreaty.TreatyType.PROCUREMENT
            t_title = (getattr(procurement, "treaty_title", "") or "").strip() or (procurement.title or "Договор закупки")
            treaty = BuildingTreaty.objects.create(
                company_id=rc.company_id,
                residential_complex=rc,
                client=None,
                title=t_title,
                description="",
                number=next_treaty_number(rc.company_id),
                amount=Decimal(procurement.total_amount or 0).quantize(Decimal("0.01")),
                treaty_type=t_type,
                operation_type=BuildingTreaty.OperationType.OTHER,
                payment_type=BuildingTreaty.PaymentType.FULL,
                payment_mode=_treaty_payment_mode(procurement.payment_mode),
                created_by=actor,
            )
            BuildingTreatyFile.objects.create(
                treaty=treaty,
                file=pf.file,
                title=pf.title or "",
                created_by=actor,
            )
            procurement.treaty = treaty
            procurement.save(update_fields=["treaty", "updated_at"])
            result.update({"treaty_id": str(treaty.id), "treaty_created": True})
            log_event(
                action="treaty_auto_created",
                actor=actor,
                procurement=procurement,
                payload={"treaty_id": str(treaty.id), "source": "procurement_file"},
            )
        elif procurement.treaty_id:
            BuildingTreatyFile.objects.create(
                treaty=procurement.treaty,
                file=pf.file,
                title=pf.title or "",
                created_by=actor,
            )
            result["treaty_id"] = str(procurement.treaty_id)
    except Exception as exc:
        result["treaty_error"] = str(exc)
        log_event(
            action="treaty_auto_create_failed",
            actor=actor,
            procurement=procurement,
            message=str(exc),
            payload={"source": "procurement_file"},
        )
    return result


@transaction.atomic
def ensure_treaty_from_work_entry_files(work_entry: BuildingWorkEntry, created_files, actor) -> dict:
    result = {"treaty_id": None, "treaty_created": False, "treaty_error": None}
    try:
        if getattr(work_entry, "treaty_auto_create", False) and not work_entry.treaty_id:
            rc = work_entry.residential_complex
            t_type = (getattr(work_entry, "treaty_type", "") or "").strip() or BuildingTreaty.TreatyType.CONSTRUCTION_DEPARTMENT
            t_title = (getattr(work_entry, "treaty_title", "") or "").strip() or (work_entry.title or "Договор по работам")
            treaty = BuildingTreaty.objects.create(
                company_id=rc.company_id,
                residential_complex=rc,
                client=work_entry.client,
                title=t_title,
                description="",
                number=next_treaty_number(rc.company_id),
                amount=Decimal(work_entry.contract_amount or 0).quantize(Decimal("0.01")) if work_entry.contract_amount else Decimal("0.00"),
                treaty_type=t_type,
                operation_type=BuildingTreaty.OperationType.OTHER,
                payment_type=BuildingTreaty.PaymentType.FULL,
                payment_mode=_treaty_payment_mode(work_entry.payment_mode),
                created_by=actor,
            )
            for wf in created_files:
                BuildingTreatyFile.objects.create(treaty=treaty, file=wf.file, title=wf.title or "", created_by=actor)
            work_entry.treaty = treaty
            work_entry.save(update_fields=["treaty", "updated_at"])
            result.update({"treaty_id": str(treaty.id), "treaty_created": True})
            log_event(
                action="treaty_auto_created",
                actor=actor,
                payload={"treaty_id": str(treaty.id), "work_entry_id": str(work_entry.id)},
            )
        elif work_entry.treaty_id:
            for wf in created_files:
                BuildingTreatyFile.objects.create(treaty=work_entry.treaty, file=wf.file, title=wf.title or "", created_by=actor)
            result["treaty_id"] = str(work_entry.treaty_id)
    except Exception as exc:
        result["treaty_error"] = str(exc)
        log_event(
            action="treaty_auto_create_failed",
            actor=actor,
            message=str(exc),
            payload={"work_entry_id": str(work_entry.id)},
        )
    return result


@transaction.atomic
def on_work_entry_completed(work_entry: BuildingWorkEntry, actor, old_status: str):
    if old_status == BuildingWorkEntry.WorkStatus.COMPLETED:
        return
    if work_entry.work_status != BuildingWorkEntry.WorkStatus.COMPLETED:
        return
    if not work_entry.contractor_id:
        return

    BuildingWorkEntryAcceptance.objects.get_or_create(work_entry=work_entry)
    create_work_entry_debt_entries(work_entry, actor)

    payment_mode = getattr(work_entry, "payment_mode", None) or BuildingWorkEntry.PaymentMode.CASH
    cash_amount = cash_amount_for_source(
        work_entry.residential_complex.company_id,
        payment_mode,
        work_entry.contract_amount,
        "work_entry",
        work_entry.id,
    )
    if cash_amount <= 0:
        return
    if BuildingCashRegisterRequest.objects.filter(
        work_entry=work_entry,
        request_type=BuildingCashRegisterRequest.RequestType.CONTRACTOR_PAYMENT,
    ).exists():
        return
    rc = work_entry.residential_complex
    cashbox = rc.salary_cashbox or BuildingCashbox.objects.filter(company_id=rc.company_id).first()
    if not cashbox:
        return
    BuildingCashRegisterRequest.objects.create(
        company_id=rc.company_id,
        work_entry=work_entry,
        request_type=BuildingCashRegisterRequest.RequestType.CONTRACTOR_PAYMENT,
        status=BuildingCashRegisterRequest.Status.PENDING,
        amount=cash_amount,
        comment=f"Оплата подрядчику по процессу работ: {work_entry.title or work_entry.id}",
        cashbox=cashbox,
        contractor=work_entry.contractor,
        residential_complex=rc,
        created_by=actor,
    )
    log_event(
        action="work_entry_completed",
        actor=actor,
        payload={"work_entry_id": str(work_entry.id), "cash_request_amount": str(cash_amount)},
    )


def decrement_stock_item(*, stock_item_id, warehouse_id, qty: Decimal) -> BuildingWarehouseStockItem:
    stock_item = BuildingWarehouseStockItem.objects.select_for_update().get(id=stock_item_id, warehouse_id=warehouse_id)
    current = Decimal(stock_item.quantity or 0)
    qty = Decimal(qty)
    if qty <= 0:
        raise ValidationError({"items": "Количество должно быть положительным."})
    if current < qty:
        raise ValidationError({"items": f"Недостаточно остатка для {stock_item.name} (доступно {current})."})
    stock_item.quantity = (current - qty).quantize(Decimal("0.001"))
    stock_item.save(update_fields=["quantity", "updated_at"])
    return stock_item


@transaction.atomic
def cancel_cash_register_request(req: BuildingCashRegisterRequest, actor, reason: str = ""):
    _require_cash_perm(actor)
    _same_company_or_raise(actor, req.company_id)
    if req.status != BuildingCashRegisterRequest.Status.PENDING:
        raise ValidationError({"status": "Отменить можно только заявку в статусе pending."})
    old_status = req.status
    req.status = BuildingCashRegisterRequest.Status.CANCELLED
    if reason:
        req.reject_reason = reason
    req.save(update_fields=["status", "reject_reason", "updated_at"])
    log_event(action="cash_register_cancelled", actor=actor, message=reason or "", from_status=old_status, to_status=req.status, payload={"request_id": str(req.id)})
    return req


@transaction.atomic
def approve_warehouse_request(req: BuildingWarehouseRequest, actor, approved_items: list | None = None):
    _require_warehouse_perm(actor)
    rc_company_id = req.work_entry.residential_complex.company_id
    _same_company_or_raise(actor, rc_company_id)
    if req.status not in (BuildingWarehouseRequest.Status.PENDING, BuildingWarehouseRequest.Status.PARTIALLY_APPROVED):
        raise ValidationError({"status": "Заявка уже обработана."})
    if approved_items:
        for row in approved_items:
            item = req.items.select_for_update().filter(id=row["id"]).first()
            if not item:
                raise ValidationError({"items": f"Позиция {row.get('id')} не найдена в заявке."})
            approved_qty = Decimal(row["approved_quantity"])
            if approved_qty < 0 or approved_qty > item.quantity:
                raise ValidationError({"items": f"Некорректное одобренное количество для {item.stock_item.name}."})
            item.approved_quantity = approved_qty
            item.save(update_fields=["approved_quantity"])
    else:
        for item in req.items.select_for_update().all():
            if item.approved_quantity is None:
                item.approved_quantity = item.quantity
                item.save(update_fields=["approved_quantity"])
    req.status = BuildingWarehouseRequest.Status.APPROVED
    req.decided_by = actor
    req.save(update_fields=["status", "decided_by", "updated_at"])
    log_event(action="warehouse_request_approved", actor=actor, payload={"request_id": str(req.id)})
    return req


@transaction.atomic
def reject_warehouse_request(req: BuildingWarehouseRequest, actor, reason: str):
    _require_warehouse_perm(actor)
    rc_company_id = req.work_entry.residential_complex.company_id
    _same_company_or_raise(actor, rc_company_id)
    if req.status not in (BuildingWarehouseRequest.Status.PENDING, BuildingWarehouseRequest.Status.PARTIALLY_APPROVED):
        raise ValidationError({"status": "Заявка уже обработана."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Укажите причину отказа."})
    old_status = req.status
    req.status = BuildingWarehouseRequest.Status.REJECTED
    req.decided_by = actor
    req.comment = (req.comment or "") + (f"\nОтказ: {reason}" if reason else "")
    req.save(update_fields=["status", "decided_by", "comment", "updated_at"])
    log_event(action="warehouse_request_rejected", actor=actor, from_status=old_status, to_status=req.status, message=reason, payload={"request_id": str(req.id)})
    return req


@transaction.atomic
def set_reconciliation_act_status(act: BuildingReconciliationAct, actor, new_status: str, reason: str = ""):
    _require_warehouse_perm(actor)
    rc_company_id = act.work_entry.residential_complex.company_id
    _same_company_or_raise(actor, rc_company_id)
    if act.status != BuildingReconciliationAct.Status.DRAFT:
        raise ValidationError({"status": "Менять статус можно только у черновика акта сверки."})
    if new_status not in (BuildingReconciliationAct.Status.APPROVED, BuildingReconciliationAct.Status.REJECTED):
        raise ValidationError({"status": "Допустимы статусы approved/rejected."})
    if new_status == BuildingReconciliationAct.Status.REJECTED and not (reason or "").strip():
        raise ValidationError({"reason": "Укажите причину отклонения акта сверки."})
    old_status = act.status
    act.status = new_status
    if reason:
        act.comment = (act.comment or "") + (f"\n{reason}" if act.comment else reason)
    act.save(update_fields=["status", "comment", "updated_at"])
    log_event(
        action="reconciliation_act_status_changed",
        actor=actor,
        from_status=old_status,
        to_status=new_status,
        message=reason or "",
        payload={"act_id": str(act.id), "work_entry_id": str(act.work_entry_id)},
    )
    return act


@transaction.atomic
def void_payroll_payment(payment: BuildingPayrollPayment, actor, reason: str):
    _require_cash_perm(actor)
    line = payment.line
    payroll = line.payroll
    _same_company_or_raise(actor, payroll.company_id)
    if payment.status != BuildingPayrollPayment.Status.POSTED:
        raise ValidationError({"status": "Отменить можно только проведённую выплату."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Укажите причину отмены выплаты."})
    payment.status = BuildingPayrollPayment.Status.VOID
    payment.void_reason = reason
    payment.voided_by = actor
    payment.voided_at = timezone.now()
    payment.save(update_fields=["status", "void_reason", "voided_by", "voided_at"])
    if payment.cashflow_id:
        cf = payment.cashflow
        cf.status = BuildingCashFlow.Status.REJECTED
        cf.save(update_fields=["status"])
    line.recalculate_paid_total()
    log_event(action="payroll_payment_voided", actor=actor, message=reason, payload={"payment_id": str(payment.id)})
    return payment


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) in ("owner", "admin"):
        return True
    if getattr(user, "owned_company_id", None):
        return True
    return False


def _require_procurement_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_procurement", False):
        raise ValidationError({"detail": "Нет прав отдела закупок."})


def _require_cash_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_cash_register", False):
        raise ValidationError({"detail": "Нет прав кассы."})


def _require_warehouse_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_stock", False):
        raise ValidationError({"detail": "Нет прав складского ответственного."})


def _require_clients_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_clients", False):
        raise ValidationError({"detail": "Нет прав на клиентов (Building)."})


def _require_treaty_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_treaty", False):
        raise ValidationError({"detail": "Нет прав на договора (Building)."})


def _require_work_process_perm(user):
    if _is_owner_like(user):
        return
    if not getattr(user, "can_view_building_work_process", False):
        raise ValidationError({"detail": "Нет прав на процесс работы (Building)."})


def _same_company_or_raise(user, company_id):
    user_company_id = getattr(user, "company_id", None) or getattr(getattr(user, "owned_company", None), "id", None)
    if not _is_owner_like(user) and (not user_company_id or user_company_id != company_id):
        raise ValidationError({"detail": "Объект другой компании."})


def log_event(
    *,
    action: str,
    actor=None,
    procurement=None,
    procurement_item=None,
    transfer=None,
    transfer_item=None,
    warehouse=None,
    stock_item=None,
    from_status: str | None = None,
    to_status: str | None = None,
    message: str = "",
    payload: dict | None = None,
):
    BuildingWorkflowEvent.objects.create(
        action=action,
        actor=actor,
        procurement=procurement,
        procurement_item=procurement_item,
        transfer=transfer,
        transfer_item=transfer_item,
        warehouse=warehouse,
        stock_item=stock_item,
        from_status=from_status,
        to_status=to_status,
        message=message or "",
        payload=payload or {},
    )


@transaction.atomic
def submit_procurement_to_cash(procurement: BuildingProcurementRequest, actor):
    _require_procurement_perm(actor)
    _same_company_or_raise(actor, procurement.residential_complex.company_id)
    if procurement.status != BuildingProcurementRequest.Status.DRAFT:
        raise ValidationError({"status": "В кассу можно отправить только черновик."})
    if not procurement.items.exists():
        raise ValidationError({"items": "Нельзя отправить пустую закупку."})

    old_status = procurement.status
    procurement.status = BuildingProcurementRequest.Status.SUBMITTED_TO_CASH
    procurement.submitted_to_cash_at = timezone.now()
    procurement.recalculate_totals()
    procurement.save(update_fields=["status", "submitted_to_cash_at", "total_amount", "updated_at"])

    log_event(
        action="procurement_submitted_to_cash",
        actor=actor,
        procurement=procurement,
        from_status=old_status,
        to_status=procurement.status,
        payload={"total_amount": str(procurement.total_amount)},
    )
    return procurement


@transaction.atomic
def approve_procurement_cash(procurement: BuildingProcurementRequest, actor, reason: str = ""):
    _require_cash_perm(actor)
    _same_company_or_raise(actor, procurement.residential_complex.company_id)
    if procurement.status != BuildingProcurementRequest.Status.SUBMITTED_TO_CASH:
        raise ValidationError({"status": "Одобрить можно только закупку в ожидании кассы."})

    old_status = procurement.status
    now = timezone.now()
    procurement.status = BuildingProcurementRequest.Status.CASH_APPROVED
    procurement.cash_decided_at = now
    procurement.cash_decided_by = actor
    procurement.save(update_fields=["status", "cash_decided_at", "cash_decided_by", "updated_at"])

    BuildingProcurementCashDecision.objects.update_or_create(
        procurement=procurement,
        defaults={
            "decision": BuildingProcurementCashDecision.Decision.APPROVED,
            "reason": reason or "",
            "decided_by": actor,
        },
    )

    log_event(
        action="cash_approved",
        actor=actor,
        procurement=procurement,
        from_status=old_status,
        to_status=procurement.status,
        message=reason or "",
    )
    return procurement


@transaction.atomic
def reject_procurement_cash(procurement: BuildingProcurementRequest, actor, reason: str):
    _require_cash_perm(actor)
    _same_company_or_raise(actor, procurement.residential_complex.company_id)
    if procurement.status != BuildingProcurementRequest.Status.SUBMITTED_TO_CASH:
        raise ValidationError({"status": "Отклонить можно только закупку в ожидании кассы."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Укажите причину отказа кассы."})

    old_status = procurement.status
    now = timezone.now()
    procurement.status = BuildingProcurementRequest.Status.CASH_REJECTED
    procurement.cash_decided_at = now
    procurement.cash_decided_by = actor
    procurement.save(update_fields=["status", "cash_decided_at", "cash_decided_by", "updated_at"])

    BuildingProcurementCashDecision.objects.update_or_create(
        procurement=procurement,
        defaults={
            "decision": BuildingProcurementCashDecision.Decision.REJECTED,
            "reason": reason,
            "decided_by": actor,
        },
    )

    log_event(
        action="cash_rejected",
        actor=actor,
        procurement=procurement,
        from_status=old_status,
        to_status=procurement.status,
        message=reason,
    )
    return procurement


@transaction.atomic
def create_transfer_from_procurement(procurement: BuildingProcurementRequest, actor, note: str = ""):
    _require_procurement_perm(actor)
    _same_company_or_raise(actor, procurement.residential_complex.company_id)
    if procurement.status != BuildingProcurementRequest.Status.CASH_APPROVED:
        raise ValidationError({"status": "Передачу можно создать только после одобрения кассой."})
    if not procurement.items.exists():
        raise ValidationError({"items": "В закупке нет позиций для передачи."})

    warehouse, _ = ResidentialComplexWarehouse.objects.get_or_create(
        residential_complex=procurement.residential_complex,
        defaults={"name": f"Склад {procurement.residential_complex.name}", "is_active": True},
    )

    transfer = BuildingTransferRequest.objects.create(
        procurement=procurement,
        warehouse=warehouse,
        created_by=actor,
        status=BuildingTransferRequest.Status.PENDING_RECEIPT,
        note=note or "",
        total_amount=Decimal("0.00"),
    )

    for idx, item in enumerate(procurement.items.all(), start=1):
        t_item = BuildingTransferItem.objects.create(
            transfer=transfer,
            procurement_item=item,
            name=item.name,
            unit=item.unit,
            quantity=item.quantity,
            price=item.price,
            order=idx,
        )
        log_event(
            action="transfer_item_created",
            actor=actor,
            procurement=procurement,
            procurement_item=item,
            transfer=transfer,
            transfer_item=t_item,
            warehouse=warehouse,
            payload={
                "name": t_item.name,
                "unit": t_item.unit,
                "quantity": str(t_item.quantity),
                "price": str(t_item.price),
                "line_total": str(t_item.line_total),
            },
        )

    transfer.recalculate_totals()

    old_status = procurement.status
    procurement.status = BuildingProcurementRequest.Status.TRANSFER_CREATED
    procurement.save(update_fields=["status", "updated_at"])

    log_event(
        action="transfer_created",
        actor=actor,
        procurement=procurement,
        transfer=transfer,
        warehouse=warehouse,
        from_status=old_status,
        to_status=procurement.status,
        payload={"transfer_id": str(transfer.id), "total_amount": str(transfer.total_amount)},
    )
    return transfer


@transaction.atomic
def accept_transfer(transfer: BuildingTransferRequest, actor, note: str = ""):
    _require_warehouse_perm(actor)
    _same_company_or_raise(actor, transfer.warehouse.residential_complex.company_id)
    if transfer.status != BuildingTransferRequest.Status.PENDING_RECEIPT:
        raise ValidationError({"status": "Передача уже обработана."})
    if not transfer.items.exists():
        raise ValidationError({"items": "В передаче нет позиций."})

    for item in transfer.items.select_related("procurement_item").all():
        stock_item, _ = BuildingWarehouseStockItem.objects.select_for_update().get_or_create(
            warehouse=transfer.warehouse,
            name=item.name,
            unit=item.unit,
            defaults={
                "quantity": Decimal("0.000"),
                "last_price": item.price,
            },
        )
        old_qty = Decimal(stock_item.quantity or 0)
        new_qty = old_qty + Decimal(item.quantity or 0)
        stock_item.quantity = new_qty
        stock_item.last_price = item.price
        stock_item.save(update_fields=["quantity", "last_price", "updated_at"])

        move = BuildingWarehouseStockMove.objects.create(
            warehouse=transfer.warehouse,
            stock_item=stock_item,
            transfer=transfer,
            move_type=BuildingWarehouseStockMove.MoveType.INCOMING,
            quantity_delta=item.quantity,
            price=item.price,
            created_by=actor,
        )

        log_event(
            action="stock_incoming",
            actor=actor,
            procurement=transfer.procurement,
            procurement_item=item.procurement_item,
            transfer=transfer,
            transfer_item=item,
            warehouse=transfer.warehouse,
            stock_item=stock_item,
            payload={
                "move_id": str(move.id),
                "old_quantity": str(old_qty),
                "new_quantity": str(new_qty),
                "delta": str(item.quantity),
                "price": str(item.price),
            },
        )

    old_transfer_status = transfer.status
    transfer.status = BuildingTransferRequest.Status.ACCEPTED
    transfer.decided_by = actor
    transfer.accepted_at = timezone.now()
    if note:
        transfer.note = note
    transfer.save(update_fields=["status", "decided_by", "accepted_at", "note", "updated_at"])

    procurement = transfer.procurement
    old_proc_status = procurement.status
    procurement.status = BuildingProcurementRequest.Status.TRANSFERRED
    procurement.save(update_fields=["status", "updated_at"])

    # Закупка "в долг": создаём запись долга (мы должны поставщику)
    create_procurement_debt_entries(procurement, actor)

    log_event(
        action="transfer_accepted",
        actor=actor,
        procurement=procurement,
        transfer=transfer,
        warehouse=transfer.warehouse,
        from_status=old_transfer_status,
        to_status=transfer.status,
        message=note or "",
    )
    log_event(
        action="procurement_transferred",
        actor=actor,
        procurement=procurement,
        transfer=transfer,
        warehouse=transfer.warehouse,
        from_status=old_proc_status,
        to_status=procurement.status,
    )
    return transfer


@transaction.atomic
def reject_transfer(transfer: BuildingTransferRequest, actor, reason: str):
    _require_warehouse_perm(actor)
    _same_company_or_raise(actor, transfer.warehouse.residential_complex.company_id)
    if transfer.status != BuildingTransferRequest.Status.PENDING_RECEIPT:
        raise ValidationError({"status": "Передача уже обработана."})
    if not (reason or "").strip():
        raise ValidationError({"reason": "Укажите причину отказа склада."})

    old_transfer_status = transfer.status
    transfer.status = BuildingTransferRequest.Status.REJECTED
    transfer.decided_by = actor
    transfer.rejected_at = timezone.now()
    transfer.rejection_reason = reason
    transfer.save(update_fields=["status", "decided_by", "rejected_at", "rejection_reason", "updated_at"])

    procurement = transfer.procurement
    old_proc_status = procurement.status
    procurement.status = BuildingProcurementRequest.Status.PARTIALLY_TRANSFERRED
    procurement.save(update_fields=["status", "updated_at"])

    log_event(
        action="transfer_rejected",
        actor=actor,
        procurement=procurement,
        transfer=transfer,
        warehouse=transfer.warehouse,
        from_status=old_transfer_status,
        to_status=transfer.status,
        message=reason,
    )
    log_event(
        action="procurement_partially_transferred",
        actor=actor,
        procurement=procurement,
        transfer=transfer,
        warehouse=transfer.warehouse,
        from_status=old_proc_status,
        to_status=procurement.status,
        message=reason,
    )
    return transfer


@transaction.atomic
def request_treaty_create_in_erp(treaty: BuildingTreaty, actor):
    """
    Запросить создание договора в ERP.

    Интеграция сделана безопасно: если ERP не настроена, просто фиксируем статус и ошибку.
    """
    _require_treaty_perm(actor)
    _same_company_or_raise(actor, treaty.residential_complex.company_id)

    endpoint = (os.getenv("BUILDING_ERP_TREATY_ENDPOINT") or "").strip()
    token = (os.getenv("BUILDING_ERP_TOKEN") or "").strip()
    now = timezone.now()

    treaty.erp_requested_at = now
    if not endpoint:
        treaty.erp_sync_status = BuildingTreaty.ErpSyncStatus.NOT_CONFIGURED
        treaty.erp_last_error = "ERP endpoint не настроен (env BUILDING_ERP_TREATY_ENDPOINT)."
        treaty.save(update_fields=["erp_requested_at", "erp_sync_status", "erp_last_error", "updated_at"])
        return treaty

    # Помечаем как "requested" до попытки вызова
    treaty.erp_sync_status = BuildingTreaty.ErpSyncStatus.REQUESTED
    treaty.erp_last_error = ""
    treaty.save(update_fields=["erp_requested_at", "erp_sync_status", "erp_last_error", "updated_at"])

    payload = {
        "id": str(treaty.id),
        "number": treaty.number,
        "title": treaty.title,
        "description": treaty.description,
        "amount": str(treaty.amount),
        "status": treaty.status,
        "residential_complex_id": str(treaty.residential_complex_id),
        "residential_complex_name": getattr(treaty.residential_complex, "name", ""),
        "client_id": str(treaty.client_id) if treaty.client_id else None,
        "client_name": getattr(treaty.client, "name", None) if treaty.client_id else None,
    }

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = httpx.post(endpoint, json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data = {}
        try:
            data = resp.json() or {}
        except Exception:
            data = {}

        treaty.erp_sync_status = BuildingTreaty.ErpSyncStatus.SYNCED
        treaty.erp_external_id = (data.get("external_id") or data.get("id") or treaty.erp_external_id or "").strip()
        treaty.erp_last_error = ""
        treaty.erp_synced_at = now
        treaty.save(update_fields=["erp_sync_status", "erp_external_id", "erp_last_error", "erp_synced_at", "updated_at"])
        return treaty
    except Exception as e:
        treaty.erp_sync_status = BuildingTreaty.ErpSyncStatus.FAILED
        treaty.erp_last_error = str(e)
        treaty.save(update_fields=["erp_sync_status", "erp_last_error", "updated_at"])
        return treaty


@transaction.atomic
def create_sale_commission_adjustment(treaty: BuildingTreaty):
    """
    При продаже (договор подписан/активен, тип SALE): создать премию в строке начисления
    ответственного (created_by), если у него настроено начисление от продаж.
    Идемпотентно: не создаёт повторно по одному и тому же договору.
    Если период или строка начисления не существуют — создаёт их автоматически.
    """
    if treaty.operation_type != BuildingTreaty.OperationType.SALE:
        return
    if treaty.status not in (BuildingTreaty.Status.ACTIVE, BuildingTreaty.Status.SIGNED):
        return
    employee_id = treaty.created_by_id
    if not employee_id:
        return
    rc = treaty.residential_complex
    comp = (
        BuildingEmployeeCompensation.objects.filter(
            company_id=rc.company_id,
            user_id=employee_id,
            is_active=True,
        )
        .exclude(sale_commission_type=BuildingEmployeeCompensation.SaleCommissionType.NONE)
        .exclude(sale_commission_type="")
        .first()
    )
    if not comp or not comp.sale_commission_value:
        return
    sale_date = (treaty.signed_at or treaty.created_at) or timezone.now()
    sale_date = sale_date.date() if hasattr(sale_date, "date") else sale_date

    period = (
        BuildingPayrollPeriod.objects.filter(
            residential_complex=rc,
            status__in=(BuildingPayrollPeriod.Status.DRAFT, BuildingPayrollPeriod.Status.APPROVED),
            period_start__lte=sale_date,
            period_end__gte=sale_date,
        )
        .order_by("-period_end")
        .first()
    )
    if not period:
        _, last_day = calendar.monthrange(sale_date.year, sale_date.month)
        period_start = sale_date.replace(day=1)
        period_end = sale_date.replace(day=last_day)
        period = BuildingPayrollPeriod.objects.create(
            company_id=rc.company_id,
            residential_complex=rc,
            title=f"ЗП {period_start.strftime('%Y-%m')}",
            period_start=period_start,
            period_end=period_end,
            status=BuildingPayrollPeriod.Status.DRAFT,
        )

    line = BuildingPayrollLine.objects.filter(payroll=period, employee_id=employee_id).first()
    if not line:
        base_amount = getattr(comp, "base_salary", None) or Decimal("0.00")
        line = BuildingPayrollLine.objects.create(
            payroll=period,
            employee_id=employee_id,
            base_amount=base_amount,
        )
        line.recalculate_totals()
        line.recalculate_paid_total()

    if BuildingPayrollAdjustment.objects.filter(line=line, source_treaty=treaty).exists():
        return
    if comp.sale_commission_type == BuildingEmployeeCompensation.SaleCommissionType.FIXED:
        amount = comp.sale_commission_value
    else:
        amount = (Decimal(treaty.amount or 0) * (comp.sale_commission_value / Decimal("100"))).quantize(Decimal("0.01"))
    if amount <= 0:
        return
    treaty_number = treaty.number or str(treaty.id)
    title = f"От продажи № {treaty_number}"
    BuildingPayrollAdjustment.objects.create(
        line=line,
        type=BuildingPayrollAdjustment.Type.BONUS,
        status=BuildingPayrollAdjustment.Status.COMPLETED,
        title=title,
        amount=amount,
        source_treaty=treaty,
    )
    line.recalculate_totals()
    line.recalculate_paid_total()
