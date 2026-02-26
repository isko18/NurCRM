from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    BuildingProcurementCashDecision,
    BuildingProcurementRequest,
    BuildingTransferItem,
    BuildingTransferRequest,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingWorkflowEvent,
    ResidentialComplexWarehouse,
)


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
