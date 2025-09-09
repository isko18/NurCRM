# shop/services.py
from django.db import transaction
from django.db.models import F
from .models import Cart, Sale, SaleItem
from apps.construction.models import Department, Cashbox, CashFlow


class NotEnoughStock(Exception):
    """Ошибка нехватки остатков при оформлении продажи."""
    pass


# def _resolve_department_for_sale(cart: Cart, department: Department | None) -> Department | None:
#     """
#     Определяем отдел для движения по кассе:
#     1) Явно переданный department
#     2) Отдел компании, где числится пользователь cart.user
#     3) Первый отдел компании (если нужен такой fallback)
#     """
#     if department:
#         return department
#     if cart.user:
#         dept = Department.objects.filter(company=cart.company, employees=cart.user).first()
#         if dept:
#             return dept
#     return Department.objects.filter(company=cart.company).first()


@transaction.atomic
def checkout_cart(cart: Cart, department: Department | None = None) -> Sale:
    items_qs = cart.items.select_for_update(of=("self",)).select_related("product")
    if not items_qs.exists():
        raise ValueError("Корзина пуста")

    for ci in items_qs:
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
    for ci in items_qs:
        sale_items.append(SaleItem(
            sale=sale,
            product=ci.product,
            name_snapshot=ci.product.name,
            barcode_snapshot=ci.product.barcode,
            unit_price=ci.unit_price,
            quantity=ci.quantity,
            company=sale.company,
        ))
        ci.product.quantity = F("quantity") - ci.quantity
        ci.product.save(update_fields=["quantity"])

    SaleItem.objects.bulk_create(sale_items)

    cart.status = Cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])
    cart.items.all().delete()

    return sale
