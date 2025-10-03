# shop/services.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

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
