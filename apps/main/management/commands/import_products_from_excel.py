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
  --merge-by-name   Один товар на наименование: первый ШК — основной, остальные уходят
                    в доп. штрихкоды (main.ProductAlternateBarcode)
  --merge-ignore-price  При группировке не учитывать цену (по умолчанию строки с одним
                    наименованием, но разной ценой считаются разными товарами)
"""
import re
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.main.models import Product, ProductAlternateBarcode, Company
from apps.users.models import Branch


def _col_index(headers, names, fallback_col=None, exclude=()):
    """
    Найти индекс колонки по заголовку или вернуть fallback.
    exclude — индексы уже занятых колонок: заголовки пересекаются («Штрих код»
    содержит «код», «Цена закупки» содержит «цена»), и без этого одна и та же
    колонка утекает сразу в два поля.
    """
    if fallback_col is not None:
        return fallback_col
    headers_lower = [str(h).strip().lower() if h is not None else "" for h in headers]
    for name in names:
        for i, h in enumerate(headers_lower):
            if i in exclude or not h:
                continue
            if name in h or h in name:
                return i
    return None


def _norm_name(name):
    """Ключ группировки по наименованию: регистр и лишние пробелы не различаем."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


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
        parser.add_argument(
            "--merge-by-name",
            action="store_true",
            help="Один товар на наименование: первый ШК основной, остальные — доп. штрихкоды",
        )
        parser.add_argument(
            "--merge-ignore-price",
            action="store_true",
            help="При --merge-by-name склеивать строки с одним именем даже при разной цене",
        )

    def _extract_1c_binary_text(self, file_path):
        """Извлечь текст из бинарного дампа 1С (формат L\\x00). Возвращает rows или []."""
        def _looks_like_real_data(rows_list):
            """Проверка: данные похожи на таблицу (кириллица, цифры, баркоды)."""
            sample = " ".join(str(c) for row in rows_list[:5] for c in row[:5])
            has_cyrillic = any("\u0400" <= c <= "\u04FF" for c in sample)
            has_digits = any(c.isdigit() for c in sample)
            has_long_word = any(len(str(c)) >= 5 and str(c).isalnum() for row in rows_list[:3] for c in row[:5])
            return has_cyrillic or (has_digits and has_long_word)

        with open(file_path, "rb") as f:
            data = f.read()

        rows = []
        # 1) Пробуем UTF-16LE — часто используется в 1С
        try:
            text = data.decode("utf-16-le", errors="ignore")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for line in lines:
                cells = re.split(r"[\t;]+", line, maxsplit=15)
                cells = [c.strip() for c in cells if c.strip()]
                if len(cells) >= 2:
                    rows.append(cells)
            if len(rows) >= 2 and _looks_like_real_data(rows):
                return rows
        except Exception:
            pass

        # 2) Ищем последовательности читаемых символов (ASCII + Latin-1/UTF-8)
        rows = []
        chunks = re.findall(rb"[\x20-\x7E\xC0-\xFF]+", data)
        parts = []
        for c in chunks:
            try:
                s = c.decode("utf-8", errors="ignore").strip()
                if len(s) >= 2 and any(x.isalnum() for x in s):
                    parts.append(s)
            except Exception:
                pass
        if len(parts) >= 4:
            ncols = 5
            for i in range(0, min(len(parts), 1000), ncols):
                row = parts[i : i + ncols]
                if len(row) >= 2:
                    rows.append(row)
            if len(rows) >= 2 and _looks_like_real_data(rows):
                return rows

        return []

    def _load_excel_rows(self, file_path):
        """Загружает строки из Excel (.xlsx или .xls). Возвращает (rows, wb_to_close) или (None, None) при ошибке."""
        import os
        file_path = os.path.abspath(os.path.normpath(file_path))

        # 1. Пробуем pandas (надёжно читает .xlsx, лучше обрабатывает пути)
        try:
            import pandas as pd
            df = pd.read_excel(file_path, header=None, engine="openpyxl")
            rows = [["" if (pd.isna(c) or c is None) else c for c in row] for row in df.values.tolist()]
            return (rows, None)
        except ImportError:
            pass
        except Exception as e:
            err_msg = str(e).lower()
            if "zip" in err_msg or "not a zip" in err_msg:
                pass
            else:
                self.stderr.write(self.style.ERROR(f"Ошибка pandas/openpyxl: {e}"))
                return (None, None)

        # 2. Пробуем openpyxl напрямую
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            ws = wb.active
            if ws is None:
                self.stderr.write(self.style.ERROR("Нет активного листа"))
                return (None, None)
            rows = [list(cell if cell is not None else "" for cell in row) for row in ws.iter_rows(values_only=True)]
            return (rows, wb)
        except Exception as e:
            err_msg = str(e).lower()
            if "zip" in err_msg or "not a zip" in err_msg:
                pass
            else:
                self.stderr.write(self.style.ERROR(f"Ошибка openpyxl: {e}"))
                return (None, None)

        # 3. Пробуем xlrd (для .xls)
        try:
            import xlrd
            wb = xlrd.open_workbook(file_path)
            sh = wb.sheet_by_index(0)
            rows = [list(sh.row_values(r)) for r in range(sh.nrows)]
            return (rows, None)
        except ImportError:
            pass
        except Exception:
            pass  # не .xls — пробуем CSV

        # 4. Пробуем как CSV/TSV (только если файл похож на текст)
        try:
            with open(file_path, "rb") as f:
                sample = f.read(512)
            # Не бинарный: нет нулевых байтов, или это UTF-8 текст
            null_count = sample.count(b"\x00")
            if null_count > len(sample) // 4:  # много нулей = бинарный
                raise ValueError("binary")
            # Или начинается с нечитаемого — пропускаем CSV
            if sample[:2] in (b"PK", b"\xd0\xcf", b"L\x00") or sample[:4] == b"\x00\x00\x00\x00":
                raise ValueError("binary")
        except (ValueError, IOError):
            pass
        else:
            try:
                import csv
                for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
                    for delim in ("\t", ",", ";"):
                        try:
                            with open(file_path, "r", encoding=encoding) as f:
                                reader = csv.reader(f, delimiter=delim)
                                rows = list(reader)
                            if rows and len(rows[0]) > 1:
                                # Проверка: первая ячейка похожа на текст (не бинарный мусор)
                                first = str(rows[0][0])[:50]
                                if first.isprintable() or any(c.isalnum() for c in first):
                                    return (rows, None)
                        except (UnicodeDecodeError, csv.Error):
                            continue
            except Exception:
                pass

        # 5. Пробуем извлечь текст из бинарного дампа 1С (формат L\x00)
        try:
            with open(file_path, "rb") as f:
                head = f.read(8)
            if head[:2] == b"L\x00" or head[:4] == b"L\x00\x00\x00":
                rows = self._extract_1c_binary_text(file_path)
                if rows:
                    return (rows, None)
        except Exception:
            pass

        # Подсказка по формату файла
        try:
            with open(file_path, "rb") as f:
                head = f.read(8)
            if head[:2] == b"PK":
                hint = "Файл похож на .xlsx (zip), но повреждён."
            elif head[:4] == b"\xd0\xcf\x11\xe0":
                hint = "Файл похож на старый .xls (OLE)."
            elif head[:2] == b"L\x00" or head[:4] == b"L\x00\x00\x00":
                hint = "Файл экспортирован из 1С в собственном формате."
            else:
                hint = "Файл имеет нестандартный формат."
        except Exception:
            hint = ""

        self.stderr.write(
            self.style.ERROR(
                f"Не удалось прочитать файл. {hint}\n"
                "Решение: 1) В 1С — выгрузите в «Табличный документ» и сохраните как .xlsx; "
                "2) Либо откройте файл в Excel/LibreOffice и сохраните как .xlsx — затем повторите импорт."
            )
        )
        return (None, None)

    def handle(self, *args, **options):
        file_path = options["file"]
        company_id = options["company"].strip()
        branch_id = (options["branch"] or "").strip() or None
        header_row = options["header_row"]
        dry_run = options["dry_run"]
        skip_duplicates = options["skip_duplicates"]
        default_qty = Decimal(str(options.get("default_qty", 50)))
        merge_by_name = options.get("merge_by_name", False)
        merge_ignore_price = options.get("merge_ignore_price", False)

        rows, wb_to_close = self._load_excel_rows(file_path)
        if rows is None:
            return

        try:
            if wb_to_close:
                wb_to_close.close()
        except Exception:
            pass

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
        used = {c for c in (barcode_col, name_col) if c is not None}

        price_col = _col_index(
            headers,
            ["price", "цена", "цена продажи"],
            options.get("price_col"),
            exclude=used,
        )
        used |= {c for c in (price_col,) if c is not None}

        purchase_price_col = _col_index(
            headers,
            # Основы слов, а не полные формы: в выгрузках встречается и
            # «Себестоимость», и «Закупочная», и «Цена закупки».
            ["purchase_price", "себестоим", "закуп"],
            options.get("purchase_price_col"),
            exclude=used,
        )
        used |= {c for c in (purchase_price_col,) if c is not None}

        article_col = _col_index(
            headers,
            ["article", "артикул", "код"],
            options.get("article_col"),
            exclude=used,
        )
        used |= {c for c in (article_col,) if c is not None}

        quantity_col = _col_index(
            headers,
            ["quantity", "количество", "остаток", "qty"],
            options.get("quantity_col"),
            exclude=used,
        )
        used |= {c for c in (quantity_col,) if c is not None}

        unit_col = _col_index(
            headers,
            ["unit", "единица", "ед. изм", "ед"],
            options.get("unit_col"),
            exclude=used,
        )

        if barcode_col is None:
            self.stderr.write(
                self.style.ERROR(
                    "Не найдена колонка штрихкода. Укажите --barcode-col <индекс> (0-based)."
                )
            )
            self.stdout.write("Заголовки (1-я строка): " + str(headers[:10]))
            if len(rows) > header_row + 1:
                sample = rows[header_row + 1]
                if isinstance(sample, (list, tuple)):
                    self.stdout.write("Пример данных (2-я строка): " + str(list(sample)[:10]))
            self.stdout.write(
                "Пример: --barcode-col 0 --name-col 1 --article-col 2 --price-col 3"
            )
            return

        self.stdout.write(
            f"Колонки: barcode={barcode_col}, name={name_col}, article={article_col}, "
            f"price={price_col}, purchase_price={purchase_price_col}, "
            f"quantity={quantity_col}, unit={unit_col}"
        )

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

        # Занятые в компании штрихкоды: и основные, и дополнительные — товар ищем по обоим.
        owner_by_barcode = {}
        for bc, pid in Product.objects.filter(company=company).exclude(
            barcode__in=(None, "")
        ).values_list("barcode", "id"):
            owner_by_barcode[bc] = pid
        for bc, pid in ProductAlternateBarcode.objects.filter(company=company).values_list(
            "barcode", "product_id"
        ):
            owner_by_barcode.setdefault(bc, pid)

        skipped_no_barcode = 0
        seen_in_file = {}      # штрихкод -> ключ группы, куда он уже попал
        groups = []            # порядок как в файле
        by_key = {}

        for row_idx, row in enumerate(data_rows):
            row_num = header_row + 2 + row_idx
            barcode = _val(row, barcode_col)
            if not barcode:
                skipped_no_barcode += 1
                continue

            barcode = str(barcode).strip()
            name = _val(row, name_col) if name_col is not None else barcode
            if not name:
                name = f"Товар {barcode}"

            price = _decimal_val(row, price_col) if price_col is not None else Decimal("0")

            if merge_by_name:
                key = (_norm_name(name),) if merge_ignore_price else (_norm_name(name), str(price))
            else:
                key = ("row", row_idx)

            if barcode in seen_in_file:
                # Один и тот же ШК в файле дважды — вторую строку игнорируем.
                continue
            seen_in_file[barcode] = key

            group = by_key.get(key)
            if group is None:
                unit = _val(row, unit_col) if unit_col is not None else "шт."
                group = {
                    "row_num": row_num,
                    "name": name,
                    "article": _val(row, article_col) if article_col is not None else "",
                    "price": price,
                    "purchase_price": (
                        _decimal_val(row, purchase_price_col)
                        if purchase_price_col is not None
                        else price
                    ),
                    "quantity": (
                        _decimal_val(row, quantity_col, default_qty)
                        if quantity_col is not None
                        else default_qty
                    ),
                    "unit": unit or "шт.",
                    "barcodes": [],
                }
                by_key[key] = group
                groups.append(group)
            elif quantity_col is not None:
                # Остатки одного товара, разнесённые по строкам, складываем.
                group["quantity"] += _decimal_val(row, quantity_col, Decimal("0"))

            group["barcodes"].append(barcode)

        merged_groups = sum(1 for g in groups if len(g["barcodes"]) > 1)
        if merge_by_name:
            self.stdout.write(
                f"Группировка по наименованию{'' if merge_ignore_price else ' + цене'}: "
                f"{len(groups)} товаров, из них {merged_groups} с несколькими штрихкодами"
            )

        created = 0
        alt_created = 0
        skipped_duplicate = 0
        conflicts = []
        errors = []

        for group in groups:
            codes = group["barcodes"]
            name = group["name"]
            row_num = group["row_num"]

            # Уже есть товар с одним из этих кодов?
            product_id = next((owner_by_barcode[b] for b in codes if b in owner_by_barcode), None)

            if product_id is not None and skip_duplicates:
                skipped_duplicate += 1
                extra = [b for b in codes if b not in owner_by_barcode]
                if extra:
                    if dry_run:
                        self.stdout.write(f"  [dry-run] +доп.ШК к существующему {name[:40]}: {', '.join(extra)}")
                        alt_created += len(extra)
                        for b in extra:
                            owner_by_barcode[b] = product_id
                    else:
                        try:
                            with transaction.atomic():
                                ProductAlternateBarcode.objects.bulk_create(
                                    [
                                        ProductAlternateBarcode(
                                            product_id=product_id, company=company, barcode=b
                                        )
                                        for b in extra
                                    ]
                                )
                            alt_created += len(extra)
                            for b in extra:
                                owner_by_barcode[b] = product_id
                        except Exception as e:
                            errors.append((row_num, ", ".join(extra), str(e)))
                continue

            main_bc = codes[0]
            alt_bcs = [b for b in codes[1:] if b not in owner_by_barcode]
            taken = [b for b in codes[1:] if b in owner_by_barcode]
            if taken:
                conflicts.append((row_num, name, taken))

            if dry_run:
                suffix = f" | доп.ШК: {', '.join(alt_bcs)}" if alt_bcs else ""
                self.stdout.write(f"  [dry-run] {main_bc} | {name[:40]} | {group['price']}{suffix}")
                created += 1
                alt_created += len(alt_bcs)
                for b in codes:
                    owner_by_barcode[b] = "dry-run"
                continue

            price = group["price"]
            purchase_price = group["purchase_price"]
            article = group["article"]
            unit = group["unit"]

            try:
                with transaction.atomic():
                    product = Product.objects.create(
                        company=company,
                        branch=branch,
                        name=name[:255],
                        barcode=main_bc,
                        article=article[:64] if article else "",
                        price=price,
                        purchase_price=purchase_price,
                        markup_percent=Decimal("0") if purchase_price == 0 else ((price - purchase_price) / purchase_price * 100).quantize(Decimal("0.01")),
                        quantity=group["quantity"],
                        unit=unit[:32] if unit else "шт.",
                        kind=Product.Kind.PRODUCT,
                    )
                    if alt_bcs:
                        ProductAlternateBarcode.objects.bulk_create(
                            [
                                ProductAlternateBarcode(product=product, company=company, barcode=b)
                                for b in alt_bcs
                            ]
                        )
                created += 1
                alt_created += len(alt_bcs)
                for b in [main_bc] + alt_bcs:
                    owner_by_barcode[b] = product.id
                if created % 100 == 0:
                    self.stdout.write(f"  Импортировано: {created}")
            except Exception as e:
                errors.append((row_num, main_bc, str(e)))

        self.stdout.write(self.style.SUCCESS(f"\nИмпорт завершён."))
        self.stdout.write(f"  Создано товаров: {created}")
        self.stdout.write(f"  Доп. штрихкодов: {alt_created}")
        self.stdout.write(f"  Пропущено (нет штрихкода): {skipped_no_barcode}")
        self.stdout.write(f"  Пропущено (дубликат): {skipped_duplicate}")
        if conflicts:
            self.stderr.write(
                self.style.WARNING(f"  ШК заняты другими товарами (не добавлены): {len(conflicts)}")
            )
            for rn, nm, bcs in conflicts[:10]:
                self.stderr.write(f"    Строка {rn}, {nm[:40]}: {', '.join(bcs)}")
            if len(conflicts) > 10:
                self.stderr.write(f"    ... и ещё {len(conflicts) - 10}")
        if errors:
            self.stderr.write(self.style.WARNING(f"  Ошибок: {len(errors)}"))
            for rn, bc, err in errors[:10]:
                self.stderr.write(f"    Строка {rn}, {bc}: {err}")
            if len(errors) > 10:
                self.stderr.write(f"    ... и ещё {len(errors) - 10}")
