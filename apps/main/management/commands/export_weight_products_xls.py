"""
Выгрузка весовых товаров компании пользователя в Excel 97-2003 (.xls) для настройки весов.

Модель товара: apps.main.models.Product
Признак весового товара: Product.is_weight == True

Запуск:
  python manage.py export_weight_products_xls --email kubanychbekovularbek82@gmail.com

Опции:
  --email   Email пользователя (обязательно)
  --output  Путь к .xls (по умолчанию /tmp/weighted_products_<email>.xls)
"""
from __future__ import annotations

import re
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.main.models import Product
from apps.users.models import Company

try:
    import xlwt
except ImportError as exc:
    xlwt = None
    _XLWT_IMPORT_ERROR = exc
else:
    _XLWT_IMPORT_ERROR = None

# Заголовки колонок — строго как в ТЗ для импорта в весы
XLS_COLUMNS = [
    "Номер PLU",
    "Наименование товара",
    "Код товара",
    "Цена за единицу",
    "Тип товара",
    "Срок действия",
    "Тара",
    "Номер этикетки",
    "Префикс штрихкода",
]

WEIGHT_PRODUCT_TYPE_LABEL = "Взвешивание"
DEFAULT_SHELF_LIFE_DAYS = 90
DEFAULT_TARE = 0
DEFAULT_LABEL_NUMBER = 0
DEFAULT_BARCODE_PREFIX = 20


def _safe_email_slug(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    return re.sub(r"[^a-z0-9_]+", "_", local).strip("_") or "user"


def _default_output_path(email: str) -> Path:
    """
    По умолчанию: /tmp/weighted_products_<local>.xls (Linux/macOS).
    На Windows, если /tmp нет — каталог системного TEMP.
    """
    slug = _safe_email_slug(email)
    filename = f"weighted_products_{slug}.xls"
    tmp_unix = Path("/tmp")
    if tmp_unix.is_dir():
        return tmp_unix / filename
    return Path(tempfile.gettempdir()) / filename


def _resolve_company(user) -> Company | None:
    """
    Компания сотрудника (User.company) или компания владельца (User.owned_company).
    """
    if user.company_id:
        return user.company
    try:
        return user.owned_company
    except Company.DoesNotExist:
        return None


def _product_code(product: Product) -> str:
    """Код для весов: внутренний code, иначе штрихкод, иначе артикул."""
    for value in (product.code, product.barcode, product.article):
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _format_price(price) -> str:
    if price is None:
        return "0"
    try:
        dec = Decimal(str(price))
    except (InvalidOperation, ValueError):
        return "0"
    # Целое без копеек, если возможно; иначе два знака
    if dec == dec.to_integral_value():
        return str(int(dec))
    return f"{dec.quantize(Decimal('0.01'))}"


class Command(BaseCommand):
    help = (
        "Экспорт весовых товаров (Product.is_weight=True) компании пользователя "
        "в файл Microsoft Excel 97-2003 (.xls) через xlwt."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email пользователя (User.email), например kubanychbekovularbek82@gmail.com",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Путь к выходному .xls (по умолчанию /tmp/weighted_products_<local_part>.xls)",
        )

    def handle(self, *args, **options):
        if xlwt is None:
            raise CommandError(
                "Библиотека xlwt не установлена. Выполните: pip install xlwt"
            ) from _XLWT_IMPORT_ERROR

        email = (options["email"] or "").strip().lower()
        if not email:
            raise CommandError("Укажите --email.")

        output_path = Path(options["output"]) if options.get("output") else _default_output_path(email)

        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f"Пользователь с email «{email}» не найден.")

        company = _resolve_company(user)
        if company is None:
            raise CommandError(
                f"У пользователя «{email}» не найдена компания "
                "(нет User.company и нет owned_company)."
            )

        # Весовые товары: в проекте nurCRM признак — Product.is_weight (BooleanField)
        products = (
            Product.objects.filter(company=company, is_weight=True)
            .order_by("name", "code", "id")
        )

        count = products.count()
        if count == 0:
            raise CommandError(
                f"У компании «{company.name}» (id={company.id}) нет весовых товаров "
                f"(Product.is_weight=True). Штучные товары не выгружаются."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        workbook = xlwt.Workbook(encoding="utf-8")
        sheet = workbook.add_sheet("Товары")

        # Стиль заголовка (опционально — жирный)
        header_style = xlwt.easyxf("font: bold on;")
        for col_idx, title in enumerate(XLS_COLUMNS):
            sheet.write(0, col_idx, title, header_style)

        for row_idx, product in enumerate(products, start=1):
            plu_number = row_idx  # порядковый номер с 1, не поле Product.plu из БД
            row_data = [
                plu_number,
                product.name or "",
                _product_code(product),
                _format_price(product.price),
                WEIGHT_PRODUCT_TYPE_LABEL,
                DEFAULT_SHELF_LIFE_DAYS,
                DEFAULT_TARE,
                DEFAULT_LABEL_NUMBER,
                DEFAULT_BARCODE_PREFIX,
            ]
            for col_idx, cell in enumerate(row_data):
                sheet.write(row_idx, col_idx, cell)

        workbook.save(str(output_path))

        self.stdout.write(
            self.style.SUCCESS(
                f"Выгружено {count} весовых товаров компании «{company.name}» "
                f"пользователя {email} → {output_path}"
            )
        )
