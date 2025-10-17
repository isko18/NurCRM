# apps/main/services_agent_pos.py
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Sum
from apps.main.models import (
    Sale, SaleItem, Product, ManufactureSubreal, AgentSaleAllocation
)
from django.db import models


class AgentNotEnoughStock(Exception):
    pass


def model_has_field(model, field_name: str) -> bool:
    try:
        return any(f.name == field_name for f in model._meta.get_fields())
    except Exception:
        return False


@transaction.atomic
def checkout_agent_cart(cart, *, department=None, agent=None):
    """
    Чекаут корзины от лица АГЕНТА (acting_agent).
    Создаёт Sale/SaleItem, затем распределяет списание по передачам агента (FIFO)
    через AgentSaleAllocation. Склад Product.quantity НЕ трогаем.

    ВАЖНО:
    - Лочим передачи агента select_for_update() (без GROUP BY) => нет гонок по остаткам.
    - Аллокации пишем get_or_create с обработкой IntegrityError =>
      устойчиво к повторам/параллельным вызовам при наличии уникального индекса (sale_item, subreal).
    """
    company = cart.company
    operator = cart.user
    acting_agent = agent or cart.user
    branch  = getattr(cart, "branch", None)

    # --- 1) агрегируем потребности корзины ---
    needs = {}
    for it in cart.items.select_related("product"):
        if it.product is None:
            key = f"custom:{it.custom_name}:{it.unit_price}"
            row = needs.setdefault(key, {"custom_name": it.custom_name, "unit_price": it.unit_price, "qty": 0})
            row["qty"] += int(it.quantity or 0)
            continue

        pid = str(it.product_id)
        row = needs.setdefault(pid, {"product": it.product, "unit_price": it.unit_price, "qty": 0})
        row["qty"] += int(it.quantity or 0)

    # --- 2) готовим FIFO остатки агента по продуктам ---
    product_ids = [v["product"].id for k, v in needs.items() if not k.startswith("custom:")]

    if product_ids:
        # ЛОЧИМ передачи (без аннотаций) — это безопасно: нет GROUP BY
        subreals = (
            ManufactureSubreal.objects
            .select_for_update()
            .filter(agent_id=acting_agent.id, product_id__in=product_ids, company=company)
            .select_related("product")
            .order_by("product_id", "created_at", "id")
        )

        # Сколько уже продано по каждой передаче (без блокировки и без FOR UPDATE)
        sold_map = (
            AgentSaleAllocation.objects
            .filter(agent=acting_agent, company=company, product_id__in=product_ids)
            .values("subreal_id").annotate(s=Sum("qty"))
        )
        sold_by_subreal = {r["subreal_id"]: int(r["s"] or 0) for r in sold_map}
    else:
        subreals = []
        sold_by_subreal = {}

    # Собираем очереди FIFO: product_id -> [(subreal, free_qty), ...]
    fifo = {}
    for s in subreals:
        free = max(0, int(s.qty_accepted or 0) - int(s.qty_returned or 0) - int(sold_by_subreal.get(s.id, 0)))
        if free > 0:
            fifo.setdefault(str(s.product_id), []).append([s, free])

    # --- 3) проверяем достаточность остатков агента ---
    for k, v in needs.items():
        if k.startswith("custom:"):
            continue
        pid = str(v["product"].id)
        need = int(v["qty"] or 0)
        have = sum(q for _, q in fifo.get(pid, []))
        if need > have:
            raise AgentNotEnoughStock(
                f"Недостаточно у агента: «{v['product'].name}». Нужно {need}, доступно {have}."
            )

    # --- 4) создаём Sale (только существующие поля) ---
    create_kwargs = dict(
        company=company,
        user=operator,
        status=Sale.Status.PAID,         # или ваша бизнес-логика
        subtotal=Decimal("0.00"),
        discount_total=cart.order_discount_total or Decimal("0.00"),
        tax_total=Decimal("0.00"),
        total=Decimal("0.00"),
    )
    if model_has_field(Sale, "branch"):
        create_kwargs["branch"] = branch
    if model_has_field(Sale, "client") and getattr(cart, "client", None) is not None:
        create_kwargs["client"] = cart.client
    if model_has_field(Sale, "department") and department is not None:
        create_kwargs["department"] = department

    sale = Sale.objects.create(**create_kwargs)

    subtotal = Decimal("0.00")

    # --- 5) переносим позиции и делаем FIFO-аллокации ---
    for k, v in needs.items():
        if k.startswith("custom:"):
            qty = int(v["qty"])
            price = v["unit_price"] or Decimal("0.00")
            SaleItem.objects.create(
                sale=sale, product=None,
                name_snapshot=v["custom_name"], barcode_snapshot=None,
                unit_price=price, quantity=qty
            )
            subtotal += (price or Decimal("0.00")) * qty
            continue

        product = v["product"]
        qty = int(v["qty"])
        price = v["unit_price"] or product.price or Decimal("0.00")

        sitem = SaleItem.objects.create(
            sale=sale, product=product,
            name_snapshot=product.name, barcode_snapshot=product.barcode,
            unit_price=price, quantity=qty
        )
        subtotal += (price or Decimal("0.00")) * qty

        # FIFO по заблокированной очереди
        left = qty
        queue = fifo.get(str(product.id), [])
        while left > 0 and queue:
            subr, free = queue[0]
            take = min(left, free)

            # антиидупликационный upsert: уникальный индекс (sale_item, subreal)
            try:
                alloc, created = AgentSaleAllocation.objects.get_or_create(
                    company=company,
                    agent=acting_agent,
                    subreal=subr,
                    sale=sale,
                    sale_item=sitem,
                    product=product,
                    defaults={"qty": take},
                )
                if not created:
                    # редкий случай, если попали сюда повторно — просто инкрементим
                    AgentSaleAllocation.objects.filter(pk=alloc.pk).update(qty=models.F("qty") + take)
            except IntegrityError:
                # гонка на уникальном индексе — перечитываем и инкрементим
                alloc = AgentSaleAllocation.objects.get(
                    company=company,
                    agent=acting_agent,
                    subreal=subr,
                    sale=sale,
                    sale_item=sitem,
                    product=product,
                )
                AgentSaleAllocation.objects.filter(pk=alloc.pk).update(qty=models.F("qty") + take)

            free -= take
            left -= take
            if free == 0:
                queue.pop(0)
            else:
                queue[0][1] = free

    # --- 6) итоги как в обычной продаже ---
    sale.subtotal = subtotal
    total = subtotal - (cart.order_discount_total or Decimal("0.00"))
    sale.total = total if total > 0 else Decimal("0.00")
    sale.save(update_fields=["subtotal", "discount_total", "total"])

    # --- 7) закрываем корзину (лучше делать под внешним select_for_update(cart)) ---
    cart.status = cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])

    return sale
