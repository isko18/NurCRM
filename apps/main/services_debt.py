from __future__ import annotations

from typing import Optional

# Допустимые способы оплаты согласно debt-payment-method-backend.md
VALID_DEBT_PAYMENT_METHODS = {
    "cash",
    "mbank",
    "optima",
    "obank",
    "bakai",
    "demir",
    "other",
    "transfer",
}

DEBT_PAYMENT_METHODS = VALID_DEBT_PAYMENT_METHODS
DEBT_PAYMENT_DEFAULT = "cash"
DEBT_PAYMENT_FALLBACK = "transfer"


def normalize_debt_payment_method(raw: Optional[str]) -> str:
    """
    Нормализует payment_method согласно debt-payment-method-backend.md:
    "cash" | "mbank" | "optima" | "obank" | "bakai" | "demir" | "other" | "transfer"

    - Отсутствие / None / "" / "   " -> "cash" (по умолчанию, сохранение совместимости)
    - Допустимое значение -> точное значение
    - Неизвестное значение -> "transfer" (безнал, не падает 400 согласно вопросу D2)
    """
    if raw is None:
        return DEBT_PAYMENT_DEFAULT
    val = str(raw).strip().lower()
    if not val:
        return DEBT_PAYMENT_DEFAULT
    if val in VALID_DEBT_PAYMENT_METHODS:
        return val
    return DEBT_PAYMENT_FALLBACK
