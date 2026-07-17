"""
Экспорт весовых товаров компании в .xls под PLU-менеджер весов Rongta RLS1100.

Колонки и дефолты соответствуют таблице PLU-менеджера весов (18 полей).
Импорт в весы — позиционный (по порядку колонок).

Ключевое: PLU (Product.plu) пишется в поля кода («LF код» и «Код»), поэтому
штрихкод, который печатают весы, совпадает с номером, который касса читает из
штрихкода (positions 2..7) и по которому ищет товар. Товарам без plu номер
присваивается здесь же и сохраняется.
"""
from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, connection, transaction

try:
    import xlwt
except ImportError:  # pragma: no cover - библиотека объявлена в requirements
    xlwt = None

from apps.main.models import Product


class XlwtNotInstalled(RuntimeError):
    """xlwt не установлен (объявлен в requirements: xlwt==1.3.0)."""


# Заголовки — порядок и состав как в PLU-менеджере весов Rongta RLS.
XLS_COLUMNS = [
    "Порядковая клавиша",   # 1  hotkey (порядковый)
    "Название",             # 2  имя товара (латиница на весах; ≤ ~36 симв.)
    "LF код",               # 3  = PLU
    "Код",                  # 4  = PLU (в него весы кодируют штрихкод)
    "Тип штрихкода",        # 5  номер шаблона штрихкода на весах
    "Цена единицы",         # 6  цена
    "Ед. изм.",             # 7  единица (Kg для весовых)
    "Кол-во дней",          # 8  количество дней
    "Отдел",                # 9  департамент (участвует в префиксе штрихкода)
    "Вес PT",               # 10 вес упаковки
    "Срок годности",        # 11 срок годности
    "Тип упаковки",         # 12 тип упаковки
    "Тара",                 # 13 тара
    "Скидка(%)",            # 14 скидка
    "Сообщение 1",          # 15 info 1
    "Сообщение 2",          # 16 info 2
    "Этикетка",             # 17 номер этикетки
    "Таблица скидок",       # 18 таблица скидок
]

# Дефолты — как в присланной таблице PLU-менеджера.
DEFAULT_BARCODE_TYPE = 5
DEFAULT_UNIT = "Kg"
DEFAULT_DAYS = 0
DEFAULT_DEPARTMENT = 21
DEFAULT_PACK_WEIGHT = "0.000"
DEFAULT_SHELF_LIFE_DAYS = 15
DEFAULT_PACKING_TYPE = "Нормальный"
DEFAULT_TARE = "0.000"
DEFAULT_DISCOUNT = 0
DEFAULT_INFO1 = 0
DEFAULT_INFO2 = 0
DEFAULT_LABEL_NUMBER = 0
DEFAULT_DISCOUNT_TABLE = 0

# Латиница для имени на весах (многие RLS печатают только ASCII). Транслит опционален.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def _translit(name: str) -> str:
    out = []
    for ch in name or "":
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.upper() if ch.isupper() else t)
        else:
            out.append(ch)
    return "".join(out)


def _pg_advisory_lock_company(company_id):
    """Сериализуем выдачу PLU в рамках компании (как в send_products_to_scale)."""
    if connection.vendor != "postgresql" or not company_id:
        return
    key = int(str(company_id).replace("-", "")[:16], 16) & 0x7FFFFFFFFFFFFFFF
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint);", [key])


def _price_number(price) -> float:
    if price is None:
        return 0.0
    try:
        return float(Decimal(str(price)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return 0.0


@transaction.atomic
def _ensure_plu_for_weight_products(company_id, products: list[Product]) -> None:
    """Присваивает plu весовым товарам без него и сохраняет."""
    missing = [p for p in products if p.plu is None]
    if not missing:
        return

    _pg_advisory_lock_company(company_id)

    used = set(
        Product.objects
        .filter(company_id=company_id, plu__isnull=False)
        .values_list("plu", flat=True)
    )
    next_plu = (max(used) if used else 0) + 1

    for p in missing:
        while next_plu in used:
            next_plu += 1
        plu_value = next_plu
        p.plu = plu_value
        while True:
            try:
                p.save(update_fields=["plu"])
                break
            except IntegrityError:
                used.add(plu_value)
                plu_value += 1
                while plu_value in used:
                    plu_value += 1
                p.plu = plu_value
        used.add(plu_value)
        next_plu = plu_value + 1


def _txp_line(plu, name, price, *, barcode_type, unit_code, department, shelf_life_days):
    """Одна строка .TXP (24 поля, TAB-разделитель) — как в экспорте PLU-менеджера весов."""
    code = 1000 + (int(plu) - 1) * 10
    price_x100 = int((Decimal(str(price or 0)) * 100).quantize(Decimal("1")))
    fields = [
        str(int(plu)),          # 0  PLU
        name,                   # 1  название (латиница)
        str(int(plu)),          # 2  LF код = PLU
        str(code),              # 3  Код = 1000 + (plu-1)*10
        str(barcode_type),      # 4  тип штрихкода
        str(price_x100),        # 5  цена ×100
        str(unit_code),         # 6  единица (код; 4 = kg)
        str(department),        # 7  отдел
        " 0,000",               # 8  вес PT
        str(shelf_life_days),   # 9  срок годности
        "0",                    # 10
        " 0,000",               # 11 тара
        "0", "0", "0", "0", "0", "0", "0",  # 12–18
        "",                     # 19 (пустое поле)
        "0", "0", "0",          # 20–22
        "0,0",                  # 23
    ]
    return "\t".join(fields)


def build_weight_products_txp(
    company,
    *,
    product_ids=None,
    barcode_type: int = DEFAULT_BARCODE_TYPE,
    unit_code: int = 4,
    department: int = DEFAULT_DEPARTMENT,
    shelf_life_days: int = DEFAULT_SHELF_LIFE_DAYS,
    translit_name: bool = True,
    assign_plu: bool = True,
) -> tuple[bytes, int]:
    """
    Собирает .TXP (TAB-разделитель, CRLF, без заголовка) — формат импорта PLU-менеджера
    весов Rongta RLS. Возвращает (bytes, count). Пустой набор → (b"", 0).

    Кодировка cp1251 с заменой непредставимых символов на '?', как делает само ПО
    (поэтому названия лучше слать латиницей — translit_name=True).
    """
    qs = Product.objects.filter(company=company, is_weight=True)
    if product_ids:
        qs = qs.filter(id__in=product_ids)
    products = list(qs.order_by("plu", "name", "id"))
    if not products:
        return b"", 0

    if assign_plu:
        _ensure_plu_for_weight_products(company.id, products)
        products.sort(key=lambda p: (p.plu if p.plu is not None else 0, p.name or ""))

    lines = []
    for idx, product in enumerate(products):
        plu = int(product.plu) if product.plu is not None else (idx + 1)
        name = _translit(product.name) if translit_name else (product.name or "")
        lines.append(_txp_line(
            plu, name, product.price,
            barcode_type=barcode_type,
            unit_code=unit_code,
            department=department,
            shelf_life_days=shelf_life_days,
        ))

    text = "\r\n".join(lines) + "\r\n"
    return text.encode("cp1251", errors="replace"), len(products)


def build_weight_products_xls(
    company,
    *,
    product_ids=None,
    barcode_type: int = DEFAULT_BARCODE_TYPE,
    department: int = DEFAULT_DEPARTMENT,
    shelf_life_days: int = DEFAULT_SHELF_LIFE_DAYS,
    tare=DEFAULT_TARE,
    label_number: int = DEFAULT_LABEL_NUMBER,
    unit: str = DEFAULT_UNIT,
    packing_type: str = DEFAULT_PACKING_TYPE,
    translit_name: bool = True,
    assign_plu: bool = True,
    include_header: bool = True,
) -> tuple[bytes, int]:
    """
    Собирает .xls весовых товаров компании под PLU-менеджер весов.
    Возвращает (bytes, count). Пустой набор → (b"", 0).

    include_header=False — без строки заголовков (данные с первой строки). Нужно,
    если импорт ПО весов позиционный и трактует первую строку как данные.
    """
    if xlwt is None:
        raise XlwtNotInstalled("Библиотека xlwt не установлена: pip install xlwt==1.3.0")

    qs = Product.objects.filter(company=company, is_weight=True)
    if product_ids:
        qs = qs.filter(id__in=product_ids)
    products = list(qs.order_by("plu", "name", "id"))
    if not products:
        return b"", 0

    if assign_plu:
        _ensure_plu_for_weight_products(company.id, products)
        products.sort(key=lambda p: (p.plu if p.plu is not None else 0, p.name or ""))

    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("PLU")

    row_offset = 0
    if include_header:
        header_style = xlwt.easyxf("font: bold on;")
        for col_idx, title in enumerate(XLS_COLUMNS):
            sheet.write(0, col_idx, title, header_style)
        row_offset = 1

    for idx, product in enumerate(products):
        row_idx = idx + row_offset
        hotkey = idx + 1               # порядковый 1..N, не зависит от строки листа
        plu = int(product.plu) if product.plu is not None else hotkey
        name = _translit(product.name) if translit_name else (product.name or "")
        row_data = [
            hotkey,                        # 1 Порядковая клавиша
            name,                          # 2 Название
            plu,                           # 3 LF код = PLU
            plu,                           # 4 Код = PLU
            barcode_type,                  # 5 Тип штрихкода
            _price_number(product.price),  # 6 Цена единицы
            unit,                          # 7 Ед. изм.
            DEFAULT_DAYS,                  # 8 Кол-во дней
            department,                    # 9 Отдел
            DEFAULT_PACK_WEIGHT,           # 10 Вес PT
            shelf_life_days,               # 11 Срок годности
            packing_type,                  # 12 Тип упаковки
            tare,                          # 13 Тара
            DEFAULT_DISCOUNT,              # 14 Скидка(%)
            DEFAULT_INFO1,                 # 15 Сообщение 1
            DEFAULT_INFO2,                 # 16 Сообщение 2
            label_number,                  # 17 Этикетка
            DEFAULT_DISCOUNT_TABLE,        # 18 Таблица скидок
        ]
        for col_idx, cell in enumerate(row_data):
            sheet.write(row_idx, col_idx, cell)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), len(products)
