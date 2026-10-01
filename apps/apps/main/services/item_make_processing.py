from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from apps.main.models import ItemMake

_Q2 = Decimal("0.01")
_Q3 = Decimal("0.001")


def item_make_recipe_ready_filter():
    """Сырьё, доступное для рецепта: обработанное или сырое без обязательной обработки."""
    from django.db.models import Q
    return Q(kind=ItemMake.Kind.PROCESSED) | Q(
        kind=ItemMake.Kind.RAW,
        needs_processing=False,
    )


def assert_item_make_recipe_ready(im: ItemMake) -> None:
    if im.kind == ItemMake.Kind.RAW and im.needs_processing:
        raise ValueError(
            f"Сырьё «{im.name}» требует обработки. Сначала выполните /items-make/{{id}}/process/ "
            f"и добавьте в рецепт обработанную позицию."
        )


def calc_recipe_unit_cost(recipe_entries, ims_map) -> Decimal:
    """Себестоимость 1 ед. готового товара по рецепту (sum qty_per_unit × price сырья)."""
    total = Decimal("0")
    for entry in recipe_entries:
        im = ims_map[entry["id"]]
        total += entry["qty_per_unit"] * Decimal(str(im.price or 0))
    return total.quantize(_Q2, rounding=ROUND_HALF_UP)


def _merge_unit_price(
    existing_qty: Decimal,
    existing_price: Decimal,
    input_qty: Decimal,
    source_price: Decimal,
    output_qty: Decimal,
    processing_cost: Decimal,
) -> Decimal:
    old_value = existing_qty * existing_price
    batch_value = input_qty * source_price + processing_cost
    new_qty = existing_qty + output_qty
    if new_qty <= 0:
        return Decimal("0.00")
    return ((old_value + batch_value) / new_qty).quantize(_Q2, rounding=ROUND_HALF_UP)


def _batch_unit_price(
    input_qty: Decimal,
    source_price: Decimal,
    output_qty: Decimal,
    processing_cost: Decimal,
) -> Decimal:
    if output_qty <= 0:
        return Decimal("0.00")
    batch_value = input_qty * source_price + processing_cost
    return (batch_value / output_qty).quantize(_Q2, rounding=ROUND_HALF_UP)


@transaction.atomic
def process_raw_item_make(
    source: ItemMake,
    *,
    input_quantity: Decimal,
    output_quantity: Decimal,
    name: str | None = None,
    processing_cost: Decimal = Decimal("0"),
    target: ItemMake | None = None,
) -> tuple[ItemMake, ItemMake]:
    """
    Списывает сырьё и создаёт/пополняет обработанную позицию на складе.

    Returns:
        (source, processed_item)
    """
    source = ItemMake.objects.select_for_update().get(pk=source.pk)

    if source.kind != ItemMake.Kind.RAW:
        raise ValueError("Обрабатывать можно только необработанное сырьё (kind=raw).")
    if not source.needs_processing:
        raise ValueError(f"Сырьё «{source.name}» не требует обработки.")
    if input_quantity <= 0:
        raise ValueError("input_quantity должно быть > 0.")
    if output_quantity <= 0:
        raise ValueError("output_quantity должно быть > 0.")
    if input_quantity > source.quantity:
        raise ValueError(
            f"Недостаточно сырья «{source.name}»: требуется {input_quantity}, доступно {source.quantity}."
        )
    if processing_cost < 0:
        raise ValueError("processing_cost не может быть отрицательной.")

    source.quantity = (source.quantity - input_quantity).quantize(_Q3, rounding=ROUND_HALF_UP)
    source.save(update_fields=["quantity", "updated_at"])

    batch_price = _batch_unit_price(
        input_quantity,
        Decimal(str(source.price or 0)),
        output_quantity,
        processing_cost,
    )

    if target is not None:
        target = ItemMake.objects.select_for_update().get(pk=target.pk)
        if target.kind != ItemMake.Kind.PROCESSED:
            raise ValueError("target должен быть обработанным сырьём (kind=processed).")
        if target.source_id != source.id:
            raise ValueError("target должен быть получен из того же исходного сырья.")
        if target.company_id != source.company_id:
            raise ValueError("target принадлежит другой компании.")
        if (target.branch_id or None) != (source.branch_id or None):
            raise ValueError("target принадлежит другому филиалу.")

        existing_qty = Decimal(str(target.quantity or 0))
        existing_price = Decimal(str(target.price or 0))
        target.price = _merge_unit_price(
            existing_qty,
            existing_price,
            input_quantity,
            Decimal(str(source.price or 0)),
            output_quantity,
            processing_cost,
        )
        target.quantity = (existing_qty + output_quantity).quantize(_Q3, rounding=ROUND_HALF_UP)
        target.save(update_fields=["price", "quantity", "updated_at"])
        processed = target
    else:
        processed = ItemMake.objects.create(
            company=source.company,
            branch=source.branch,
            kind=ItemMake.Kind.PROCESSED,
            source=source,
            needs_processing=False,
            name=(name or f"{source.name} (обработанное)").strip(),
            supplier=source.supplier,
            price=batch_price,
            unit=source.unit,
            quantity=output_quantity.quantize(_Q3, rounding=ROUND_HALF_UP),
        )

    return source, processed
