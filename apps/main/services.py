# shop/services.py
from django.db import transaction
from django.db.models import F
from .models import Cart, Sale, SaleItem
from apps.construction.models import Department, Cashbox, CashFlow

class NotEnoughStock(Exception):
    pass

@transaction.atomic
def checkout_cart(cart: Cart) -> Sale:
    """
    Переносит позиции из Cart в Sale, списывает остатки Product.quantity,
    закрывает корзину. Всё в одной транзакции.
    """
    # блокируем позиции корзины на время операции
    cart_items = cart.items.select_for_update(of=("self",)).select_related("product")

    if not cart_items.exists():
        raise ValueError("Корзина пуста")

    for ci in cart_items:
        if ci.quantity > ci.product.quantity:
            raise NotEnoughStock(f"Недостаточно остатка: {ci.product.name}")

    # создаём продажу
    sale = Sale.objects.create(
        company=cart.company,
        user=cart.user,
        subtotal=cart.subtotal,
        discount_total=cart.discount_total,
        tax_total=cart.tax_total,
        total=cart.total,
    )

    # позиции продажи + списание остатков
    sale_items = []
    for ci in cart_items:
        sale_items.append(SaleItem(
            sale=sale,
            product=ci.product,
            name_snapshot=ci.product.name,
            barcode_snapshot=ci.product.barcode,
            unit_price=ci.unit_price,
            quantity=ci.quantity,
        ))
        ci.product.quantity = F("quantity") - ci.quantity
        ci.product.save(update_fields=["quantity"])

    SaleItem.objects.bulk_create(sale_items)

    # закрываем корзину
    cart.status = cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])
    cart.items.all().delete()

    return sale


class NotEnoughStock(Exception):
    """Ошибка нехватки остатков при оформлении продажи."""
    pass


def _resolve_department_for_sale(cart: Cart, department: Department | None) -> Department | None:
    """
    Определяем отдел, к которому привяжем движение по кассе:
    1) Явно переданный department
    2) Первый отдел компании, где числится пользователь cart.user
    3) Fallback: первый отдел компании (если хочется — убери этот fallback и бросай ошибку)
    """
    if department:
        return department
    if cart.user:
        dept = Department.objects.filter(company=cart.company, employees=cart.user).first()
        if dept:
            return dept
    return Department.objects.filter(company=cart.company).first()

@transaction.atomic
def checkout_cart(cart: Cart, department: Department | None = None) -> Sale:
    items = cart.items.select_for_update(of=("self",)).select_related("product")
    if not items.exists():
        raise ValueError("Корзина пуста")

    for ci in items:
        if ci.quantity > ci.product.quantity:
            raise NotEnoughStock(f"Недостаточно остатка: {ci.product.name}")

    sale = Sale.objects.create(
        company=cart.company,
        user=cart.user,
        subtotal=cart.subtotal,
        discount_total=cart.discount_total,
        tax_total=cart.tax_total,
        total=cart.total,
    )

    sale_items: list[SaleItem] = []
    for ci in items:
        sale_items.append(SaleItem(
            sale=sale,
            product=ci.product,
            name_snapshot=ci.product.name,
            barcode_snapshot=ci.product.barcode,
            unit_price=ci.unit_price,
            quantity=ci.quantity,
        ))
        ci.product.quantity = F("quantity") - ci.quantity
        ci.product.save(update_fields=["quantity"])

    SaleItem.objects.bulk_create(sale_items)

    cart.status = Cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])
    cart.items.all().delete()

    dept = _resolve_department_for_sale(cart, department)
    if dept:
        cashbox, _ = Cashbox.objects.get_or_create(department=dept)
        product_names = ", ".join(ci.product.name for ci in items)
        CashFlow.objects.create(
            cashbox=cashbox,
            type="income",
            name=f"Продажа товара: {product_names}",
            amount=sale.total,
        )

    return sale
