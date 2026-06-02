"""Авто-расходы «Закупки» при движении склада и посуды."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cafe.models import (
    CafeExpense,
    CafeExpenseCategory,
    CafeHouseholdItem,
    CafeHouseholdMovement,
    Warehouse,
    WarehouseMovement,
)

ZAKUPKI_SLUG = "zakupki"
ZAKUPKI_TITLE = "Закупки"
MIN_EXPENSE_AMOUNT = Decimal("0.01")


def _parse_remainder(raw) -> Decimal:
    if raw is None:
        return Decimal("0")
    try:
        return Decimal(str(raw).replace(",", ".").strip() or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def ensure_zakupki_category(company, branch=None) -> CafeExpenseCategory:
    cat, _ = CafeExpenseCategory.objects.get_or_create(
        company=company,
        slug=ZAKUPKI_SLUG,
        defaults={
            "title": ZAKUPKI_TITLE,
            "is_system": True,
            "branch": branch,
            "sort_order": 0,
        },
    )
    if not cat.is_system or cat.title != ZAKUPKI_TITLE:
        cat.is_system = True
        cat.title = ZAKUPKI_TITLE
        cat.save(update_fields=["is_system", "title"])
    return cat


def _existing_auto_expense(company, source: str, source_id) -> CafeExpense | None:
    if not source_id:
        return None
    return CafeExpense.objects.filter(
        company=company, source=source, source_id=source_id,
    ).first()


@transaction.atomic
def create_warehouse_initial_expense(*, warehouse: Warehouse, user) -> CafeExpense | None:
    """Начальный остаток при POST /warehouse/ (без повторного увеличения remainder)."""
    qty = _parse_remainder(warehouse.remainder)
    price = Decimal(str(warehouse.unit_price or 0))
    amount = (qty * price).quantize(Decimal("0.01"))
    if amount < MIN_EXPENSE_AMOUNT:
        return None
    source = CafeExpense.Source.WAREHOUSE_CREATE
    existing = _existing_auto_expense(warehouse.company, source, warehouse.id)
    if existing:
        return existing
    cat = ensure_zakupki_category(warehouse.company, warehouse.branch)
    try:
        return CafeExpense.objects.create(
            company=warehouse.company,
            branch=warehouse.branch,
            title=f"Закупка: {warehouse.title}",
            amount=amount,
            category=ZAKUPKI_TITLE,
            category_slug=ZAKUPKI_SLUG,
            expense_category=cat,
            source=source,
            source_id=warehouse.id,
            expense_date=timezone.localdate(),
            note="Создание позиции",
            created_by=user if user and getattr(user, "is_authenticated", False) else None,
        )
    except IntegrityError:
        return _existing_auto_expense(warehouse.company, source, warehouse.id)


@transaction.atomic
def create_warehouse_receipt_expense(
    *,
    warehouse: Warehouse,
    quantity: Decimal,
    unit_price: Decimal,
    user,
    source: str,
    note: str,
    skip_remainder_update: bool = False,
) -> tuple[WarehouseMovement, CafeExpense | None]:
    """
    Оприходование: движение склада + расход «Закупки».
    skip_remainder_update=True — остаток уже обновлён (PUT warehouse); только журнал и расход.
    """
    qty = Decimal(str(quantity))
    price = Decimal(str(unit_price or 0))
    amount = (qty * price).quantize(Decimal("0.01"))

    if skip_remainder_update:
        after = _parse_remainder(warehouse.remainder)
        before = after - qty
    else:
        before = _parse_remainder(warehouse.remainder)
        after = before + qty

    movement = WarehouseMovement.objects.create(
        warehouse=warehouse,
        movement_type=WarehouseMovement.MovementType.IN,
        quantity=qty,
        unit_price=price,
        remainder_before=str(before),
        remainder_after=str(after),
        note=note or "",
        created_by=user if user and getattr(user, "is_authenticated", False) else None,
    )

    if not skip_remainder_update:
        warehouse.remainder = str(after)
        warehouse.save(update_fields=["remainder"])

    expense = None
    if amount >= MIN_EXPENSE_AMOUNT:
        existing = _existing_auto_expense(warehouse.company, source, movement.id)
        if existing:
            movement.expense = existing
            movement.save(update_fields=["expense"])
            return movement, existing

        cat = ensure_zakupki_category(warehouse.company, warehouse.branch)
        try:
            expense = CafeExpense.objects.create(
                company=warehouse.company,
                branch=warehouse.branch,
                title=f"Закупка: {warehouse.title}",
                amount=amount,
                category=ZAKUPKI_TITLE,
                category_slug=ZAKUPKI_SLUG,
                expense_category=cat,
                source=source,
                source_id=movement.id,
                expense_date=timezone.localdate(),
                note=note or "",
                created_by=user if user and getattr(user, "is_authenticated", False) else None,
            )
        except IntegrityError:
            expense = _existing_auto_expense(warehouse.company, source, movement.id)
        if expense:
            movement.expense = expense
            movement.save(update_fields=["expense"])

    return movement, expense


@transaction.atomic
def household_receive(
    *,
    item: CafeHouseholdItem,
    quantity: Decimal,
    unit_price: Decimal | None,
    user,
    note: str = "",
) -> tuple[CafeHouseholdMovement, CafeExpense | None]:
    qty = Decimal(str(quantity))
    if qty <= 0:
        raise ValueError("quantity должно быть больше нуля.")

    before = item.remainder or Decimal("0")
    after = before + qty

    movement = CafeHouseholdMovement.objects.create(
        item=item,
        movement_type=CafeHouseholdMovement.MovementType.IN,
        quantity=qty,
        unit_price=unit_price,
        remainder_before=before,
        remainder_after=after,
        note=note or "",
        created_by=user if user and getattr(user, "is_authenticated", False) else None,
    )

    item.remainder = after
    item.save(update_fields=["remainder", "updated_at"])

    expense = None
    price = Decimal(str(unit_price)) if unit_price is not None else None
    if price is not None and price >= 0:
        amount = (qty * price).quantize(Decimal("0.01"))
        if amount >= MIN_EXPENSE_AMOUNT:
            source = CafeExpense.Source.HOUSEHOLD_RECEIPT
            existing = _existing_auto_expense(item.company, source, movement.id)
            if existing:
                movement.expense = existing
                movement.save(update_fields=["expense"])
                return movement, existing
            cat = ensure_zakupki_category(item.company, item.branch)
            try:
                expense = CafeExpense.objects.create(
                    company=item.company,
                    branch=item.branch,
                    title=f"Закупка: {item.title}",
                    amount=amount,
                    category=ZAKUPKI_TITLE,
                    category_slug=ZAKUPKI_SLUG,
                    expense_category=cat,
                    source=source,
                    source_id=movement.id,
                    expense_date=timezone.localdate(),
                    note=note or "",
                    created_by=user if user and getattr(user, "is_authenticated", False) else None,
                )
            except IntegrityError:
                expense = _existing_auto_expense(item.company, source, movement.id)
            if expense:
                movement.expense = expense
                movement.save(update_fields=["expense"])

    return movement, expense


@transaction.atomic
def household_write_off(*, item: CafeHouseholdItem, quantity: Decimal, user, note: str = "") -> CafeHouseholdMovement:
    qty = Decimal(str(quantity))
    if qty <= 0:
        raise ValueError("quantity должно быть больше нуля.")
    before = item.remainder or Decimal("0")
    if qty > before:
        raise ValueError("Недостаточно остатка для списания.")
    after = before - qty
    movement = CafeHouseholdMovement.objects.create(
        item=item,
        movement_type=CafeHouseholdMovement.MovementType.OUT,
        quantity=qty,
        remainder_before=before,
        remainder_after=after,
        note=note or "",
        created_by=user if user and getattr(user, "is_authenticated", False) else None,
    )
    item.remainder = after
    item.save(update_fields=["remainder", "updated_at"])
    return movement
