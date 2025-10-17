# # apps/main/services_agent_pos.py
# from decimal import Decimal
# from django.db import transaction
# from django.db.models import Sum
# from django.db import models
# from apps.main.models import (
#     Sale, SaleItem, Product, ManufactureSubreal, AgentSaleAllocation
# )

# class AgentNotEnoughStock(Exception):
#     pass


# def model_has_field(model, field_name: str) -> bool:
#     """
#     Возвращает True, если у модели есть поле с именем field_name.
#     Безопасно для вызова на любом Django model class.
#     """
#     try:
#         return any(f.name == field_name for f in model._meta.get_fields())
#     except Exception:
#         return False


# @transaction.atomic
# def checkout_agent_cart(cart, *, department=None, agent=None):
#     """
#     Чекаут корзины с продажей от лица АГЕНТА (может быть отличен от cart.user).
#     Создаёт Sale/SaleItem как обычная продажа, а списание идёт по партиям агента (ManufactureSubreal)
#     через AgentSaleAllocation. Склад Product.quantity НЕ трогаем.

#     :param cart:     корзина
#     :param department: опционально, подразделение для продажи
#     :param agent:    пользователь-агент, у которого списываем (если None — берём cart.user)
#     """
#     company = cart.company
#     operator = cart.user                # кто оформляет (кассир / владелец)
#     acting_agent = agent or cart.user   # у кого списываем “на руках”
#     branch  = getattr(cart, "branch", None)

#     # 1) агрегируем потребности (по продуктам и кастомным позициям)
#     needs = {}
#     for it in cart.items.select_related("product"):
#         if it.product is None:
#             key = f"custom:{it.custom_name}:{it.unit_price}"
#             needs.setdefault(key, {"custom_name": it.custom_name, "unit_price": it.unit_price, "qty": 0})
#             needs[key]["qty"] += int(it.quantity or 0)
#             continue

#         pid = str(it.product_id)
#         if pid not in needs:
#             needs[pid] = {"product": it.product, "unit_price": it.unit_price, "qty": 0}
#         needs[pid]["qty"] += int(it.quantity or 0)

#     # 2) подготовим FIFO остатки КОНКРЕТНОГО агента (acting_agent)
#     product_ids = [v["product"].id for k, v in needs.items() if not k.startswith("custom:")]
#     if product_ids:
#         subreals = (
#             ManufactureSubreal.objects
#             .filter(agent_id=acting_agent.id, product_id__in=product_ids, company=company)
#             .select_related("product")
#             .order_by("product_id", "created_at", "id")
#         )
#         sold_map = (
#             AgentSaleAllocation.objects
#             .filter(agent=acting_agent, company=company, product_id__in=product_ids)
#             .values("subreal_id").annotate(s=Sum("qty"))
#         )
#         sold_by_subreal = {r["subreal_id"]: int(r["s"] or 0) for r in sold_map}
#     else:
#         subreals = []
#         sold_by_subreal = {}

#     fifo = {}
#     for s in subreals:
#         # доступно по этой передаче: принято - возвращено - уже распределено в прошлых продажах
#         free = max(0, int(s.qty_accepted or 0) - int(s.qty_returned or 0) - int(sold_by_subreal.get(s.id, 0)))
#         if free > 0:
#             fifo.setdefault(str(s.product_id), []).append([s, free])

#     # 3) проверка достаточности остатков агента
#     for k, v in needs.items():
#         if k.startswith("custom:"):
#             continue
#         pid = str(v["product"].id)
#         need = int(v["qty"] or 0)
#         have = sum(q for _, q in fifo.get(pid, []))
#         if need > have:
#             raise AgentNotEnoughStock(
#                 f"Недостаточно у агента: «{v['product'].name}». Нужно {need}, доступно {have}."
#             )

#     # 4) создаём Sale — только с реально существующими полями
#     create_kwargs = dict(
#         company=company,
#         user=operator,                   # оператор, кто оформляет
#         status=Sale.Status.PAID,         # либо ваша логика статуса
#         subtotal=Decimal("0.00"),
#         discount_total=cart.order_discount_total or Decimal("0.00"),
#         tax_total=Decimal("0.00"),
#         total=Decimal("0.00"),
#     )
#     if model_has_field(Sale, "branch"):
#         create_kwargs["branch"] = branch
#     if model_has_field(Sale, "client") and getattr(cart, "client", None) is not None:
#         create_kwargs["client"] = cart.client
#     if model_has_field(Sale, "department") and department is not None:
#         create_kwargs["department"] = department

#     sale = Sale.objects.create(**create_kwargs)

#     allocations = []
#     subtotal = Decimal("0.00")

#     # 5) перенос позиций в Sale + FIFO-распределение по партиям агента
#     for k, v in needs.items():
#         if k.startswith("custom:"):
#             qty = int(v["qty"])
#             price = v["unit_price"] or Decimal("0.00")
#             SaleItem.objects.create(
#                 sale=sale, product=None,
#                 name_snapshot=v["custom_name"], barcode_snapshot=None,
#                 unit_price=price, quantity=qty
#             )
#             subtotal += (price or Decimal("0.00")) * qty
#             continue

#         product = v["product"]
#         qty = int(v["qty"])
#         price = v["unit_price"] or product.price or Decimal("0.00")

#         sitem = SaleItem.objects.create(
#             sale=sale, product=product,
#             name_snapshot=product.name, barcode_snapshot=product.barcode,
#             unit_price=price, quantity=qty
#         )
#         subtotal += (price or Decimal("0.00")) * qty

#         # FIFO
#         left = qty
#         queue = fifo.get(str(product.id), [])
#         while left > 0 and queue:
#             subr, free = queue[0]
#             take = min(left, free)

#             allocations.append(AgentSaleAllocation(
#                 company=company,
#                 agent=acting_agent,     # ← КЛЮЧЕВОЕ: списываем у acting_agent
#                 subreal=subr,
#                 sale=sale,
#                 sale_item=sitem,
#                 product=product,
#                 qty=take,
#             ))

#             free -= take
#             left -= take
#             if free == 0:
#                 queue.pop(0)
#             else:
#                 queue[0][1] = free

#     if allocations:
#         AgentSaleAllocation.objects.bulk_create(allocations)

#     # 6) итоги как в обычной продаже
#     sale.subtotal = subtotal
#     total = subtotal - (cart.order_discount_total or Decimal("0.00"))
#     sale.total = total if total > 0 else Decimal("0.00")
#     sale.save(update_fields=["subtotal", "discount_total", "total"])

#     # 7) закрыть корзину
#     cart.status = cart.Status.CHECKED_OUT
#     cart.save(update_fields=["status"])

#     return sale
