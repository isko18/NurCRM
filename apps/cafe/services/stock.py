from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.cafe.models import DishIngredient, MenuItem, Preparation, PreparationIngredient, Warehouse
from apps.cafe.services.costing import (
    calculate_preparation_ingredient,
    convert_quantity,
    _norm_unit,
)


def validate_stock(have: Decimal, need: Decimal, *, label: str):
    if need <= 0:
        return
    if have < need:
        raise ValidationError({"detail": f"Недостаточно на складе: {label}. Нужно {need}, есть {have}."})


def consume_product(product: Warehouse, quantity: Decimal, *, quantity_unit: str):
    """
    Warehouse.remainder в cafe — CharField.
    quantity передаётся в единицах quantity_unit, продукт хранит unit (product.unit).
    """
    from apps.cafe.views import _decimal_from_warehouse_remainder

    q = Decimal(quantity or 0)
    if q <= 0:
        return
    to_unit = _norm_unit(product.unit)
    q_in_prod_unit = convert_quantity(q, _norm_unit(quantity_unit), to_unit)
    have = _decimal_from_warehouse_remainder(product.remainder)
    validate_stock(have, q_in_prod_unit, label=product.title)
    product.remainder = str(have - q_in_prod_unit)
    product.save(update_fields=["remainder"])


def consume_preparation(prep: Preparation, quantity: Decimal, *, quantity_unit: str):
    q = Decimal(quantity or 0)
    if q <= 0:
        return
    to_unit = _norm_unit(prep.output_unit)
    q_in_prep_unit = convert_quantity(q, _norm_unit(quantity_unit), to_unit)
    have = Decimal(prep.stock_quantity or 0)
    validate_stock(have, q_in_prep_unit, label=prep.name)
    prep.stock_quantity = have - q_in_prep_unit
    prep.save(update_fields=["stock_quantity", "updated_at"])


def add_preparation_stock(prep: Preparation, quantity: Decimal, *, quantity_unit: str):
    q = Decimal(quantity or 0)
    if q <= 0:
        return
    to_unit = _norm_unit(prep.output_unit)
    q_in_prep_unit = convert_quantity(q, _norm_unit(quantity_unit), to_unit)
    prep.stock_quantity = Decimal(prep.stock_quantity or 0) + q_in_prep_unit
    prep.save(update_fields=["stock_quantity", "updated_at"])


def _receive_scale(prep: Preparation, batch_output_quantity: Decimal | None) -> Decimal:
    recipe_out = Decimal(prep.output_quantity or 0)
    if recipe_out <= 0:
        raise ValidationError({"detail": "output_quantity заготовки должен быть > 0."})
    batch = batch_output_quantity if batch_output_quantity is not None else recipe_out
    if batch <= 0:
        raise ValidationError({"detail": "Количество выпуска должно быть > 0."})
    return batch / recipe_out


@transaction.atomic
def receive_preparation(
    prep: Preparation,
    *,
    batch_output_quantity: Decimal | None = None,
    input_quantity: Decimal | None = None,
    output_quantity: Decimal | None = None,
    processing_cost: Decimal | None = None,
):
    """
    Оприходование заготовки.
    Техкарта (ingredients): списание по строкам, приход по batch output.
    Legacy: списание source_product по input, приход по output.
    """
    from apps.cafe.services.costing import recalculate_preparation

    if prep.ingredients.exists():
        scale = _receive_scale(prep, batch_output_quantity)
        batch_out = (
            batch_output_quantity
            if batch_output_quantity is not None
            else Decimal(prep.output_quantity or 0)
        )

        rows = list(
            prep.ingredients.select_related("product", "child_preparation").select_for_update(of=("self",))
        )
        for row in rows:
            calc = calculate_preparation_ingredient(row)
            need = (calc["net_quantity"] * scale).quantize(Decimal("0.000001"))
            net_unit = calc["net_unit"]
            if row.product_id:
                product = Warehouse.objects.select_for_update().get(pk=row.product_id)
                consume_product(product, need, quantity_unit=net_unit)
            elif row.child_preparation_id:
                child = Preparation.objects.select_for_update().get(pk=row.child_preparation_id)
                consume_preparation(child, need, quantity_unit=net_unit)

        add_preparation_stock(prep, batch_out, quantity_unit=prep.output_unit)
        recalculate_preparation(prep, save=True)
        return

    if not prep.source_product_id:
        raise ValidationError({"detail": "Укажите source_product или строки техкарты (ingredients)."})

    if input_quantity is None or output_quantity is None:
        raise ValidationError(
            {"detail": "Для legacy-заготовки укажите input_quantity и output_quantity."}
        )

    if input_quantity is not None:
        prep.input_quantity = input_quantity
    if output_quantity is not None:
        prep.output_quantity = output_quantity
    if processing_cost is not None:
        prep.processing_cost = processing_cost

    recalculate_preparation(prep, save=False)
    product = Warehouse.objects.select_for_update().get(pk=prep.source_product_id)
    consume_product(product, prep.input_quantity, quantity_unit=prep.input_unit)
    add_preparation_stock(prep, prep.output_quantity, quantity_unit=prep.output_unit)

    prep.save(
        update_fields=[
            "input_quantity",
            "output_quantity",
            "processing_cost",
            "loss_quantity",
            "loss_percent",
            "raw_material_cost",
            "total_cost",
            "unit_cost",
            "updated_at",
        ]
    )


@transaction.atomic
def consume_dish_for_order(dish: MenuItem, dish_qty: Decimal):
    """
    Списать ингредиенты блюда для количества порций dish_qty.
    """
    qty = Decimal(dish_qty or 0)
    if qty <= 0:
        return

    if dish.dish_ingredients.exists():
        ings = dish.dish_ingredients.select_related("product", "preparation").select_for_update(of=("self",))
        for ing in ings:
            need = (Decimal(ing.quantity or 0) * qty)
            if need <= 0:
                continue
            if ing.ingredient_type == DishIngredient.IngredientType.PRODUCT:
                consume_product(ing.product, need, quantity_unit=ing.unit)
            else:
                consume_preparation(ing.preparation, need, quantity_unit=ing.unit)
        return

    from apps.cafe.views import _decimal_from_warehouse_remainder

    usage_by_product_id: dict = {}
    for ing in dish.ingredients.select_related("product").all():
        need = (Decimal(ing.amount or 0) * qty)
        if need <= 0:
            continue
        usage_by_product_id[ing.product_id] = usage_by_product_id.get(ing.product_id, Decimal("0")) + need
    if not usage_by_product_id:
        return

    products = Warehouse.objects.select_for_update().filter(id__in=list(usage_by_product_id.keys()))
    products_by_id = {p.id: p for p in products}
    missing = [str(pid) for pid in usage_by_product_id.keys() if pid not in products_by_id]
    if missing:
        raise ValidationError({"detail": f"Не найдены товары склада для ингредиентов: {', '.join(missing)}"})

    for pid, need in usage_by_product_id.items():
        p = products_by_id[pid]
        have = _decimal_from_warehouse_remainder(p.remainder)
        validate_stock(have, need, label=p.title)
        p.remainder = str(have - need)
        p.save(update_fields=["remainder"])
