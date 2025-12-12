# shop/services.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.main.models import Cart, CartItem, Sale, SaleItem, Product

class NotEnoughStock(Exception):
    pass

@transaction.atomic
def checkout_cart(cart: Cart, department=None) -> Sale:
    """
    Оформляет корзину в продажу.
    Услуги (product=None) допускаются; склад трогаем только для product!=None.
    """
    # пересчитать на всякий случай
    cart.recalc()

    items = list(cart.items.select_related("product"))
    if not items:
        raise ValueError("Корзина пуста.")

    # блокируем реальные товары для корректной проверки/списания
    prod_ids = [it.product_id for it in items if it.product_id]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    # проверка остатков только по товарным строкам
    for it in items:
        if not it.product_id:
            continue
        qty_need = int(it.quantity or 0)
        if qty_need <= 0:
            continue
        p = products.get(it.product_id)
        if p is None:
            raise ValueError("Товар позиции не найден.")
        if (p.quantity or 0) < qty_need:
            raise NotEnoughStock(f"Недостаточно на складе: «{p.name}». Нужно {qty_need}, доступно {p.quantity}.")

    # создаём продажу по агрегатам корзины
    sale = Sale.objects.create(
        company=cart.company,
        user=cart.user,
        status=Sale.Status.NEW,
        subtotal=cart.subtotal,
        discount_total=cart.discount_total,
        tax_total=cart.tax_total,
        total=cart.total,
        created_at=timezone.now(),
    )

    # переносим строки корзины в продажу (делаем снимки на дату продажи)
    sale_items = []
    for it in items:
        p = it.product  # может быть None
        name_snap = (getattr(p, "name", None) or getattr(it, "custom_name", None) or "Позиция")
        barcode_snap = getattr(p, "barcode", None) or ""
        sale_items.append(SaleItem(
            company=cart.company,
            sale=sale,
            product=p,  # допускается None
            name_snapshot=name_snap,
            barcode_snapshot=barcode_snap,
            unit_price=it.unit_price or Decimal("0.00"),
            quantity=int(it.quantity or 0),
        ))
    SaleItem.objects.bulk_create(sale_items)

    # списываем склад только по товарам
    changed = []
    for it in items:
        if not it.product_id:
            continue
        qty_need = int(it.quantity or 0)
        if qty_need <= 0:
            continue
        p = products[it.product_id]
        new_qty = int(p.quantity or 0) - qty_need
        if new_qty < 0:
            # дополнительная защита от гонки
            raise NotEnoughStock(f"Недостаточно на складе при списании: «{p.name}».")
        p.quantity = new_qty
        changed.append(p)
    if changed:
        Product.objects.bulk_update(changed, ["quantity"])

    # чистим корзину и закрываем её
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
