"""
Найти штрихкоды, принадлежащие нескольким товарам внутри одной компании.

Коллизия = одно значение ШК встречается у >1 разных товаров, считая вместе основной
`Product.barcode` и дополнительные `ProductAlternateBarcode.barcode`. Именно из-за
таких коллизий на кассе «в чек уходит не тот товар». Пер-полевые DB-constraint'ы это
не ловят (основной ШК одного товара может совпасть с доп. кодом другого).

Команда только ПОКАЗЫВАЕТ коллизии — какой товар оставить за кодом, решается вручную
(снять чужой barcode / доп.код через админку или API).

Запуск:
  python manage.py audit_barcode_collisions                     # все компании
  python manage.py audit_barcode_collisions --company-id <uuid> # одна компания
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from apps.main.models import Product, ProductAlternateBarcode
from apps.users.models import Company


class Command(BaseCommand):
    help = "Показать штрихкоды, принадлежащие нескольким товарам внутри компании."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=str, default=None,
                            help="UUID компании: проверить только её.")

    def handle(self, *args, **opts):
        cid = (opts.get("company_id") or "").strip()
        if cid:
            company = Company.objects.filter(id=cid).first()
            if company is None:
                raise CommandError(f"Компания с id={cid} не найдена.")
            companies = [company]
        else:
            companies = list(Company.objects.order_by("name"))

        total_collisions = 0
        for company in companies:
            collisions = self._collisions_for_company(company)
            if not collisions:
                continue
            total_collisions += len(collisions)
            self.stdout.write(self.style.WARNING(
                f"\n{company.name} ({company.id}): коллизий {len(collisions)}"
            ))
            # Имена товаров одним запросом.
            all_ids = {pid for pids in collisions.values() for pid in pids}
            names = dict(
                Product.objects.filter(id__in=all_ids).values_list("id", "name")
            )
            for bc, pids in sorted(collisions.items()):
                self.stdout.write(f"  ШК «{bc}» → {len(pids)} товаров:")
                for pid in pids:
                    self.stdout.write(f"      {pid}  {names.get(pid, '?')}")

        if total_collisions == 0:
            self.stdout.write(self.style.SUCCESS("Коллизий штрихкодов не найдено."))
        else:
            self.stdout.write(self.style.WARNING(
                f"\nИтого коллизий: {total_collisions}. "
                f"Разрешите вручную (снимите чужой barcode/доп.код у лишних товаров)."
            ))

    def _collisions_for_company(self, company) -> dict:
        """{barcode: set(product_id)} только для значений, у которых >1 товара."""
        owners: dict = defaultdict(set)

        main = (
            Product.objects
            .filter(company=company)
            .exclude(barcode__isnull=True)
            .exclude(barcode="")
            .values_list("id", "barcode")
        )
        for pid, bc in main:
            owners[(bc or "").strip()].add(pid)

        alt = (
            ProductAlternateBarcode.objects
            .filter(company=company)
            .values_list("product_id", "barcode")
        )
        for pid, bc in alt:
            owners[(bc or "").strip()].add(pid)

        return {bc: pids for bc, pids in owners.items() if bc and len(pids) > 1}
