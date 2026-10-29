"""
Документы с датой в будущем (A14).

Продажа с date «завтра+» не видна в текущем периоде аналитики. Новые такие документы
запрещены (serializer + post_document: «Дата документа не может быть в будущем»), а
существующие (на проде — 7 продаж, 54 027 сом) нужно показать владельцам.

По умолчанию — СУХОЙ ПРОГОН (только отчёт): id, компания, номер, тип, статус, дата,
created_at, сумма.

  python manage.py report_future_dated_documents
  python manage.py report_future_dated_documents --company <uuid>
  python manage.py report_future_dated_documents --days 2        # date > created_at + 2 дня (по умолчанию 1)

Если владелец подтвердил, что дата ошибочная, — перенести дату на created_at:
  python manage.py report_future_dated_documents --ids <uuid> [<uuid> ...] --apply

--apply меняет ТОЛЬКО документы, перечисленные в --ids (без --ids запись запрещена).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import ExpressionWrapper, F, DateTimeField, Q
from django.utils import timezone

from apps.warehouse import models as m
from apps.warehouse.analytics_cache import bump_analytics_version


class Command(BaseCommand):
    help = "Отчёт по документам с датой в будущем; перенос даты на created_at только для --ids с --apply."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--days", type=int, default=1, help="Порог: date > created_at + N дней (по умолчанию 1).")
        parser.add_argument("--ids", nargs="+", default=None, help="Документы, которым перенести дату на created_at.")
        parser.add_argument("--dry-run", action="store_true", default=True, help="Только отчёт (по умолчанию).")
        parser.add_argument("--apply", action="store_true", help="Записать изменения (только вместе с --ids).")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        if apply and not options["ids"]:
            raise CommandError("--apply разрешён только вместе с --ids (явный список документов).")

        threshold = timedelta(days=max(0, options["days"]))
        qs = (
            m.Document.objects.annotate(
                _limit=ExpressionWrapper(F("created_at") + threshold, output_field=DateTimeField())
            )
            .filter(Q(date__gt=F("_limit")) | Q(date__gt=timezone.now() + threshold))
            .exclude(status=m.Document.Status.DRAFT)
            .select_related("warehouse_from__company", "warehouse_to__company")
            .order_by("date")
        )
        if options["company"]:
            qs = qs.filter(
                Q(warehouse_from__company_id=options["company"]) | Q(warehouse_to__company_id=options["company"])
            )
        if options["ids"]:
            qs = qs.filter(pk__in=options["ids"])

        docs = list(qs)
        total = sum((Decimal(d.total or 0) for d in docs), Decimal("0.00"))
        self.stdout.write("doc_id | компания (id) | номер | тип | статус | date | created_at | сумма")
        for d in docs:
            wh = d.warehouse_from or d.warehouse_to
            company = getattr(wh, "company", None)
            self.stdout.write(
                f"{d.pk} | {getattr(company, 'name', '?')} ({getattr(company, 'pk', None)}) | {d.number or '—'} | "
                f"{d.doc_type} | {d.status} | {timezone.localtime(d.date):%Y-%m-%d %H:%M} | "
                f"{timezone.localtime(d.created_at):%Y-%m-%d %H:%M} | {d.total}"
            )
        self.stdout.write(f"Итого: {len(docs)} док., {total} сом.")

        if options["ids"]:
            missing = set(options["ids"]) - {str(d.pk) for d in docs}
            if missing:
                self.stdout.write(self.style.WARNING(f"Не найдены среди документов с датой в будущем: {', '.join(sorted(missing))}"))

        if not apply:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано."))
            return

        companies = set()
        for d in docs:
            m.Document.objects.filter(pk=d.pk).update(date=d.created_at)
            wh = d.warehouse_from or d.warehouse_to
            if wh is not None:
                companies.add(wh.company_id)
        for cid in companies:
            bump_analytics_version(cid)
        self.stdout.write(self.style.SUCCESS(f"Дата перенесена на created_at: {len(docs)}."))
