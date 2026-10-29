"""
Нормализация телефонов клиентов (E.164, по умолчанию Кыргызстан +996).

Client.phone — свободный текст с кассы/сайта ("0555 12-34-56", "+996 (555) 123 456",
"996555123456"). Для поиска одного и того же человека (касса, приложение клиентов, QR)
храним Client.phone_normalized = normalize_phone_e164(phone).
"""
import re

_NON_DIGITS = re.compile(r"\D+")
KG_CODE = "996"


def phone_digits(value) -> str:
    return _NON_DIGITS.sub("", str(value or ""))


def normalize_phone_e164(value) -> str:
    """
    "+996555123456" для любых записей кыргызского номера; для иностранных — "+<цифры>".
    Пустая строка, если номер не распознан (слишком короткий/длинный).
    """
    raw = str(value or "").strip()
    digits = phone_digits(raw)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(KG_CODE) and len(digits) == 12:
        return f"+{digits}"
    if len(digits) == 10 and digits[0] in ("0", "8"):
        # 0555123456 / 8555123456 — национальный формат КР
        return f"+{KG_CODE}{digits[1:]}"
    if len(digits) == 9:
        return f"+{KG_CODE}{digits}"
    if len(digits) == 11 and digits[0] == "8":
        # 8 XXX XXX XX XX — Россия/Казахстан
        return f"+7{digits[1:]}"
    if 11 <= len(digits) <= 15:
        return f"+{digits}"
    return ""


def phone_search_suffix(value) -> str:
    """Последние 9 цифр — абонентский номер КР (для поиска по части номера)."""
    digits = phone_digits(value)
    if len(digits) >= 9:
        return digits[-9:]
    # часть номера в национальном формате: "0555 12" → "55512"
    return digits[1:] if digits.startswith("0") else digits


def looks_like_phone(term) -> bool:
    term = str(term or "").strip()
    if not term or not re.fullmatch(r"[\d\s()+\-.]+", term):
        return False
    return len(phone_digits(term)) >= 6
