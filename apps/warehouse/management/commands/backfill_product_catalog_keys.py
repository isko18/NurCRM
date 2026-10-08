"""
QA B01: проставить catalog_key (общий ключ копий товара в компании) существующим товарам.

Правило: товары компании с одинаковым непустым штрихкодом → один ключ; остальные —
каждый свой ключ (проставится сам при первом перемещении). Товары, у которых ключ уже
есть, не меняются. Если в группе по штрихкоду уже есть ключ — он раздаётся остальным.

  python manage.py backfill_product_catalog_keys             # отчёт (dry-run)
  python manage.py backfill_product_catalog_keys --apply
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.warehouse import models as m


class Command(BaseCommand):
    help = "Проставляет catalog_key товарам с одинаковым штрихкодом в компании. По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        qs = m.WarehouseProduct.objects.exclude(barcode__isnull=True).exclude(barcode="")
        if opts["company"]:
            qs = qs.filter(company_id=opts["company"])
        groups = defaultdict(list)
        for pid, company_id, barcode, key in qs.values_list("id", "company_id", "barcode", "catalog_key"):
            groups[(company_id, barcode.strip())].append((pid, key))

        to_set = 0
        conflicts = 0
        plan = []
        for (company_id, barcode), items in groups.items():
            if len(items) < 2:
                continue
            keys = {k for _pid, k in items if k}
            if len(keys) > 1:
                conflicts += 1
                self.stdout.write(self.style.WARNING(
                    f"компания {company_id}, штрихкод {barcode}: разные ключи {len(keys)} — пропуск"
                ))
                continue
            key = next(iter(keys)) if keys else uuid.uuid4()
            ids = [pid for pid, k in items if k != key]
            if ids:
                plan.append((key, ids))
                to_set += len(ids)

        self.stdout.write(f"Групп по штрихкоду: {len(plan)}, товаров к обновлению: {to_set}, конфликтов: {conflicts}.")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))
            return
        with transaction.atomic():
            for key, ids in plan:
                m.WarehouseProduct.objects.filter(pk__in=ids).update(catalog_key=key)
        self.stdout.write(self.style.SUCCESS(f"Обновлено товаров: {to_set}."))
