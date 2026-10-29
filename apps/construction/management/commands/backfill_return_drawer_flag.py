"""
Снимает affects_shift_drawer у компенсирующих движений возврата (pos_sale_return).

Зачем: наличные смены считаются по строкам оплат живых чеков — возврат уже
отражён там (полный уводит чек из paid, частичный уменьшает строки оплат).
Пока у движения стоит флаг «влияет на ящик», та же сумма снимается второй раз.

    manage.py backfill_return_drawer_flag           # показать, что будет сделано
    manage.py backfill_return_drawer_flag --apply   # применить
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.construction.models import CashFlow


class Command(BaseCommand):
    help = "Снимает affects_shift_drawer у движений pos_sale_return."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Применить (без флага — только показать)")

    def handle(self, *args, **opts):
        qs = CashFlow.objects.filter(
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            affects_shift_drawer=True,
        )
        total = qs.count()
        shifts = len({sid for sid in qs.values_list("shift_id", flat=True) if sid})

        self.stdout.write(f"Движений возврата с флагом «влияет на ящик»: {total} (смен затронуто: {shifts})")
        for cf in qs.select_related("company")[:10]:
            self.stdout.write(
                f"   {cf.created_at:%d.%m.%Y} {cf.company.name[:24]:24} {cf.amount:>10} {cf.name!r}"
            )
        if total > 10:
            self.stdout.write(f"   … и ещё {total - 10}")

        if not total:
            self.stdout.write(self.style.SUCCESS("Нечего исправлять."))
            return

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY-RUN: ничего не изменено. Повторите с --apply."))
            return

        with transaction.atomic():
            updated = qs.update(affects_shift_drawer=False)
        self.stdout.write(self.style.SUCCESS(f"Готово: обновлено движений — {updated}."))
