from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.cafe.models import (
    DishIngredient,
    DishIngredientProcessing,
    MenuItem,
    Preparation,
    PreparationProcessing,
    ProcessingType,
    Warehouse,
)


SUPPORTED_UNITS = {"kg", "g", "l", "ml", "pcs"}


def _norm_unit(unit: str | None) -> str:
    s = (unit or "").strip().lower()
    s = s.replace(".", "").replace(",", "").strip()
    if not s:
        return ""
    mapping = {
        # weight
        "кг": "kg",
        "kg": "kg",
        "килограмм": "kg",
        "килограммы": "kg",
        "г": "g",
        "гр": "g",
        "грамм": "g",
        "граммы": "g",
        "g": "g",
        # volume
        "л": "l",
        "l": "l",
        "литр": "l",
        "литры": "l",
        "мл": "ml",
        "ml": "ml",
        "миллилитр": "ml",
        "миллилитры": "ml",
        # pieces
        "шт": "pcs",
        "штука": "pcs",
        "штуки": "pcs",
        "piece": "pcs",
        "pieces": "pcs",
        "pc": "pcs",
        "pcs": "pcs",
    }
    return mapping.get(s, s)


def convert_quantity(quantity: Decimal, from_unit: str, to_unit: str) -> Decimal:
    """
    Поддерживаем: kg, g, l, ml, pcs
    Внутренние базы: g, ml, pcs
    """
    if quantity is None:
        return Decimal("0")
    q = Decimal(quantity)
    fu = _norm_unit(from_unit)
    tu = _norm_unit(to_unit)
    if fu == tu:
        return q
    if fu not in SUPPORTED_UNITS or tu not in SUPPORTED_UNITS:
        raise ValueError("Unsupported unit.")

    # weight
    if fu in ("kg", "g") and tu in ("kg", "g"):
        g = q * Decimal("1000") if fu == "kg" else q
        return (g / Decimal("1000")) if tu == "kg" else g

    # volume
    if fu in ("l", "ml") and tu in ("l", "ml"):
        ml = q * Decimal("1000") if fu == "l" else q
        return (ml / Decimal("1000")) if tu == "l" else ml

    # pcs only convertible to pcs
    raise ValueError("Incompatible unit conversion.")


def calculate_preparation(prep: Preparation, *, raw_unit_cost: Decimal | None = None) -> dict:
    """
    Возвращает расчётные поля заготовки (без сохранения).
    raw_unit_cost: себестоимость единицы исходного продукта (в его unit).
    """
    input_q = Decimal(prep.input_quantity or 0)
    output_q = Decimal(prep.output_quantity or 0)
    if input_q <= 0 or output_q <= 0:
        raise ValueError("input_quantity/output_quantity must be > 0")

    loss_q = input_q - output_q
    loss_percent = (loss_q / input_q * Decimal("100")) if input_q else Decimal("0")

    if raw_unit_cost is None:
        raw_unit_cost = Decimal(prep.source_product.unit_price or 0)

    raw_cost = (raw_unit_cost * input_q).quantize(Decimal("0.01"))
    proc_cost = Decimal(prep.processing_cost or 0).quantize(Decimal("0.01"))
    for row in prep.processings.all():
        c = Decimal(row.cost or 0)
        if row.charge_type == PreparationProcessing.ChargeType.FIXED:
            proc_cost += c
        else:
            if row.output_quantity is not None:
                row_unit = _norm_unit(row.output_unit) or _norm_unit(prep.output_unit)
                qty_in_prep_unit = convert_quantity(Decimal(row.output_quantity), row_unit, _norm_unit(prep.output_unit))
                base_qty = qty_in_prep_unit
            else:
                base_qty = output_q
            proc_cost += (c * base_qty).quantize(Decimal("0.01"))
    proc_cost = proc_cost.quantize(Decimal("0.01"))
    total_cost = (raw_cost + proc_cost).quantize(Decimal("0.01"))
    unit_cost = (total_cost / output_q).quantize(Decimal("0.0001"))

    return {
        "loss_quantity": loss_q,
        "loss_percent": loss_percent.quantize(Decimal("0.01")),
        "raw_material_cost": raw_cost,
        "total_cost": total_cost,
        "unit_cost": unit_cost,
    }


def calculate_ingredient(ing: DishIngredient) -> dict:
    """
    Возвращает поля себестоимости ингредиента (без сохранения).
    quantity/unit у ингредиента задаётся в единицах блюда.
    """
    qty = Decimal(ing.quantity or 0)
    if qty <= 0:
        raise ValueError("quantity must be > 0")

    unit = _norm_unit(ing.unit)

    if ing.ingredient_type == DishIngredient.IngredientType.PRODUCT:
        if not ing.product_id:
            raise ValueError("product required")
        src_unit_cost = Decimal(ing.product.unit_price or 0).quantize(Decimal("0.0001"))
        src_unit = _norm_unit(ing.product.unit)
        # цена хранится "за единицу товара на складе"
        qty_in_src_unit = convert_quantity(qty, unit, src_unit)
        ingredient_cost = (src_unit_cost * qty_in_src_unit).quantize(Decimal("0.01"))
        unit_cost = src_unit_cost
    else:
        if not ing.preparation_id:
            raise ValueError("preparation required")
        src_unit_cost = Decimal(ing.preparation.unit_cost or 0).quantize(Decimal("0.0001"))
        src_unit = _norm_unit(ing.preparation.output_unit)
        qty_in_src_unit = convert_quantity(qty, unit, src_unit)
        ingredient_cost = (src_unit_cost * qty_in_src_unit).quantize(Decimal("0.01"))
        unit_cost = src_unit_cost

    processing_total = Decimal("0.00")
    qs = ing.processings.select_related("processing_type", "preparation_processing").all()
    for p in qs:
        if p.preparation_processing_id:
            row = p.preparation_processing
            if row.charge_type == PreparationProcessing.ChargeType.FIXED:
                c = Decimal(row.cost or 0)
            else:
                c = Decimal(row.cost or 0) * qty
        else:
            pt: ProcessingType = p.processing_type
            if pt.charge_type == ProcessingType.ChargeType.FIXED:
                c = Decimal(pt.cost or 0)
            else:
                # per_unit: ставка * quantity (в единицах ингредиента)
                c = Decimal(pt.cost or 0) * qty
        processing_total += c
    processing_total = processing_total.quantize(Decimal("0.01"))
    total = (ingredient_cost + processing_total).quantize(Decimal("0.01"))

    return {
        "unit_cost": unit_cost,
        "ingredient_cost": ingredient_cost,
        "processing_cost": processing_total,
        "total_cost": total,
    }


def calculate_margin(dish: MenuItem) -> dict:
    sale_price = Decimal(dish.price or 0)
    cost_price = Decimal(dish.cost_price or 0)
    margin_amount = (sale_price - cost_price).quantize(Decimal("0.01"))
    if sale_price and sale_price != 0:
        margin_percent = (margin_amount / sale_price * Decimal("100")).quantize(Decimal("0.01"))
    else:
        margin_percent = Decimal("0.00")
    return {"margin_amount": margin_amount, "margin_percent": margin_percent}


def recalculate_dish(dish: MenuItem, *, save: bool = True) -> MenuItem:
    """
    Себестоимость блюда:
      - если есть новые dish_ingredients -> считаем по ним
      - иначе оставляем старую логику по Ingredient (compat)
    """
    other = Decimal(dish.other_expenses or 0)
    if dish.dish_ingredients.exists():
        total = Decimal("0.00")
        for ing in dish.dish_ingredients.select_related("product", "preparation").prefetch_related(
            "processings__processing_type",
            "processings__preparation_processing",
        ):
            calc = calculate_ingredient(ing)
            ing.unit_cost = calc["unit_cost"]
            ing.ingredient_cost = calc["ingredient_cost"]
            ing.processing_cost = calc["processing_cost"]
            ing.total_cost = calc["total_cost"]
            if save:
                ing.save(update_fields=["unit_cost", "ingredient_cost", "processing_cost", "total_cost", "updated_at"])
            total += ing.total_cost
        dish.cost_price = (total + other).quantize(Decimal("0.01"))
    else:
        # legacy Ingredient model
        ingredients_cost = Decimal("0.00")
        for ingredient in dish.ingredients.select_related("product").all():
            unit_price = ingredient.product.unit_price or Decimal("0.00")
            amount = ingredient.amount or Decimal("0.00")
            ingredients_cost += Decimal(unit_price) * Decimal(amount)
        dish.cost_price = (ingredients_cost + other).quantize(Decimal("0.01"))

    m = calculate_margin(dish)
    dish.margin_amount = m["margin_amount"]
    dish.margin_percent = m["margin_percent"]

    if save:
        dish.save(update_fields=["cost_price", "margin_amount", "margin_percent", "updated_at"])
    return dish

