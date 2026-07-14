"""
Фильтры GET /api/main/products/list/ (склад маркета, модалка «Фильтры»).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.main.models import Product, Sale, SaleItem

VALID_KINDS = {c[0] for c in Product.Kind.choices}
VALID_PRESETS = {
    "discounted",
    "shelf_life_expires_7d",
    "zero_cost",
    "shelf_life_expired",
    "out_of_stock",
    "not_sold_90d",
    "negative_stock",
    "stock_below_min",
}

# Обратная совместимость с кириллическими slug (старый фронт)
_PRESET_ALIASES = {
    "товары_со_скидкой": "discounted",
    "срок_годности_7д": "shelf_life_expires_7d",
    "срок_годности_истекает_7д": "shelf_life_expires_7d",
    "нулевая_себестоимость": "zero_cost",
    "истёк_срок_годности": "shelf_life_expired",
    "истек_срок_годности": "shelf_life_expired",
    "нет_в_наличии": "out_of_stock",
    "не_продаются_3_месяца": "not_sold_90d",
    "не_продаются_90д": "not_sold_90d",
    "отрицательный_остаток": "negative_stock",
    "остаток_ниже_минимума": "stock_below_min",
    "общий_остаток_меньше_минимального": "stock_below_min",
}


def _parse_decimal(raw) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _parse_int(raw) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _parse_bool(raw) -> bool | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def _parse_kind_list(query_params) -> list[str]:
    raw: list[str] = []
    raw.extend(query_params.getlist("kind"))
    raw.extend(query_params.getlist("kind[]"))
    single = (query_params.get("kind") or "").strip()
    if single:
        if "," in single:
            raw.extend(x.strip() for x in single.split(",") if x.strip())
        else:
            raw.append(single)
    kinds = []
    for k in raw:
        v = str(k).strip().lower()
        if v in VALID_KINDS:
            kinds.append(v)
    return list(dict.fromkeys(kinds))


def _normalize_preset(raw: str | None) -> str | None:
    if not raw:
        return None
    p = str(raw).strip().lower()
    p = _PRESET_ALIASES.get(p, p)
    return p if p in VALID_PRESETS else None


def _compare_field(qs, field_name: str, condition: str, value: Decimal):
    if condition == "gt":
        return qs.filter(**{f"{field_name}__gt": value})
    if condition == "lt":
        return qs.filter(**{f"{field_name}__lt": value})
    return qs.filter(**{f"{field_name}": value})


def _price_field(price_type: str) -> str | None:
    mapping = {
        "base": "price",
        "purchase": "purchase_price",
        "cost": "purchase_price",
        "discount": "discount_percent",
    }
    return mapping.get((price_type or "").strip().lower())


def _apply_preset(qs, preset: str):
    today = timezone.localdate()

    if preset == "discounted":
        return qs.filter(Q(discount_percent__gt=0) | Q(stock=True))
    if preset == "shelf_life_expires_7d":
        end = today + timedelta(days=7)
        return qs.filter(
            expiration_date__isnull=False,
            expiration_date__gte=today,
            expiration_date__lte=end,
        )
    if preset == "zero_cost":
        return qs.filter(Q(purchase_price__isnull=True) | Q(purchase_price=0))
    if preset == "shelf_life_expired":
        return qs.filter(expiration_date__isnull=False, expiration_date__lt=today)
    if preset == "out_of_stock":
        return qs.filter(Q(quantity__isnull=True) | Q(quantity__lte=0))
    if preset == "negative_stock":
        return qs.filter(quantity__lt=0)
    if preset == "not_sold_90d":
        return _apply_not_sold_within(qs, 90)
    if preset == "stock_below_min":
        return qs.filter(
            minimum_quantity__isnull=False,
            minimum_quantity__gt=0,
        ).filter(quantity__lt=F("minimum_quantity"))
    return qs


def _paid_sale_items_since(days: int):
    since = timezone.now() - timedelta(days=days)
    return SaleItem.objects.filter(
        product_id=OuterRef("pk"),
        sale__status=Sale.Status.PAID,
        sale__paid_at__gte=since,
    )


def _apply_not_sold_within(qs, days: int):
    return qs.exclude(Exists(_paid_sale_items_since(days)))


def _apply_sold_within(qs, days: int):
    return qs.filter(Exists(_paid_sale_items_since(days)))


def apply_product_list_filters(qs, query_params):
    """Применяет query-параметры модалки фильтров склада к queryset Product."""
    kinds = _parse_kind_list(query_params)
    if kinds:
        qs = qs.filter(kind__in=kinds)

    category_id = (query_params.get("category") or "").strip()
    if category_id:
        qs = qs.filter(category_id=category_id)

    brand_id = (query_params.get("brand") or "").strip()
    if brand_id:
        qs = qs.filter(brand_id=brand_id)

    is_weight = _parse_bool(query_params.get("is_weight"))
    if is_weight is not None:
        qs = qs.filter(is_weight=is_weight)

    preset = _normalize_preset(query_params.get("preset"))
    if preset:
        qs = _apply_preset(qs, preset)

    price_type = (query_params.get("price_type") or "").strip().lower()
    price_condition = (query_params.get("price_condition") or "eq").strip().lower()
    price_value = _parse_decimal(query_params.get("price_value"))
    if price_value is not None and price_value != 0:
        field = _price_field(price_type)
        if field and price_condition in ("gt", "lt", "eq"):
            qs = _compare_field(qs, field, price_condition, price_value)

    stock_type = (query_params.get("stock_type") or "").strip().lower()
    stock_condition = (query_params.get("stock_condition") or "eq").strip().lower()
    stock_value = _parse_decimal(query_params.get("stock_value"))
    if stock_type == "total" and stock_value is not None and stock_value != 0:
        if stock_condition in ("gt", "lt", "eq"):
            qs = _compare_field(qs, "quantity", stock_condition, stock_value)

    shelf_cond = (query_params.get("shelf_life_condition") or "").strip().lower()
    shelf_days = _parse_int(query_params.get("shelf_life_value"))
    today = timezone.localdate()
    if shelf_cond == "expires_within" and shelf_days is not None and shelf_days >= 0:
        end = today + timedelta(days=shelf_days)
        qs = qs.filter(
            expiration_date__isnull=False,
            expiration_date__gte=today,
            expiration_date__lte=end,
        )
    elif shelf_cond == "expired":
        qs = qs.filter(expiration_date__isnull=False, expiration_date__lt=today)

    changes_cond = (query_params.get("changes_condition") or "eq").strip().lower()
    changes_days = _parse_int(query_params.get("changes_value"))
    if changes_days is not None and changes_days > 0:
        threshold = timezone.now() - timedelta(days=changes_days)
        if changes_cond == "gt":
            qs = qs.filter(updated_at__lt=threshold)
        elif changes_cond == "lt":
            qs = qs.filter(updated_at__gt=threshold)
        else:
            qs = qs.filter(updated_at__gte=threshold)

    sell_cond = (query_params.get("sellability_condition") or "").strip().lower()
    sell_days = _parse_int(query_params.get("sellability_value"))
    if sell_days is not None and sell_days > 0:
        if sell_cond == "sold_within":
            qs = _apply_sold_within(qs, sell_days)
        elif sell_cond == "not_sold_within":
            qs = _apply_not_sold_within(qs, sell_days)

    return qs
