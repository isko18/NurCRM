# apps/cafe/services/fiscal.py
"""
Подготовка тела фискального чека из заказа кафе.

Бэкенд не отправляет запрос в коннектор сам — он лишь строит payload, который
фронт отправит на POST {connector}/driver/cash-register/receipt. Здесь же
маппинг способа оплаты кафе -> операции/суммы фискального чека.
"""
from decimal import Decimal, ROUND_HALF_UP

from ..models import Order, OrderItem

TWO = Decimal("0.01")


def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


# Способы оплаты кафе, которые считаются «наличными» для разнесения сумм чека.
_CASH_METHODS = {Order.PaymentMethod.CASH}


def _cash_cashless_split(order: Order, final_amount: Decimal):
    """
    Возвращает (totalCashSum, totalCashlessSum) для фискального чека.

    Для split-оплаты берём фактические части из checkout_payments,
    иначе — по способу оплаты заказа целиком.
    """
    final_amount = _q2(final_amount)

    if order.payment_method == Order.PaymentMethod.SPLIT:
        cash = Decimal("0")
        for part in order.checkout_payments.all():
            if part.payment_method in _CASH_METHODS:
                cash += Decimal(str(part.amount or 0))
        cash = _q2(cash)
        if cash > final_amount:
            cash = final_amount
        return cash, _q2(final_amount - cash)

    if order.payment_method in _CASH_METHODS:
        return final_amount, Decimal("0")
    # card / transfer / прочее безналичное
    return Decimal("0"), final_amount


def _first_not_none(*values, default=None):
    for v in values:
        if v is not None:
            return v
    return default


def build_receipt_payload(
    order: Order,
    settings_obj,
    *,
    operation_type: str = "INCOME",
    cash_received=None,
    charge_amount=None,
    origin_fd_number=None,
    origin_fn_serial_number=None,
):
    """
    Строит тело запроса для POST /driver/cash-register/receipt из заказа.

    order            — заказ кафе (с предзагруженными items/menu_item желательно).
    settings_obj     — CafeFiscalSettings компании (источник ставок/реквизитов по умолчанию).
    operation_type   — INCOME | INCOME_RETURN | EXPENDITURE | EXPENDITURE_RETURN.
    cash_received    — фактически принятые наличные (для расчёта сдачи), опционально.
    charge_amount    — фискализируемая сумма (для долга/частичной оплаты paySum=факт);
                       по умолчанию = итог заказа за вычетом скидки.
    origin_*         — реквизиты чека-основания (для возвратов).

    Налоговые коды позиций берутся с MenuItem, при отсутствии — дефолты компании.
    """
    order.recalc_total()
    total = _q2(order.total_amount)
    discount = _q2(order.discount_amount)
    final_amount = _q2(total - discount)
    if final_amount < 0:
        final_amount = Decimal("0")

    # Сумма, на которую формируется чек (по умолчанию весь итог; для долга — меньше).
    target = _q2(charge_amount) if charge_amount is not None else final_amount
    if target < 0:
        target = Decimal("0")

    # Коэффициент, чтобы сумма позиций совпала с фискализируемой суммой (скидка/частичная оплата).
    ratio = (target / total) if total > 0 else Decimal("1")

    default_vat = int(getattr(settings_obj, "default_vat_code", 0) or 0)
    default_st = int(getattr(settings_obj, "default_st_code", 0) or 0)
    default_attr = int(getattr(settings_obj, "default_calc_item_attr_code", 1) or 1)
    default_measure = getattr(settings_obj, "default_measure", "шт") or "шт"

    positions = []
    running = Decimal("0")
    items = [it for it in order.items.all() if not it.is_rejected]
    for idx, it in enumerate(items):
        mi = it.menu_item if it.menu_item_id else None
        if it.line_kind == OrderItem.LineKind.SERVICE:
            name = it.service_title or "Услуга"
            unit = it.unit_price or Decimal("0")
            measure = default_measure
        else:
            name = mi.title if mi else "Товар"
            unit = it.unit_price if it.unit_price is not None else (mi.price if mi else Decimal("0"))
            measure = _first_not_none(
                (getattr(mi, "fiscal_measure", "") or None) if mi else None,
                it.menu_item_sale_unit if it.menu_item_is_sold_by_weight else None,
                default_measure,
            )

        vat = int(_first_not_none(getattr(mi, "fiscal_vat_code", None) if mi else None, default_vat))
        st = int(_first_not_none(getattr(mi, "fiscal_st_code", None) if mi else None, default_st))
        attr = int(_first_not_none(getattr(mi, "fiscal_calc_item_attr_code", None) if mi else None, default_attr))
        sgtin = (getattr(mi, "fiscal_sgtin", "") if mi else "") or None

        qty = Decimal(str(it.quantity or 0))
        price = _q2(Decimal(str(unit)) * ratio)

        # Последняя позиция добирает копеечную разницу округления до target.
        if idx == len(items) - 1:
            cost = _q2(target - running)
        else:
            cost = _q2(price * qty)
            running += cost

        positions.append({
            "calcItemAttributeCode": attr,
            "sgtin": sgtin,
            "name": name,
            "price": float(price),
            "quantity": float(qty),
            "cost": float(cost),
            "measure": measure,
            "vat": vat,
            "st": st,
        })

    pay_sum = _q2(cash_received) if cash_received is not None else target
    delivery_sum = _q2(pay_sum - target)
    if delivery_sum < 0:
        delivery_sum = Decimal("0")

    cash_sum, cashless_sum = _cash_cashless_split(order, target)

    payload = {
        "operationType": operation_type,
        "paySum": float(pay_sum),
        "deliverySum": float(delivery_sum),
        "totalSum": float(target),
        "totalCashSum": float(cash_sum),
        "totalCashlessSum": float(cashless_sum),
        "positions": positions,
    }
    if origin_fd_number is not None:
        payload["originFdNumber"] = int(origin_fd_number)
    if origin_fn_serial_number:
        payload["originFnSerialNumber"] = str(origin_fn_serial_number)

    return payload
