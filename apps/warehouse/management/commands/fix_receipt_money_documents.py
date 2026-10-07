"""
Исправление денежных документов, ошибочно созданных оприходованием (RECEIPT) — A1.

Раньше _resolve_money_doc_type сопоставлял приход товара с ПРИХОДОМ денег
(MONEY_RECEIPT). Из-за этого в кассе появлялся фиктивный приход, а баланс кассы
завышался. Маппинг исправлен (RECEIPT → MONEY_EXPENSE), эта команда чистит старые
данные.

По умолчанию — СУХОЙ ПРОГОН: только отчёт, в БД ничего не пишется.
Запись — только с явным флагом --apply.

Шаг 1 (отклонение ошибочных приходов):
  python manage.py fix_receipt_money_documents                 # отчёт
  python manage.py fix_receipt_money_documents --apply         # POSTED MONEY_RECEIPT → REJECTED + пометка

Шаг 2 (после ответа владельца компании, по каждому товарному документу отдельно):
  # товар оплачен из кассы → создать корректный расход (MONEY_EXPENSE) на дату документа
  python manage.py fix_receipt_money_documents --regenerate-expense <document_uuid> [--apply]
  # товар оплачен не из кассы → payment_kind = external («Вне кассы»)
  python manage.py fix_receipt_money_documents --mark-external <document_uuid> [--apply]

Опции:
  --company <uuid>   ограничить одной компанией
  --commit           устаревший синоним --apply

Команда идемпотентна: уже отклонённые документы не трогаются повторно, расход не
создаётся второй раз, если у документа уже есть не отклонённый денежный документ.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.warehouse import models as m
from apps.warehouse import services, services_money
from apps.warehouse.analytics_cache import bump_analytics_version

FIX_NOTE = "Исправление: приход товара ошибочно создал приход денег."
REGEN_NOTE = "Исправление: расход по приходу товара создан заново (оплата из кассы подтверждена владельцем)."


class Command(BaseCommand):
    help = "Отклоняет MONEY_RECEIPT, ошибочно созданные оприходованием товара (RECEIPT). По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--dry-run", action="store_true", default=True, help="Только отчёт (по умолчанию).")
        parser.add_argument("--apply", action="store_true", help="Записать изменения в БД.")
        parser.add_argument("--commit", action="store_true", help="Устаревший синоним --apply.")
        parser.add_argument(
            "--regenerate-expense",
            nargs="+",
            default=None,
            metavar="DOCUMENT_ID",
            help="Создать MONEY_EXPENSE для указанных RECEIPT-документов (владелец подтвердил оплату из кассы).",
        )
        parser.add_argument(
            "--mark-external",
            nargs="+",
            default=None,
            metavar="DOCUMENT_ID",
            help="Сменить payment_kind на external у указанных RECEIPT-документов (оплата не из кассы).",
        )

    # ------------------------------------------------------------------ helpers

    def _dry(self, apply):
        if not apply:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))

    def _receipt_docs(self, ids, company):
        qs = m.Document.objects.filter(pk__in=ids, doc_type=m.Document.DocType.RECEIPT).select_related(
            "warehouse_from__company"
        )
        if company:
            qs = qs.filter(warehouse_from__company_id=company)
        found = {str(d.pk): d for d in qs}
        missing = [i for i in ids if i not in found]
        if missing:
            raise CommandError(f"Не найдены RECEIPT-документы: {', '.join(missing)}")
        return list(found.values())

    @staticmethod
    def _linked_money_doc(doc):
        return m.MoneyDocument.objects.filter(source_document=doc).first()

    # ------------------------------------------------------------------ handle

    def handle(self, *args, **options):
        apply = bool(options["apply"] or options["commit"])
        company = options["company"]

        if options["regenerate_expense"] and options["mark_external"]:
            raise CommandError("Укажите либо --regenerate-expense, либо --mark-external.")
        if options["regenerate_expense"]:
            return self._regenerate_expense(options["regenerate_expense"], company, apply)
        if options["mark_external"]:
            return self._mark_external(options["mark_external"], company, apply)
        return self._reject_wrong_receipts(company, apply)

    def _reject_wrong_receipts(self, company, apply):
        qs = (
            m.MoneyDocument.objects.filter(
                source_document__doc_type=m.Document.DocType.RECEIPT,
                doc_type=m.MoneyDocument.DocType.MONEY_RECEIPT,
            )
            .exclude(status=m.MoneyDocument.Status.REJECTED)
            .select_related("company", "source_document", "cash_register", "payment_category")
            .order_by("company_id", "date")
        )
        if company:
            qs = qs.filter(company_id=company)

        docs = list(qs)
        if not docs:
            self.stdout.write("Ошибочных денежных документов не найдено.")
            return

        total = Decimal("0.00")
        companies = set()
        self.stdout.write("money_doc_id | компания (id) | номер | дата | статус | сумма | касса | категория | товарный документ (id, номер, дата, payment_kind)")
        for doc in docs:
            total += doc.amount or Decimal("0.00")
            companies.add(doc.company_id)
            src = doc.source_document
            self.stdout.write(
                f"{doc.pk} | {doc.company.name} ({doc.company_id}) | {doc.number or '—'} | {doc.date:%Y-%m-%d %H:%M} | "
                f"{doc.status} | {doc.amount} | {getattr(doc.cash_register, 'name', '—')} | "
                f"{getattr(doc.payment_category, 'title', 'Без категории')} | "
                f"{src.pk}, {src.number or '—'}, {src.date:%Y-%m-%d}, {src.payment_kind}"
            )
        self.stdout.write(f"Итого: {len(docs)} док., {total} сом, компаний: {len(companies)}.")

        if not apply:
            self._dry(apply)
            return

        fixed = 0
        for doc in docs:
            with transaction.atomic():
                doc = m.MoneyDocument.objects.select_for_update().get(pk=doc.pk)
                if doc.status == m.MoneyDocument.Status.REJECTED:
                    continue  # уже исправлен параллельным запуском
                if doc.status == m.MoneyDocument.Status.POSTED:
                    # POSTED → REJECTED: движение по кассе откатывается (баланс — только по POSTED).
                    services_money.reject_money_document(doc)
                else:
                    doc.status = m.MoneyDocument.Status.REJECTED
                    doc.save(update_fields=["status"])
                if FIX_NOTE not in (doc.comment or ""):
                    doc.comment = f"{doc.comment}\n{FIX_NOTE}".strip()
                    doc.save(update_fields=["comment"])
            fixed += 1

        for company_id in companies:
            bump_analytics_version(company_id)

        self.stdout.write(self.style.SUCCESS(f"Отклонено: {fixed}."))
        self.stdout.write(
            "Дальше: спросить владельцев, оплачивался ли товар из кассы, и запустить "
            "--regenerate-expense <id> или --mark-external <id>."
        )

    def _regenerate_expense(self, ids, company, apply):
        for doc in self._receipt_docs(ids, company):
            money = self._linked_money_doc(doc)
            amount = Decimal(doc.total or 0).quantize(Decimal("0.01"))
            prefix = f"{doc.pk} ({doc.number or '—'}, {doc.date:%Y-%m-%d}, {amount} сом)"
            if doc.status != m.Document.Status.POSTED:
                self.stdout.write(self.style.WARNING(f"{prefix}: документ не проведён — пропуск."))
                continue
            if money is not None and money.status != m.MoneyDocument.Status.REJECTED:
                self.stdout.write(f"{prefix}: уже есть денежный документ {money.pk} ({money.doc_type}, {money.status}) — пропуск.")
                continue
            if amount <= 0:
                self.stdout.write(self.style.WARNING(f"{prefix}: сумма 0 — пропуск."))
                continue
            self.stdout.write(f"{prefix}: будет создан и проведён MONEY_EXPENSE {amount} на дату документа.")
            if not apply:
                continue
            with transaction.atomic():
                doc = m.Document.objects.select_for_update(of=("self",)).get(pk=doc.pk)
                if money is not None:
                    # OneToOne source_document: отвязываем отклонённый приход (номер документа
                    # остаётся в его комментарии), чтобы привязать корректный расход.
                    m.MoneyDocument.objects.filter(pk=money.pk).update(source_document=None)
                    doc = m.Document.objects.get(pk=doc.pk)
                new_money = services._create_or_post_money_document(
                    doc, money_doc_type=m.MoneyDocument.DocType.MONEY_EXPENSE, amount=amount
                )
                m.MoneyDocument.objects.filter(pk=new_money.pk).update(
                    date=doc.date, comment=f"{new_money.comment}\n{REGEN_NOTE}".strip()
                )
                company_id = new_money.company_id
            transaction.on_commit(lambda cid=company_id: bump_analytics_version(cid))
            self.stdout.write(self.style.SUCCESS(f"{prefix}: создан MONEY_EXPENSE {new_money.pk}."))
        self._dry(apply)

    def _mark_external(self, ids, company, apply):
        for doc in self._receipt_docs(ids, company):
            prefix = f"{doc.pk} ({doc.number or '—'}, {doc.date:%Y-%m-%d}, {doc.total} сом)"
            if doc.payment_kind == m.Document.PaymentKind.EXTERNAL:
                self.stdout.write(f"{prefix}: уже external — пропуск.")
                continue
            self.stdout.write(f"{prefix}: payment_kind {doc.payment_kind} → external.")
            if not apply:
                continue
            m.Document.objects.filter(pk=doc.pk).update(payment_kind=m.Document.PaymentKind.EXTERNAL)
            company_id = getattr(doc.warehouse_from, "company_id", None)
            if company_id:
                bump_analytics_version(company_id)
        self._dry(apply)
