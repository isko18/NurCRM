"""
Импорт товаров из Excel в main.Product.
Строки без штрихкода пропускаются.

Использование:
  python manage.py import_products_from_excel Новый4.xlsx --company <UUID компании>

Опции:
  --company    UUID компании (обязательно)
  --branch     UUID филиала (опционально)
  --header-row Строка с заголовками (по умолчанию 0 = первая)
  --barcode-col Индекс колонки штрихкода (0-based). Если не указан — ищем по заголовку "barcode"/"штрих"/"штрихкод"
  --name-col   Индекс колонки названия
  --article-col Индекс колонки артикула
  --price-col  Индекс колонки цены
  --dry-run    Только показать, что будет импортировано, без записи в БД
  --skip-duplicates Пропускать если товар с таким штрихкодом уже есть (по умолчанию True)
  --default-qty Количество по умолчанию для каждого товара (по умолчанию 50)
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.main.models import Product, Company
from apps.users.models import Branch


def _col_index(headers, names, fallback_col=None):
    """Найти индекс колонки по заголовку или вернуть fallback."""
    if fallback_col is not None:
        return fallback_col
    headers_lower = [str(h).strip().lower() if h is not None else "" for h in headers]
    for name in names:
        for i, h in enumerate(headers_lower):
            if name in h or h in name:
                return i
    return None


def _val(row, col, default=""):
    if col is None or col >= len(row):
        return default
    v = row[col]
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _decimal_val(row, col, default=Decimal("0")):
    v = _val(row, col)
    if not v:
        return default
    try:
        return Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        return default


class Command(BaseCommand):
    help = "Импорт товаров из Excel в main.Product. Строки без штрихкода пропускаются."

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Путь к файлу Excel (.xlsx)")
        parser.add_argument("--company", type=str, required=True, help="UUID компании")
        parser.add_argument("--branch", type=str, default="", help="UUID филиала (опционально)")
        parser.add_argument("--header-row", type=int, default=0, help="Номер строки с заголовками (0-based)")
        parser.add_argument("--barcode-col", type=int, default=None, help="Индекс колонки штрихкода (0-based)")
        parser.add_argument("--name-col", type=int, default=None, help="Индекс колонки названия")
        parser.add_argument("--article-col", type=int, default=None, help="Индекс колонки артикула")
        parser.add_argument("--price-col", type=int, default=None, help="Индекс колонки цены")
        parser.add_argument("--purchase-price-col", type=int, default=None, help="Индекс колонки закупочной цены")
        parser.add_argument("--quantity-col", type=int, default=None, help="Индекс колонки количества")
        parser.add_argument("--unit-col", type=int, default=None, help="Индекс колонки единицы измерения")
        parser.add_argument("--dry-run", action="store_true", help="Не записывать в БД, только показать")
        parser.add_argument("--skip-duplicates", action="store_true", default=True, help="Пропускать дубликаты по штрихкоду")
        parser.add_argument("--no-skip-duplicates", action="store_false", dest="skip_duplicates", help="Не пропускать дубликаты")
        parser.add_argument("--default-qty", type=float, default=50, help="Количество по умолчанию для каждого товара (по умолчанию 50)")

    def handle(self, *args, **options):
        import openpyxl

        file_path = options["file"]
        company_id = options["company"].strip()
        branch_id = (options["branch"] or "").strip() or None
        header_row = options["header_row"]
        dry_run = options["dry_run"]
        skip_duplicates = options["skip_duplicates"]
        default_qty = Decimal(str(options.get("default_qty", 50)))

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Ошибка открытия файла: {e}"))
            return

        ws = wb.active
        if ws is None:
            self.stderr.write(self.style.ERROR("Нет активного листа"))
            return

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            self.stderr.write(self.style.ERROR("Файл пустой"))
            return

        headers = rows[header_row]
        data_rows = rows[header_row + 1 :]

        barcode_col = _col_index(
            headers,
            ["barcode", "штрих", "штрихкод", "штрих-код", "ean", "barcode"],
            options.get("barcode_col"),
        )
        name_col = _col_index(
            headers,
            ["name", "название", "наименование", "товар"],
            options.get("name_col"),
        )
        article_col = _col_index(
            headers,
            ["article", "артикул", "код"],
            options.get("article_col"),
        )
        price_col = _col_index(
            headers,
            ["price", "цена", "цена продажи"],
            options.get("price_col"),
        )
        purchase_price_col = _col_index(
            headers,
            ["purchase_price", "закупка", "цена закупки", "себестоимость"],
            options.get("purchase_price_col"),
        )
        quantity_col = _col_index(
            headers,
            ["quantity", "количество", "остаток", "qty"],
            options.get("quantity_col"),
        )
        unit_col = _col_index(
            headers,
            ["unit", "единица", "ед. изм", "ед"],
            options.get("unit_col"),
        )

        if barcode_col is None:
            self.stderr.write(
                self.style.ERROR(
                    "Не найдена колонка штрихкода. Укажите --barcode-col <индекс> или "
                    'добавьте заголовок "barcode"/"штрихкод"'
                )
            )
            self.stdout.write(f"Заголовки: {headers}")
            return

        self.stdout.write(f"Колонки: barcode={barcode_col}, name={name_col}, article={article_col}, price={price_col}")

        try:
            company = Company.objects.get(id=company_id)
        except Company.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Компания не найдена: {company_id}"))
            return

        branch = None
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, company=company)
            except Branch.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"Филиал не найден: {branch_id}, импорт без филиала"))

        existing_barcodes = set(
            Product.objects.filter(company=company).exclude(barcode__in=(None, "")).values_list("barcode", flat=True)
        )

        created = 0
        skipped_no_barcode = 0
        skipped_duplicate = 0
        errors = []

        for row_idx, row in enumerate(data_rows):
            row_num = header_row + 2 + row_idx
            barcode = _val(row, barcode_col)
            if not barcode:
                skipped_no_barcode += 1
                continue

            barcode = str(barcode).strip()
            if skip_duplicates and barcode in existing_barcodes:
                skipped_duplicate += 1
                continue

            name = _val(row, name_col) if name_col is not None else barcode
            if not name:
                name = f"Товар {barcode}"

            article = _val(row, article_col) if article_col is not None else ""
            price = _decimal_val(row, price_col) if price_col is not None else Decimal("0")
            purchase_price = _decimal_val(row, purchase_price_col) if purchase_price_col is not None else price
            quantity = default_qty
            unit = _val(row, unit_col) if unit_col is not None else "шт."
            if not unit:
                unit = "шт."

            if dry_run:
                self.stdout.write(f"  [dry-run] {barcode} | {name[:40]} | {price}")
                created += 1
                continue

            try:
                with transaction.atomic():
                    Product.objects.create(
                        company=company,
                        branch=branch,
                        name=name[:255],
                        barcode=barcode,
                        article=article[:64] if article else "",
                        price=price,
                        purchase_price=purchase_price,
                        markup_percent=Decimal("0") if purchase_price == 0 else ((price - purchase_price) / purchase_price * 100).quantize(Decimal("0.01")),
                        quantity=quantity,
                        unit=unit[:32] if unit else "шт.",
                        kind=Product.Kind.PRODUCT,
                    )
                created += 1
                existing_barcodes.add(barcode)
                if created % 100 == 0:
                    self.stdout.write(f"  Импортировано: {created}")
            except Exception as e:
                errors.append((row_num, barcode, str(e)))

        wb.close()

        self.stdout.write(self.style.SUCCESS(f"\nИмпорт завершён."))
        self.stdout.write(f"  Создано: {created}")
        self.stdout.write(f"  Пропущено (нет штрихкода): {skipped_no_barcode}")
        self.stdout.write(f"  Пропущено (дубликат): {skipped_duplicate}")
        if errors:
            self.stderr.write(self.style.WARNING(f"  Ошибок: {len(errors)}"))
            for rn, bc, err in errors[:10]:
                self.stderr.write(f"    Строка {rn}, {bc}: {err}")
            if len(errors) > 10:
                self.stderr.write(f"    ... и ещё {len(errors) - 10}")
