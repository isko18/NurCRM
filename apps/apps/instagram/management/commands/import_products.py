# yourapp/management/commands/import_products.py
import sys
from typing import Optional, Dict, Iterable, List

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.timezone import now

from apps.main.models import Product, Client, GlobalProduct
from apps.users.models import User, Company, Branch


COLUMN_MAP: Dict[str, Optional[str]] = {
    "Наименование": "name",
    "Штрихкод": "barcode",
    "Владелец": None,
    "Вес (нетто)": None,
    "Весовой": None,
    "Единица измерения": None,
    "Тип штрихкода": None,
}

REQUIRED_COLS = ["Наименование"]
CHUNK_SIZE = 800


def chunks(iterable: Iterable[str], size: int) -> Iterable[List[str]]:
    batch: List[str] = []
    for x in iterable:
        if x is None:
            continue
        batch.append(x)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class Command(BaseCommand):
    help = "Импорт товаров из XLS(X) в модель Product и GlobalProduct (upsert по barcode)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Путь к .xls/.xlsx файлу")
        parser.add_argument("--company", required=True, help="PK компании (UUID или int)")
        parser.add_argument("--branch", help="PK филиала (UUID или int)", default=None)
        parser.add_argument("--created-by", help="PK пользователя-автора (UUID или int)", default=None)
        parser.add_argument("--client", help="PK клиента (UUID или int)", default=None)
        parser.add_argument("--dry-run", action="store_true", help="Пробный запуск без сохранения")

    def handle(self, *args, **opts):
        path = opts["file"]
        company_pk = opts["company"]
        branch_pk = opts.get("branch")
        created_by_pk = opts.get("created_by")
        client_pk = opts.get("client")
        dry_run = opts["dry_run"]

        # --- проверяем FK ---
        try:
            from apps.users.models import Company, Branch, User
            company = Company.objects.get(pk=company_pk)
        except Company.DoesNotExist:
            raise CommandError(f"Company pk={company_pk} не найдена")

        branch = None
        if branch_pk:
            branch = Branch.objects.filter(pk=branch_pk).first()
            if not branch:
                raise CommandError(f"Branch pk={branch_pk} не найден")
            if branch.company_id != company.id:
                raise CommandError("Филиал принадлежит другой компании.")

        created_by = None
        if created_by_pk:
            created_by = User.objects.filter(pk=created_by_pk).first()
            if not created_by:
                raise CommandError(f"User pk={created_by_pk} не найден")

        client = None
        if client_pk:
            client = Client.objects.filter(pk=client_pk).first()
            if not client:
                raise CommandError(f"Client pk={client_pk} не найден")

        # --- читаем Excel ---
        try:
            if path.lower().endswith(".xls"):
                df = pd.read_excel(path, dtype=str, engine="xlrd")
            else:
                df = pd.read_excel(path, dtype=str, engine="openpyxl")
        except Exception as e:
            raise CommandError(f"Не удалось прочитать файл {path}: {e!r}")

        df.columns = [str(c).strip() for c in df.columns]

        for col in REQUIRED_COLS:
            if col not in df.columns:
                raise CommandError(f"В Excel нет обязательной колонки: {col}")

        used_cols = [c for c in df.columns if c in COLUMN_MAP]
        if not used_cols:
            raise CommandError("В Excel не найдено ни одной ожидаемой колонки.")

        rename_map = {c: COLUMN_MAP[c] for c in used_cols if COLUMN_MAP[c]}
        df = df[used_cols].rename(columns=rename_map)

        for c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()

        barcodes = {x for x in df.get("barcode", []) if x}

        # --- батчами загружаем уже существующие продукты ---
        existing_by_barcode = {}
        if barcodes:
            for batch in chunks(sorted(barcodes), CHUNK_SIZE):
                qs = Product.objects.filter(company=company, barcode__in=batch)
                for p in qs:
                    existing_by_barcode[p.barcode] = p

        # --- заранее кэш глобальных товаров ---
        existing_global = {}
        if barcodes:
            for batch in chunks(sorted(barcodes), CHUNK_SIZE):
                for gp in GlobalProduct.objects.filter(barcode__in=batch):
                    existing_global[gp.barcode] = gp

        created_cnt = updated_cnt = skipped_cnt = 0
        created_global = 0
        errors = []

        @transaction.atomic
        def do_import():
            nonlocal created_cnt, updated_cnt, skipped_cnt, created_global

            for idx, row in df.iterrows():
                rowno = idx + 2
                name = (row.get("name") or "").strip()
                barcode = (row.get("barcode") or "").strip() or None

                if not name:
                    skipped_cnt += 1
                    continue

                # --- GLOBAL PRODUCT ---
                global_product = None
                if barcode:
                    global_product = existing_global.get(barcode)
                    if not global_product:
                        global_product = GlobalProduct.objects.filter(barcode=barcode).first()
                        if global_product:
                            existing_global[barcode] = global_product
                if not global_product:
                    global_product = GlobalProduct(name=name, barcode=barcode)
                    global_product.save()
                    created_global += 1
                    if barcode:
                        existing_global[barcode] = global_product

                # --- LOCAL PRODUCT ---
                product = None
                if barcode:
                    product = existing_by_barcode.get(barcode)
                    if not product:
                        product = Product.objects.filter(company=company, barcode=barcode).first()

                try:
                    if product is None:
                        product = Product(
                            company=company,
                            branch=branch,
                            client=client,
                            created_by=created_by,
                            name=name,
                            barcode=barcode,
                            quantity=0,
                            purchase_price=0,
                            price=0,
                            status=Product.Status.PENDING,
                            date=now(),
                        )
                        product.full_clean()
                        product.save()
                        created_cnt += 1
                        if barcode:
                            existing_by_barcode[barcode] = product
                    else:
                        changed = False
                        if name and product.name != name:
                            product.name = name
                            changed = True
                        if branch and product.branch_id != branch.id:
                            product.branch = branch
                            changed = True
                        if client and product.client_id != client.id:
                            product.client = client
                            changed = True
                        if created_by and product.created_by_id != created_by.id:
                            product.created_by = created_by
                            changed = True
                        if changed:
                            product.full_clean()
                            product.save(update_fields=["name", "branch", "client", "created_by", "updated_at"])
                            updated_cnt += 1
                        else:
                            skipped_cnt += 1
                except Exception as e:
                    errors.append(f"Строка {rowno}: {e!r}")

            if dry_run:
                transaction.set_rollback(True)

        do_import()

        msg = (
            f"Импорт завершён. "
            f"Создано продуктов: {created_cnt}, обновлено: {updated_cnt}, пропущено: {skipped_cnt}. "
            f"Добавлено глобальных товаров: {created_global}."
        )
        if dry_run:
            msg = "[DRY-RUN] " + msg
        self.stdout.write(self.style.SUCCESS(msg))

        if errors:
            self.stdout.write(self.style.WARNING("Ошибки (первые 20):"))
            for e in errors[:20]:
                self.stdout.write(" - " + e)
            if len(errors) > 20:
                self.stdout.write(f"... ещё {len(errors)-20} ошибок")
