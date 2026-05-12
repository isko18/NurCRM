# apps/main/api/utils.py
from django.utils.timezone import localtime
from decimal import Decimal

def _to_float(x):
    try:
        if isinstance(x, Decimal):
            return float(x)
        return float(x or 0)
    except Exception:
        return 0.0

def _pick(*vals, default=None):
    for v in vals:
        if v is not None:
            return v
    return default

def build_receipt_payload(sale, cashier_name=None, *, ensure_number: bool = True):
    """
    Формирует JSON для печати чека.

    - Всегда возвращает корректный doc_no.
      Если ensure_number=True, сначала присваивает сквозной номер (ensure_sale_doc_number).
    - Все денежные/числовые поля нормализуются в float.
    - Строки — в Unicode (JSON отдается UTF-8).
    - Добавлен флаг 'encoding': 'utf-8' для фронта.
    """
    # 1) гарантируем номер чека
    doc_no = str(getattr(sale, "doc_no", "") or "")
    if ensure_number:
        try:
            # скорректируй импорт под свой проект, если путь другой
            from apps.main.utils_numbers import ensure_sale_doc_number
            doc_no = str(ensure_sale_doc_number(sale))
        except Exception:
            # fallback: id продажи
            doc_no = doc_no or str(sale.id)
    else:
        doc_no = doc_no or str(sale.id)

    # 2) позиции
    items = []
    for it in sale.items.all():
        name = _pick(
            getattr(it, "name_snapshot", None),
            getattr(it, "name", None),
            getattr(getattr(it, "product", None), "name", None),
            default="Товар"
        )
        qty = _to_float(_pick(
            getattr(it, "quantity", None),
            getattr(it, "qty", None),
            getattr(it, "count", None),
            default=1
        ))
        price = _to_float(_pick(
            getattr(it, "unit_price", None),
            getattr(it, "price", None),
            default=0
        ))
        line_disc = _to_float(getattr(it, "line_discount", 0))
        line_total = qty * price - line_disc
        items.append(
            {
                "name": str(name),
                "qty": qty,
                "price": price,
                "line_discount": line_disc,
                "line_total": line_total,
            }
        )

    # 3) шапка/итоги
    created_at = getattr(sale, "created_at", None)
    from apps.main.receipt_header import receipt_vendor_header

    vh = receipt_vendor_header(sale)
    company_name = vh.get("brand") or ""

    payload = {
        # метка кодировки для фронта (браузерный клиент сможет выбрать UTF-8)
        "encoding": "utf-8",

        "doc_no": doc_no,
        "company": company_name,
        "inn": vh.get("inn") or None,
        "address": vh.get("address") or None,
        "created_at": localtime(created_at).strftime("%Y-%m-%d %H:%M:%S") if created_at else None,
        "cashier_name": cashier_name,

        "items": items,

        "discount": _to_float(
            getattr(sale, "discount", getattr(sale, "discount_total", 0))
        ),
        "tax": _to_float(
            getattr(sale, "tax", getattr(sale, "tax_total", 0))
        ),
        "paid_cash": _to_float(getattr(sale, "paid_cash", 0)),
        "paid_card": _to_float(getattr(sale, "paid_card", 0)),
        "change": _to_float(getattr(sale, "change", 0)),
    }

    # eKassa: отдаём то, что уже сохранено в Sale.ekassa_fiscal (если есть)
    try:
        ekassa_meta = getattr(sale, "ekassa_fiscal", None)
    except Exception:
        ekassa_meta = None
    if ekassa_meta:
        payload["ekassa"] = ekassa_meta

    return payload
