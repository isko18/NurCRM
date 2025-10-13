# apps/main/services_agent_pos.py
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from apps.main.models import (
    Sale, SaleItem, Product, ManufactureSubreal, AgentSaleAllocation
)

class AgentNotEnoughStock(Exception):
    pass

@transaction.atomic
def checkout_agent_cart(cart, *, department=None):
    """
    Чекаут корзины агента.
    Создаёт Sale/SaleItem как обычная продажа, но списание идёт по партиям агента (ManufactureSubreal)
    через AgentSaleAllocation. Склад Product.quantity не трогаем.
    """
    company = cart.company
    user    = cart.user
    branch  = getattr(cart, "branch", None)

    # 1) агрегируем потребности
    needs = {}
    for it in cart.items.select_related("product"):
        if it.product is None:
            key = f"custom:{it.custom_name}:{it.unit_price}"
            needs.setdefault(key, {"custom_name": it.custom_name, "unit_price": it.unit_price, "qty": 0})
            needs[key]["qty"] += it.quantity or 0
            continue
        pid = str(it.product_id)
        needs.setdefault(pid, {"product": it.product, "unit_price": it.unit_price, "qty": 0})
        needs[pid]["qty"] += it.quantity or 0

    # 2) подготовим FIFO остатки агента
    product_ids = [v["product"].id for k, v in needs.items() if not k.startswith("custom:")]

    subreals = (
        ManufactureSubreal.objects
        .filter(agent_id=user.id, product_id__in=product_ids, company=company)
        .select_related("product")
        .order_by("product_id", "created_at", "id")
    )
    sold_map = (
        AgentSaleAllocation.objects
        .filter(agent=user, company=company, product_id__in=product_ids)
        .values("subreal_id").annotate(s=Sum("qty"))
    )
    sold_by_subreal = {r["subreal_id"]: int(r["s"] or 0) for r in sold_map}

    fifo = {}
    for s in subreals:
        free = max(0, int(s.qty_accepted or 0) - int(s.qty_returned or 0) - int(sold_by_subreal.get(s.id, 0)))
        if free > 0:
            fifo.setdefault(str(s.product_id), []).append([s, free])

    # 3) проверка достаточности
    for k, v in needs.items():
        if k.startswith("custom:"):
            continue
        pid = str(v["product"].id)
        need = int(v["qty"] or 0)
        have = sum(q for _, q in fifo.get(pid, []))
        if need > have:
            raise AgentNotEnoughStock(f"Недостаточно у агента: «{v['product'].name}». Нужно {need}, доступно {have}.")

    # 4) создаём Sale как обычно
    sale = Sale.objects.create(
        company=company,
        branch=branch if hasattr(Sale, "branch") else None,
        user=user,
        status=Sale.Status.PAID,  # или ваша логика статуса
        client=getattr(cart, "client", None) if hasattr(cart, "client") else None,
        department=department if hasattr(Sale, "department") else None,
        subtotal=Decimal("0.00"),
        discount_total=cart.order_discount_total or Decimal("0.00"),
        tax_total=Decimal("0.00"),
        total=Decimal("0.00"),
    )

    allocations = []
    subtotal = Decimal("0.00")

    # 5) перенос позиций + FIFO-распределение по партиям агента
    for k, v in needs.items():
        if k.startswith("custom:"):
            qty = int(v["qty"])
            price = v["unit_price"]
            SaleItem.objects.create(
                sale=sale, product=None,
                name_snapshot=v["custom_name"], barcode_snapshot=None,
                unit_price=price, quantity=qty
            )
            subtotal += (price or 0) * qty
            continue

        product = v["product"]
        qty = int(v["qty"])
        price = v["unit_price"] or product.price

        sitem = SaleItem.objects.create(
            sale=sale, product=product,
            name_snapshot=product.name, barcode_snapshot=product.barcode,
            unit_price=price, quantity=qty
        )
        subtotal += (price or 0) * qty

        left = qty
        queue = fifo[str(product.id)]
        while left > 0 and queue:
            subr, free = queue[0]
            take = min(left, free)
            allocations.append(AgentSaleAllocation(
                company=company, agent=user, subreal=subr,
                sale=sale, sale_item=sitem, product=product, qty=take
            ))
            free -= take
            left -= take
            if free == 0:
                queue.pop(0)
            else:
                queue[0][1] = free

    if allocations:
        AgentSaleAllocation.objects.bulk_create(allocations)

    # 6) итоги как в обычной продаже
    sale.subtotal = subtotal
    total = subtotal - (cart.order_discount_total or Decimal("0.00"))
    sale.total = total if total > 0 else Decimal("0.00")
    sale.save(update_fields=["subtotal", "discount_total", "total"])

    # 7) закрыть корзину
    cart.status = cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])

    return sale
