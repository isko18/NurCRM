from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Dict, List, Optional, Union

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.construction.models import Cashbox, CashFlow, CashShift

logger = logging.getLogger(__name__)

_Q2 = Decimal("0.01")


def resolve_auto_cashflow_status(company) -> str:
    """
    Status policy:
    - If company.subscription_plan.name == "Старт" (case-insensitive) -> approved
    - Else (auto operations) -> pending
    """
    if company:
        sub_plan = getattr(company, "subscription_plan", None)
        if sub_plan and getattr(sub_plan, "name", None):
            plan_name = str(sub_plan.name).strip().lower()
            if plan_name in ("старт", "start"):
                return CashFlow.Status.APPROVED
    return CashFlow.Status.PENDING


def resolve_auto_cashbox(
    company,
    user=None,
    branch=None,
    cashbox_id=None,
    require_cashbox: bool = True,
) -> Optional[Cashbox]:
    """
    Order of resolution:
    1. Explicit cashbox_id in body/request.
    2. Cashbox of open shift of current cashier/user.
    3. If require_cashbox is True (operation produces amount > 0) -> 400 {"cashbox_id": ["cashbox_required"]}.
    4. Otherwise None.
    """
    if cashbox_id:
        qs = Cashbox.objects.filter(id=cashbox_id, company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)
        cashbox = qs.first()
        if cashbox:
            return cashbox
        if require_cashbox:
            raise DRFValidationError({"cashbox_id": ["cashbox_required"]})

    if user and getattr(user, "is_authenticated", False):
        shift_qs = CashShift.objects.filter(
            company=company,
            cashier=user,
            status=CashShift.Status.OPEN,
        ).select_related("cashbox")
        if branch is not None:
            shift_qs = shift_qs.filter(branch=branch)
        shift = shift_qs.first()
        if shift and shift.cashbox:
            return shift.cashbox

    if require_cashbox:
        raise DRFValidationError({"cashbox_id": ["cashbox_required"]})

    return None


def create_auto_cashflow(
    *,
    company,
    branch=None,
    cashbox=None,
    cashbox_id=None,
    user=None,
    shift=None,
    type: str,
    amount: Union[Decimal, str, int, float],
    source_kind: str,
    source_id: str,
    name: str = "",
    source_business_operation_id: Optional[str] = None,
    category=None,
) -> Optional[CashFlow]:
    """
    Creates an auto-cashflow record within the transaction.
    Guarantees idempotency on (company, source_kind, source_id, type, amount).
    """
    if amount is None:
        return None
    try:
        amount_dec = Decimal(str(amount)).quantize(_Q2, rounding=ROUND_HALF_UP)
    except Exception:
        amount_dec = Decimal("0.00")

    if amount_dec <= Decimal("0.00"):
        return None

    # Idempotency check
    existing = CashFlow.objects.filter(
        company=company,
        source_kind=source_kind,
        source_id=str(source_id),
        type=type,
        amount=amount_dec,
    ).first()
    if existing:
        return existing

    # Resolve cashbox
    if cashbox is None:
        cashbox = resolve_auto_cashbox(
            company=company,
            user=user,
            branch=branch,
            cashbox_id=cashbox_id,
            require_cashbox=True,
        )

    actual_branch = branch or (cashbox.branch if cashbox else None)

    # Shift & cashier
    actual_shift = shift
    if actual_shift is None and cashbox and user and getattr(user, "is_authenticated", False):
        actual_shift = CashShift.objects.filter(
            cashbox=cashbox,
            cashier=user,
            status=CashShift.Status.OPEN,
        ).first()

    actual_cashier = (
        user
        if (user and getattr(user, "is_authenticated", False))
        else (actual_shift.cashier if actual_shift else None)
    )

    status = resolve_auto_cashflow_status(company)

    default_names = {
        CashFlow.SourceKind.POS_SALE: "Продажа",
        CashFlow.SourceKind.POS_PREPAYMENT: "Предоплата",
        CashFlow.SourceKind.DEBT_REPAYMENT: "Оплата долга",
        CashFlow.SourceKind.WAREHOUSE_PURCHASE: "Склад",
        CashFlow.SourceKind.PROCUREMENT_RECEIPT: "Закупки",
        CashFlow.SourceKind.SUPPLIER_RETURN: "Возврат поставщику",
        CashFlow.SourceKind.DEFECT_WRITEOFF: "Списание брака",
        CashFlow.SourceKind.PRODUCT_RETURN: "Возврат товара",
    }

    final_name = (name or "").strip() or default_names.get(source_kind, "Авто-операция")
    legacy_op_id = source_business_operation_id or default_names.get(source_kind, "Авто-операция")

    cf = CashFlow(
        company=company,
        branch=actual_branch,
        cashbox=cashbox,
        shift=actual_shift,
        cashier=actual_cashier,
        type=type,
        name=final_name,
        amount=amount_dec,
        status=status,
        source_kind=source_kind,
        source_id=str(source_id),
        source_business_operation_id=legacy_op_id,
        category=category,
    )
    cf.save()
    return cf


def serialize_auto_cashflows(cashflows: List[Optional[CashFlow]]) -> List[Dict[str, Any]]:
    """
    Serializes cashflow objects into the standard API response format.
    """
    res = []
    for cf in cashflows:
        if cf is None:
            continue
        res.append({
            "id": str(cf.id),
            "type": cf.type,
            "amount": f"{cf.amount:.2f}",
            "cashbox": str(cf.cashbox_id) if cf.cashbox_id else None,
            "status": cf.status,
            "source_kind": cf.source_kind or "",
            "source_id": cf.source_id or "",
            "name": cf.name or "",
        })
    return res


def handle_cashflow_reject(cashflow: CashFlow, user=None):
    """
    Reject cascade:
    - pos_sale, pos_prepayment: delete related sale
    - debt_repayment: rollback deal/debt payment
    - warehouse_purchase: delete product
    - procurement_receipt, supplier_return, defect_writeoff, product_return: unpost document
    """
    source_kind = cashflow.source_kind
    if not source_kind:
        legacy_map = {
            "Продажа": CashFlow.SourceKind.POS_SALE,
            "Оплата долга": CashFlow.SourceKind.DEBT_REPAYMENT,
            "Склад": CashFlow.SourceKind.WAREHOUSE_PURCHASE,
            "Закупки": CashFlow.SourceKind.PROCUREMENT_RECEIPT,
            "Возврат поставщику": CashFlow.SourceKind.SUPPLIER_RETURN,
            "Списание брака": CashFlow.SourceKind.DEFECT_WRITEOFF,
            "Возврат товара": CashFlow.SourceKind.PRODUCT_RETURN,
        }
        op_id = (cashflow.source_business_operation_id or "").strip()
        source_kind = legacy_map.get(op_id)

    source_id = cashflow.source_id or cashflow.source_cashbox_flow_id
    if not source_id and not source_kind:
        return

    company = cashflow.company

    if source_kind in (CashFlow.SourceKind.POS_SALE, CashFlow.SourceKind.POS_PREPAYMENT):
        from apps.main.models import Sale
        if source_id:
            Sale.objects.filter(company=company, id=source_id).delete()

    elif source_kind == CashFlow.SourceKind.DEBT_REPAYMENT:
        from apps.main.models import ClientDeal, DealPayment, Debt, DebtPayment
        if source_id:
            deal = ClientDeal.objects.filter(company=company, id=source_id).first()
            if deal:
                pay = (
                    DealPayment.objects.filter(deal=deal, kind=DealPayment.Kind.PAY)
                    .order_by("-paid_date", "-id")
                    .first()
                )
                if pay:
                    inst = pay.installment
                    if inst:
                        new_paid = max(Decimal("0.00"), (inst.paid_amount or Decimal("0.00")) - pay.amount)
                        inst.paid_amount = new_paid
                        if new_paid < inst.amount:
                            inst.paid_on = None
                        inst.save(update_fields=["paid_amount", "paid_on"])
                    pay.delete()
            else:
                debt = Debt.objects.filter(company=company, id=source_id).first()
                if debt:
                    dp = DebtPayment.objects.filter(debt=debt).order_by("-paid_at", "-id").first()
                    if dp:
                        dp.delete()

    elif source_kind == CashFlow.SourceKind.WAREHOUSE_PURCHASE:
        from apps.main.models import Product
        if source_id:
            prod = Product.objects.filter(company=company, id=source_id).first()
            if prod:
                try:
                    prod.delete()
                except ProtectedError:
                    raise DRFValidationError({
                        "detail": f"Невозможно удалить товар «{prod.name}»: на него ссылаются другие объекты."
                    })

    elif source_kind in (
        CashFlow.SourceKind.PROCUREMENT_RECEIPT,
        CashFlow.SourceKind.SUPPLIER_RETURN,
        CashFlow.SourceKind.DEFECT_WRITEOFF,
        CashFlow.SourceKind.PRODUCT_RETURN,
    ):
        from apps.warehouse import models as wh_models
        from apps.warehouse import services as wh_services
        if source_id:
            doc = wh_models.Document.objects.filter(company=company, id=source_id).first()
            if doc:
                try:
                    wh_services.unpost_document(doc)
                    doc.status = wh_models.Document.Status.REJECTED
                    doc.save(update_fields=["status"])
                except Exception as exc:
                    raise DRFValidationError({
                        "detail": f"Невозможно откатить документ {doc.number}: {exc}"
                    })
