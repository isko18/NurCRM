from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

import logging

from apps.main.models import Cart, CartItem, Sale, SaleItem, Product
from apps.main.pos_utils import cart_item_stock_consume_units, money as pos_money


class NotEnoughStock(Exception):
    pass


@transaction.atomic
def checkout_cart(cart: Cart, department=None, allow_negative_stock: bool = False) -> Sale:
    cart.recalc()

    items = list(
        cart.items.select_related("product", "sale_package")
    )
    if not items:
        raise ValueError("Корзина пуста.")

    shift = getattr(cart, "shift", None)
    if not shift:
        raise ValueError("Нет смены. Сначала открой смену или передай кассу и создай смену.")

    cashbox = shift.cashbox
    branch = shift.branch

    prod_ids = [it.product_id for it in items if it.product_id]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    consume_by_pid: dict = defaultdict(lambda: Decimal("0"))
    for it in items:
        if not it.product_id:
            continue
        if it.product_id not in products:
            raise ValueError("Товар позиции не найден.")
        try:
            item_consume = cart_item_stock_consume_units(it)
        except ValueError as e:
            raise ValueError(str(e)) from e
        consume_by_pid[it.product_id] += item_consume

    if not allow_negative_stock:
        for pid, need in consume_by_pid.items():
            p = products[pid]
            have = Decimal(str(p.quantity or 0))
            if need > have:
                raise NotEnoughStock(
                    f"Недостаточно остатка для «{getattr(p, 'name', '') or p.id}». "
                    f"Требуется {need} (в учётных единицах склада), доступно {have}."
                )

    sale = Sale.objects.create(
        company=cart.company,
        branch=branch,
        user=shift.cashier,
        shift=shift,
        cashbox=cashbox,
        status=Sale.Status.NEW,
        subtotal=cart.subtotal,
        discount_total=cart.discount_total,
        tax_total=cart.tax_total,
        total=cart.total,
        created_at=timezone.now(),
    )

    sale_items = []
    for it in items:
        p = it.product
        name_snap = (getattr(p, "name", None) or getattr(it, "custom_name", None) or "Позиция")
        barcode_snap = getattr(p, "barcode", None) or ""
        qty = Decimal(str(it.quantity or 1))
        line_disc = Decimal(str(getattr(it, "line_discount", None) or 0))
        effective_unit = (it.unit_price or Decimal("0.00")) - (line_disc / qty) if qty else (it.unit_price or Decimal("0.00"))
        pp = (p.purchase_price or Decimal("0.00")) if p else Decimal("0.00")
        if it.sale_package_id:
            ipp = Decimal(str(it.sale_package.quantity_in_package or 0))
            snap = pos_money(pp / ipp) if ipp > 0 else pos_money(pp)
        else:
            snap = pos_money(pp)
        sale_items.append(
            SaleItem(
                company=cart.company,
                branch=branch,
                sale=sale,
                product=p,
                name_snapshot=name_snap,
                barcode_snapshot=barcode_snap,
                unit_price=effective_unit,
                quantity=it.quantity,
                sale_package_id=it.sale_package_id,
                purchase_price_snapshot=snap,
            )
        )
    SaleItem.objects.bulk_create(sale_items)

    changed = []
    for pid, qty_need in consume_by_pid.items():
        p = products[pid]
        p.quantity = Decimal(str(p.quantity or 0)) - qty_need
        changed.append(p)
    if changed:
        Product.objects.bulk_update(changed, ["quantity"])
        changed_ids = [p.id for p in changed if getattr(p, "id", None)]

        def _send_webhooks():
            from apps.main.services.webhooks import send_product_webhook

            for pid in changed_ids:
                try:
                    prod = Product.objects.get(pk=pid)
                    send_product_webhook(prod, "product.updated")
                except Exception:
                    logging.getLogger("crm.webhooks").error(
                        "Failed to send product.updated webhook after checkout. product_id=%s",
                        pid,
                        exc_info=True,
                    )

        try:
            transaction.on_commit(_send_webhooks)
        except Exception:
            _send_webhooks()

    CartItem.objects.filter(cart=cart).delete()
    cart.status = Cart.Status.CHECKED_OUT
    cart.save(update_fields=["status", "updated_at"])

    return sale


def _parse_kind(raw, Product):
    kind_raw = str(raw or Product.Kind.PRODUCT).strip().lower()
    kind_map = {
        "product": Product.Kind.PRODUCT,
        "товар": Product.Kind.PRODUCT,
        "service": Product.Kind.SERVICE,
        "услуга": Product.Kind.SERVICE,
        "bundle": Product.Kind.BUNDLE,
        "комплект": Product.Kind.BUNDLE,
    }
    return kind_map.get(kind_raw, Product.Kind.PRODUCT)


def _parse_bool_like(raw):
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("1", "true", "yes", "да", "kg", "кг", "weight", "вес")


def _parse_decimal(value, field_name):
    try:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value))
    except Exception:
        raise ValueError(field_name)


def _parse_int_nonneg(value, field_name):
    try:
        if value in (None, ""):
            return 0
        v = int(value)
        if v < 0:
            raise ValueError
        return v
    except Exception:
        raise ValueError(field_name)


def _parse_date_to_aware_datetime(raw_value):
    """
    Принимает YYYY-MM-DD или ISO datetime.
    Возвращает timezone-aware datetime.
    """
    if raw_value in (None, ""):
        return None

    if isinstance(raw_value, timezone.datetime):
        dt = raw_value
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    s = str(raw_value).strip()
    dt = parse_datetime(s)
    if dt:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    d = parse_date(s)
    if d:
        dt = timezone.datetime(d.year, d.month, d.day, 0, 0, 0)
        return timezone.make_aware(dt)

    raise ValueError("date")
