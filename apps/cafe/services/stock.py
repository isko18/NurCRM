from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.cafe.models import DishIngredient, MenuItem, Preparation, Warehouse
from apps.cafe.services.costing import convert_quantity, _norm_unit


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
    from apps.cafe.views import _decimal_from_warehouse_remainder  # избежать циклов: локально

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


@transaction.atomic
def consume_dish_for_order(dish: MenuItem, dish_qty: Decimal):
    """
    Списать ингредиенты блюда для количества порций dish_qty.
    Если блюдо на новой схеме dish_ingredients — списываем их, иначе старые Ingredient.
    """
    qty = Decimal(dish_qty or 0)
    if qty <= 0:
        return

    # Новая схема
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

    # Legacy Ingredient -> Warehouse
    from apps.cafe.views import _decimal_from_warehouse_remainder  # avoid cycles

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

