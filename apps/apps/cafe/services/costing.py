from __future__ import annotations

from decimal import Decimal

from apps.cafe.models import (
    DishIngredient,
    MenuItem,
    Preparation,
    PreparationIngredient,
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
        "кг": "kg",
        "kg": "kg",
        "килограмм": "kg",
        "килограммы": "kg",
        "г": "g",
        "гр": "g",
        "грамм": "g",
        "граммы": "g",
        "g": "g",
        "л": "l",
        "l": "l",
        "литр": "l",
        "литры": "l",
        "мл": "ml",
        "ml": "ml",
        "миллилитр": "ml",
        "миллилитры": "ml",
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

    if fu in ("kg", "g") and tu in ("kg", "g"):
        g = q * Decimal("1000") if fu == "kg" else q
        return (g / Decimal("1000")) if tu == "kg" else g

    if fu in ("l", "ml") and tu in ("l", "ml"):
        ml = q * Decimal("1000") if fu == "l" else q
        return (ml / Decimal("1000")) if tu == "l" else ml

    raise ValueError("Incompatible unit conversion.")


def _waste_multiplier(waste_percent: Decimal) -> Decimal:
    wp = Decimal(waste_percent or 0)
    if wp < 0:
        raise ValueError("waste_percent must be >= 0.")
    if wp >= 100:
        raise ValueError("waste_percent must be < 100.")
    return Decimal("1") - (wp / Decimal("100"))


def _preparation_processing_extra(prep: Preparation, *, output_q: Decimal) -> Decimal:
    proc_cost = Decimal(prep.processing_cost or 0).quantize(Decimal("0.01"))
    for row in prep.processings.all():
        c = Decimal(row.cost or 0)
        if row.charge_type == PreparationProcessing.ChargeType.FIXED:
            proc_cost += c
        else:
            proc_cost += (c * output_q).quantize(Decimal("0.01"))
    return proc_cost.quantize(Decimal("0.01"))


def calculate_preparation_ingredient(row: PreparationIngredient) -> dict:
    """
    Себестоимость строки техкарты заготовки (без сохранения).
    """
    qty = Decimal(row.quantity or 0)
    if qty <= 0:
        raise ValueError("quantity must be > 0")

    unit = _norm_unit(row.unit)
    waste = Decimal(row.waste_percent or 0)
    divisor = _waste_multiplier(waste)

    processing_total = Decimal("0.00")

    if row.product_id:
        product = row.product
        src_unit_cost = Decimal(product.unit_price or 0).quantize(Decimal("0.0001"))
        src_unit = _norm_unit(product.unit)
        converted = convert_quantity(qty, unit, src_unit)
        net_quantity = (converted / divisor).quantize(Decimal("0.000001"))
        ingredient_cost = (src_unit_cost * net_quantity).quantize(Decimal("0.01"))
        unit_cost = src_unit_cost
    elif row.child_preparation_id:
        child = row.child_preparation
        src_unit_cost = Decimal(child.unit_cost or 0).quantize(Decimal("0.0001"))
        src_unit = _norm_unit(child.output_unit)
        converted = convert_quantity(qty, unit, src_unit)
        net_quantity = (converted / divisor).quantize(Decimal("0.000001"))
        ingredient_cost = (src_unit_cost * net_quantity).quantize(Decimal("0.01"))
        unit_cost = src_unit_cost
    else:
        raise ValueError("product or child_preparation required")

    total = (ingredient_cost + processing_total).quantize(Decimal("0.01"))

    return {
        "unit_cost": unit_cost,
        "ingredient_cost": ingredient_cost,
        "processing_cost": processing_total,
        "total_cost": total,
        "net_quantity": net_quantity,
        "net_unit": src_unit,
    }


def calculate_preparation(prep: Preparation, *, raw_unit_cost: Decimal | None = None) -> dict:
    """
    Расчётные поля заготовки (без сохранения).
    Если есть ingredients — техкарта; иначе legacy source_product.
    """
    output_q = Decimal(prep.output_quantity or 0)
    if output_q <= 0:
        raise ValueError("output_quantity must be > 0")

    if prep.ingredients.exists():
        ingredients_total = Decimal("0.00")
        raw_sum = Decimal("0.00")
        for row in prep.ingredients.select_related("product", "child_preparation").all():
            calc = calculate_preparation_ingredient(row)
            ingredients_total += calc["total_cost"]
            raw_sum += calc["ingredient_cost"]
        proc_cost = _preparation_processing_extra(prep, output_q=output_q)
        total_cost = (ingredients_total + proc_cost).quantize(Decimal("0.01"))
        unit_cost = (total_cost / output_q).quantize(Decimal("0.0001"))

        input_q = Decimal(prep.input_quantity or 0)
        loss_q = (input_q - output_q) if input_q > 0 else Decimal("0")
        loss_percent = (loss_q / input_q * Decimal("100")) if input_q > 0 else Decimal("0")

        return {
            "loss_quantity": loss_q,
            "loss_percent": loss_percent.quantize(Decimal("0.01")),
            "raw_material_cost": raw_sum.quantize(Decimal("0.01")),
            "processing_cost": proc_cost,
            "total_cost": total_cost,
            "unit_cost": unit_cost,
        }

    if not prep.source_product_id:
        raise ValueError("source_product required for legacy preparation")

    input_q = Decimal(prep.input_quantity or 0)
    if input_q <= 0:
        raise ValueError("input_quantity/output_quantity must be > 0")

    loss_q = input_q - output_q
    loss_percent = (loss_q / input_q * Decimal("100")) if input_q else Decimal("0")

    if raw_unit_cost is None:
        raw_unit_cost = Decimal(prep.source_product.unit_price or 0)

    raw_cost = (raw_unit_cost * input_q).quantize(Decimal("0.01"))
    proc_cost = _preparation_processing_extra(prep, output_q=output_q)
    total_cost = (raw_cost + proc_cost).quantize(Decimal("0.01"))
    unit_cost = (total_cost / output_q).quantize(Decimal("0.0001"))

    return {
        "loss_quantity": loss_q,
        "loss_percent": loss_percent.quantize(Decimal("0.01")),
        "raw_material_cost": raw_cost,
        "processing_cost": proc_cost,
        "total_cost": total_cost,
        "unit_cost": unit_cost,
    }


def check_preparation_cycle(parent_preparation: Preparation, child_preparation: Preparation) -> None:
    """
    Запрет циклов: child не должен (прямо или через цепочку) использовать parent.
    """
    if parent_preparation.id == child_preparation.id:
        raise ValueError("Заготовка не может использовать саму себя.")

    visited: set = set()
    stack = [child_preparation.id]

    while stack:
        current_id = stack.pop()
        if current_id in visited:
            continue
        visited.add(current_id)
        if current_id == parent_preparation.id:
            raise ValueError(
                "Циклическая зависимость заготовок: вложенная заготовка уже использует эту заготовку."
            )
        child_ids = PreparationIngredient.objects.filter(
            preparation_id=current_id,
            child_preparation_id__isnull=False,
        ).values_list("child_preparation_id", flat=True)
        for cid in child_ids:
            if cid not in visited:
                stack.append(cid)


def recalculate_preparation(preparation: Preparation, *, save: bool = True) -> Preparation:
    """Пересчитать строки техкарты и итоги заготовки."""
    if preparation.ingredients.exists():
        for row in preparation.ingredients.select_related("product", "child_preparation").all():
            calc = calculate_preparation_ingredient(row)
            row.unit_cost = calc["unit_cost"]
            row.ingredient_cost = calc["ingredient_cost"]
            row.processing_cost = calc["processing_cost"]
            row.total_cost = calc["total_cost"]
            if save:
                row.save(
                    update_fields=[
                        "unit_cost", "ingredient_cost", "processing_cost", "total_cost", "updated_at",
                    ]
                )

    calc = calculate_preparation(preparation)
    preparation.loss_quantity = calc["loss_quantity"]
    preparation.loss_percent = calc["loss_percent"]
    preparation.raw_material_cost = calc["raw_material_cost"]
    preparation.processing_cost = calc["processing_cost"]
    preparation.total_cost = calc["total_cost"]
    preparation.unit_cost = calc["unit_cost"]

    if save:
        preparation.save(
            update_fields=[
                "loss_quantity", "loss_percent", "raw_material_cost", "processing_cost",
                "total_cost", "unit_cost", "updated_at",
            ]
        )
    return preparation


def recalculate_dishes_by_preparation(preparation: Preparation) -> None:
    dish_ids = list(
        DishIngredient.objects.filter(preparation=preparation).values_list("dish_id", flat=True).distinct()
    )
    if not dish_ids:
        return
    for dish in MenuItem.objects.filter(id__in=dish_ids).all():
        recalculate_dish(dish, save=True)


def recalculate_preparation_tree(preparation: Preparation, visited: set | None = None) -> None:
    """
    Снизу вверх: дочерние заготовки → текущая → родители → блюда.
    """
    visited = visited if visited is not None else set()
    pid = preparation.id
    if pid in visited:
        return
    visited.add(pid)

    for row in preparation.ingredients.filter(child_preparation__isnull=False).select_related("child_preparation"):
        recalculate_preparation_tree(row.child_preparation, visited)

    recalculate_preparation(preparation, save=True)
    recalculate_dishes_by_preparation(preparation)

    parent_ids = (
        PreparationIngredient.objects.filter(child_preparation_id=pid)
        .values_list("preparation_id", flat=True)
        .distinct()
    )
    for parent in Preparation.objects.filter(id__in=parent_ids).all():
        recalculate_preparation_tree(parent, visited)


def recalculate_preparations_for_warehouse(warehouse: Warehouse) -> None:
    """Пересчёт заготовок при изменении unit_price склада."""
    prep_ids = (
        PreparationIngredient.objects.filter(product_id=warehouse.id)
        .values_list("preparation_id", flat=True)
        .distinct()
    )
    for prep in Preparation.objects.filter(id__in=prep_ids).all():
        recalculate_preparation_tree(prep)


# --- Dish ingredient (unchanged logic) ---

def calculate_ingredient(ing: DishIngredient) -> dict:
    from apps.cafe.models import DishIngredientProcessing

    qty = Decimal(ing.quantity or 0)
    if qty <= 0:
        raise ValueError("quantity must be > 0")

    unit = _norm_unit(ing.unit)

    if ing.ingredient_type == DishIngredient.IngredientType.PRODUCT:
        if not ing.product_id:
            raise ValueError("product required")
        src_unit_cost = Decimal(ing.product.unit_price or 0).quantize(Decimal("0.0001"))
        src_unit = _norm_unit(ing.product.unit)
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
      - если есть dish_ingredients -> по ним
      - иначе legacy Ingredient
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
