"""
Пересчёт итогов документов после исправления двойной скидки.

Раньше по строке вычитались обе скидки сразу: и процент, и сумма. Клиент шлёт
одну и ту же скидку двумя полями (5% и её сумму в сомах), поэтому товар за 100 сом
с 5% сохранялся как 90, а не 95. Теперь на строку действует одна скидка: процент,
а если процента нет — сумма. У документов, сохранённых до фикса, итоги остались
завышенно уценёнными — эта команда их пересчитывает.

Запуск (сначала обязательно без --commit — только отчёт):
  python manage.py recalc_document_discounts
  python manage.py recalc_document_discounts --commit

Опции:
  --company <uuid>   ограничить одной компанией
  --limit <n>        обработать не больше n документов
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.warehouse import models as m
from apps.warehouse import services


class Command(BaseCommand):
    help = "Пересчитывает line_total/total документов, где раньше вычитались процент и сумма скидки сразу."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--limit", type=int, default=None, help="Максимум документов.")
        parser.add_argument("--commit", action="store_true", help="Записать изменения в БД.")

    def handle(self, *args, **options):
        commit = bool(options["commit"])
        limit = options["limit"]

        # Затронуты только строки, где одновременно заданы процент и сумма скидки:
        # либо свой процент строки, либо общий процент документа как fallback.
        affected_items = m.DocumentItem.objects.filter(
            Q(discount_percent__gt=0) | Q(document__discount_percent__gt=0),
            discount_amount__gt=0,
        )
        doc_ids = affected_items.values_list("document_id", flat=True).distinct()

        docs = m.Document.objects.filter(id__in=list(doc_ids)).order_by("created_at")
        if options["company"]:
            # У Document нет своей company — она берётся со склада списания/поступления.
            docs = docs.filter(
                Q(warehouse_from__company_id=options["company"])
                | Q(warehouse_to__company_id=options["company"])
            )
        if limit:
            docs = docs[:limit]

        changed = 0
        delta_sum = Decimal("0.00")

        for doc in docs:
            old_total = Decimal(doc.total or 0)
            if commit:
                with transaction.atomic():
                    services.recalc_document_totals(doc)
                new_total = Decimal(doc.total or 0)
            else:
                new_total = self._preview_total(doc)

            if new_total != old_total:
                changed += 1
                delta_sum += new_total - old_total
                self.stdout.write(
                    f"{doc.number or doc.id}: {old_total} -> {new_total} ({new_total - old_total:+})"
                )

        mode = "записано" if commit else "предпросмотр (--commit не задан)"
        self.stdout.write(self.style.SUCCESS(
            f"Документов затронуто: {changed}; суммарная поправка: {delta_sum:+}; режим: {mode}."
        ))

    @staticmethod
    def _preview_total(doc) -> Decimal:
        doc_dp = Decimal(doc.discount_percent or 0)
        subtotal = Decimal("0.00")
        for item in doc.items.all():
            subtotal += services.compute_document_line_total(
                price=item.price,
                qty=item.qty,
                line_discount_percent=item.discount_percent,
                line_discount_amount=item.discount_amount,
                document_discount_percent=doc_dp,
            )
        doc_da = Decimal(doc.discount_amount or 0)
        return max(Decimal("0.00"), (subtotal - doc_da).quantize(Decimal("0.01")))
