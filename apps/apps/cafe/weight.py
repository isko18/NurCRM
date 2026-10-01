"""Весовые блюда кафе: константы и валидация количества."""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

SALE_UNITS = ("kg", "g")
DEFAULT_SALE_UNIT = "kg"
WEIGHT_QTY_MIN_KG = Decimal("0.001")
WEIGHT_QTY_MIN_G = Decimal("1")
PIECE_QTY_MIN = Decimal("1")
QTY_QUANT = Decimal("0.001")


def normalize_sale_unit(value, *, sold_by_weight: bool = False) -> str:
    if not sold_by_weight:
        return DEFAULT_SALE_UNIT
    unit = (value or DEFAULT_SALE_UNIT).strip().lower()
    if unit not in SALE_UNITS:
        raise serializers.ValidationError({"sale_unit": ["Допустимые значения: kg, g."]})
    return unit


def min_order_quantity(*, is_sold_by_weight: bool, sale_unit: str = DEFAULT_SALE_UNIT) -> Decimal:
    if not is_sold_by_weight:
        return PIECE_QTY_MIN
    if sale_unit == "g":
        return WEIGHT_QTY_MIN_G
    return WEIGHT_QTY_MIN_KG


def min_refund_quantity(*, is_sold_by_weight: bool, sale_unit: str = DEFAULT_SALE_UNIT) -> Decimal:
    return min_order_quantity(is_sold_by_weight=is_sold_by_weight, sale_unit=sale_unit)


def quantize_quantity(value) -> Decimal:
    return Decimal(str(value)).quantize(QTY_QUANT)


def validate_order_item_quantity(
    quantity,
    *,
    is_sold_by_weight: bool,
    sale_unit: str = DEFAULT_SALE_UNIT,
) -> Decimal:
    try:
        q = quantize_quantity(quantity)
    except Exception:
        raise serializers.ValidationError({"quantity": ["Укажите корректное количество."]})

    if is_sold_by_weight:
        min_q = min_order_quantity(is_sold_by_weight=True, sale_unit=sale_unit)
        if q < min_q:
            if sale_unit == "g":
                raise serializers.ValidationError(
                    {"quantity": [f"Для весового блюда (г) укажите количество не меньше {min_q}."]}
                )
            raise serializers.ValidationError(
                {"quantity": ["Для весового блюда укажите количество не меньше 0.001."]}
            )
        return q

    if q < PIECE_QTY_MIN or q != q.to_integral_value():
        raise serializers.ValidationError(
            {"quantity": ["Для штучного блюда укажите целое количество не меньше 1."]}
        )
    return q


def validate_refund_quantity(
    quantity,
    *,
    is_sold_by_weight: bool,
    sale_unit: str = DEFAULT_SALE_UNIT,
    remaining: Decimal,
) -> Decimal:
    q = validate_order_item_quantity(
        quantity,
        is_sold_by_weight=is_sold_by_weight,
        sale_unit=sale_unit,
    )
    if q > remaining:
        raise serializers.ValidationError(
            {"quantity": [f"Некорректное количество (доступно к возврату: {remaining})."]}
        )
    return q


def format_quantity_api(value) -> str:
    """Строка quantity для API (до 3 знаков после запятой)."""
    q = quantize_quantity(value or 0)
    return f"{q:.3f}"


def menu_item_weight_snapshot(menu_item) -> tuple[bool, str]:
    if not menu_item:
        return False, DEFAULT_SALE_UNIT
    return (
        bool(getattr(menu_item, "is_sold_by_weight", False)),
        normalize_sale_unit(
            getattr(menu_item, "sale_unit", None),
            sold_by_weight=bool(getattr(menu_item, "is_sold_by_weight", False)),
        ),
    )
