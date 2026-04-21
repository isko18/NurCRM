"""
Шапка чека: ИНН, наименование (бренд/организация), адрес — из eKassa (теги fields), иначе из CRM.
Теги по спецификации eKassa (см. docs/Интеграция_1.14): 1018 ИНН, 1048 наименование, 1187 место расчётов, 1009 адрес.
"""


def _str_clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s


def receipt_vendor_header(sale):
    """
    Возвращает dict: inn, brand, address — приоритет значений из sale.ekassa_fiscal['fields'],
    иначе поля Company у продажи.
    """
    company = getattr(sale, "company", None)
    crm_inn = _str_clean(getattr(company, "inn", None) if company else None)
    crm_brand = _str_clean(
        (getattr(company, "llc", None) or getattr(company, "name", None)) if company else None
    )
    crm_address = _str_clean(getattr(company, "address", None) if company else None)

    fields = {}
    try:
        meta = getattr(sale, "ekassa_fiscal", None) or {}
        raw = meta.get("fields")
        if isinstance(raw, dict):
            fields = raw
    except Exception:
        fields = {}

    def tag(key: str) -> str:
        return _str_clean(fields.get(str(key)))

    ek_inn = tag("1018")
    ek_brand = tag("1048") or tag("1187")
    ek_addr = tag("1009")

    return {
        "inn": ek_inn or crm_inn,
        "brand": ek_brand or crm_brand,
        "address": ek_addr or crm_address,
    }
