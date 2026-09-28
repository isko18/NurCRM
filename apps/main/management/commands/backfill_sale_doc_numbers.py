from django.core.management.base import BaseCommand
from django.db import transaction

from apps.main.models import Sale
from apps.main.utils_numbers import reserve_sale_doc_numbers


class Command(BaseCommand):
    help = (
        "Присваивает постоянный номер (doc_number) чекам без номера: по каждой компании, "
        "по дате создания, после текущего максимального номера. Уже выданные номера не меняются."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", help="UUID компании (по умолчанию все)")
        parser.add_argument("--batch", type=int, default=2000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = Sale.objects.filter(doc_number__isnull=True)
        if opts.get("company"):
            qs = qs.filter(company_id=opts["company"])
        company_ids = list(qs.values_list("company_id", flat=True).distinct())
        batch = max(1, opts["batch"])
        total = 0

        for company_id in company_ids:
            done = 0
            while True:
                ids = list(
                    Sale.objects.filter(company_id=company_id, doc_number__isnull=True)
                    .order_by("created_at", "id")
                    .values_list("id", flat=True)[:batch]
                )
                if not ids:
                    break
                if opts["dry_run"]:
                    done += Sale.objects.filter(company_id=company_id, doc_number__isnull=True).count()
                    break
                with transaction.atomic():
                    rows = list(
                        Sale.objects.select_for_update()
                        .filter(id__in=ids, doc_number__isnull=True)
                        .order_by("created_at", "id")
                        .only("id", "doc_number")
                    )
                    if not rows:
                        continue
                    first = reserve_sale_doc_numbers(company_id, len(rows))
                    for offset, sale in enumerate(rows):
                        sale.doc_number = first + offset
                    Sale.objects.bulk_update(rows, ["doc_number"], batch_size=500)
                done += len(rows)
            total += done
            self.stdout.write(f"{company_id}: {done}")

        verb = "будет пронумеровано" if opts["dry_run"] else "пронумеровано"
        self.stdout.write(self.style.SUCCESS(f"Итого {verb}: {total}"))
