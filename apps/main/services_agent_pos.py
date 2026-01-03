# apps/main/services_agent_pos.py
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError, models
from django.db.models import Sum
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.main.models import (
    Sale, SaleItem, ManufactureSubreal, AgentSaleAllocation
)
from apps.construction.models import Cashbox


class AgentNotEnoughStock(Exception):
    pass


class AgentCashboxError(Exception):
    pass


def model_has_field(model, field_name: str) -> bool:
    try:
        return any(f.name == field_name for f in model._meta.get_fields())
    except Exception:
        return False


def _latest_ordering(model) -> str:
    return "-created_at" if model_has_field(model, "created_at") else "-id"


def _resolve_cashbox(company, branch=None, *, cashbox_id=None):
    """
    Касса нужна только если ты хочешь сохранить cashbox в Sale/чеке/отчёте.
    Смены не трогаем вообще.
    """
    if cashbox_id:
        cb = Cashbox.objects.filter(id=cashbox_id).select_related("branch").first()
        if not cb:
            raise AgentCashboxError("Касса не найдена.")
        if cb.company_id != company.id:
            raise AgentCashboxError("Касса другой компании.")
        if (cb.branch_id or None) != (getattr(branch, "id", None) or None):
            raise AgentCashboxError("Касса другого филиала.")
        return cb

    order = _latest_ordering(Cashbox)

    if branch is not None:
        cb = (
            Cashbox.objects
            .filter(company=company, branch=branch)
            .order_by(order)
            .first()
        )
        if cb:
            return cb

    cb = (
        Cashbox.objects
        .filter(company=company, branch__isnull=True)
        .order_by(order)
        .first()
    )
    if cb:
        return cb

    raise AgentCashboxError("Нет кассы для этого филиала/компании. Создай Cashbox.")


def _to_int_qty(q) -> int:
    """
    У агента остатки (передачи/аллокейшены) обычно целые.
    Разрешаем только значения типа 1 / 2 / 3 или Decimal('2.000').
    Если придёт 0.5 или 1.250 — выдаём понятную ошибку.
    """
    if q is None:
        return 0
    try:
        d = Decimal(str(q))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"quantity": "Некорректное количество."})

    if d <= 0:
        raise ValidationError({"quantity": "Количество должно быть > 0."})

    # проверка на целое
    if d != d.to_integral_value():
        raise ValidationError({"quantity": "Для агентских продаж количество должно быть целым (штучный товар)."})
    return int(d)


@transaction.atomic
def checkout_agent_cart(
    cart,
    *,
    department=None,
    agent=None,
    cashbox_id=None,
    payment_method=None,
    cash_received=None,
):
    """
    Чекаут корзины от лица АГЕНТА.
    ✅ БЕЗ СМЕН: CashShift не используем, shift в Sale НЕ ставим.
    Можно (опционально) сохранить cashbox в Sale, если поле есть.

    Важно: Product.quantity НЕ трогаем.
    """
    company = cart.company
    operator = cart.user               # кто оформляет (оператор/кассир)
    acting_agent = agent or cart.user  # за кого списываем передачи
    branch = getattr(cart, "branch", None)

    # касса опционально (для отчёта/чека)
    cashbox = None
    if model_has_field(Sale, "cashbox"):
        cashbox = _resolve_cashbox(company, branch, cashbox_id=cashbox_id)

    # --- 1) агрегируем потребности корзины ---
    needs = {}
    for it in cart.items.select_related("product"):
        qty_int = _to_int_qty(getattr(it, "quantity", None))

        if it.product is None:
            key = f"custom:{it.custom_name}:{it.unit_price}"
            row = needs.setdefault(key, {"custom_name": it.custom_name, "unit_price": it.unit_price, "qty": 0})
            row["qty"] += qty_int
            continue

        pid = str(it.product_id)
        row = needs.setdefault(pid, {"product": it.product, "unit_price": it.unit_price, "qty": 0})
        row["qty"] += qty_int

    # --- 2) готовим FIFO остатки агента по продуктам ---
    product_ids = [v["product"].id for k, v in needs.items() if not k.startswith("custom:")]

    if product_ids:
        subreals = (
            ManufactureSubreal.objects
            .select_for_update()
            .filter(agent_id=acting_agent.id, product_id__in=product_ids, company=company)
            .select_related("product")
            .order_by("product_id", "created_at", "id")
        )

        sold_map = (
            AgentSaleAllocation.objects
            .filter(agent=acting_agent, company=company, product_id__in=product_ids)
            .values("subreal_id")
            .annotate(s=Sum("qty"))
        )
        sold_by_subreal = {r["subreal_id"]: int(r["s"] or 0) for r in sold_map}
    else:
        subreals = []
        sold_by_subreal = {}

    fifo = {}
    for s in subreals:
        free = max(
            0,
            int(s.qty_accepted or 0) - int(s.qty_returned or 0) - int(sold_by_subreal.get(s.id, 0))
        )
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

    # --- 4) создаём Sale (БЕЗ shift) ---
    create_kwargs = dict(
        company=company,
        user=operator,
        status=Sale.Status.NEW,  # дальше mark_paid()
        subtotal=Decimal("0.00"),
        discount_total=getattr(cart, "order_discount_total", None) or Decimal("0.00"),
        tax_total=Decimal("0.00"),
        total=Decimal("0.00"),
    )

    if model_has_field(Sale, "branch"):
        create_kwargs["branch"] = branch

    if model_has_field(Sale, "client") and getattr(cart, "client", None) is not None:
        create_kwargs["client"] = cart.client

    if model_has_field(Sale, "department") and department is not None:
        create_kwargs["department"] = department

    # ✅ сохраняем кассу (если нужно), но shift НЕ ставим никогда
    if model_has_field(Sale, "cashbox") and cashbox is not None:
        create_kwargs["cashbox"] = cashbox

    # ⚠️ ВАЖНО: если в модели Sale.shift null=False или Sale.clean требует shift всегда,
    # этот create упадёт. Тогда нужно сделать Sale.shift nullable или ослабить clean для agent-sales.
    if model_has_field(Sale, "shift"):
        create_kwargs["shift"] = None

    # убираем None для полей, которые могут быть not-null
    create_kwargs = {k: v for k, v in create_kwargs.items() if not (v is None and k in ("branch", "client", "department", "cashbox", "shift"))}

    sale = Sale.objects.create(**create_kwargs)

    subtotal = Decimal("0.00")

    # --- 5) переносим позиции и делаем FIFO-аллокации ---
    for k, v in needs.items():
        if k.startswith("custom:"):
            qty = int(v["qty"])
            price = v["unit_price"] or Decimal("0.00")
            SaleItem.objects.create(
                sale=sale,
                product=None,
                name_snapshot=v["custom_name"],
                barcode_snapshot=None,
                unit_price=price,
                quantity=qty,
            )
            subtotal += (price or Decimal("0.00")) * qty
            continue

        product = v["product"]
        qty = int(v["qty"])
        price = v["unit_price"] or getattr(product, "price", None) or Decimal("0.00")

        sitem = SaleItem.objects.create(
            sale=sale,
            product=product,
            name_snapshot=product.name,
            barcode_snapshot=getattr(product, "barcode", None),
            unit_price=price,
            quantity=qty,
        )
        subtotal += (price or Decimal("0.00")) * qty

        left = qty
        queue = fifo.get(str(product.id), [])
        while left > 0 and queue:
            subr, free = queue[0]
            take = min(left, free)

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
                    AgentSaleAllocation.objects.filter(pk=alloc.pk).update(qty=models.F("qty") + take)
            except IntegrityError:
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

    # --- 6) итоги + “оплата” ---
    sale.subtotal = subtotal
    total = subtotal - (getattr(cart, "order_discount_total", None) or Decimal("0.00"))
    sale.total = total if total > 0 else Decimal("0.00")
    sale.save(update_fields=["subtotal", "discount_total", "total"])

    pm = payment_method or (getattr(Sale, "PaymentMethod", None) and Sale.PaymentMethod.CASH) or "cash"
    if pm == getattr(Sale.PaymentMethod, "CASH", "cash"):
        if cash_received is None:
            cash_received = sale.total
    else:
        cash_received = Decimal("0.00")

    if hasattr(sale, "mark_paid") and callable(getattr(sale, "mark_paid")):
        sale.mark_paid(payment_method=pm, cash_received=cash_received)
    else:
        updates = []
        if model_has_field(Sale, "payment_method"):
            sale.payment_method = pm
            updates.append("payment_method")
        if model_has_field(Sale, "cash_received"):
            sale.cash_received = cash_received
            updates.append("cash_received")
        if model_has_field(Sale, "paid_at"):
            sale.paid_at = timezone.now()
            updates.append("paid_at")
        sale.status = Sale.Status.PAID
        updates.append("status")
        sale.save(update_fields=updates)

    # --- 7) закрываем корзину ---
    cart.status = cart.Status.CHECKED_OUT
    cart.save(update_fields=["status"])

    return sale
