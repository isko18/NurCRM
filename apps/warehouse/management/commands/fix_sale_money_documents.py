"""
QA B05: старые оплаченные продажи без денежного документа числятся долгом клиента.

Долг контрагента = продажи − поступления. У старых проведённых продаж с оплатой сразу
(payment_kind=cash) или с предоплатой нет проведённого MONEY_RECEIPT, поэтому оплаченная
продажа выглядит долгом. Новые продажи создают денежный документ правильно.

По умолчанию — СУХОЙ ПРОГОН: отчёт по компаниям (количество, сумма, период, касса).
С --apply создаёт и проводит MONEY_RECEIPT на оплаченную часть, датой продажи,
с системной категорией «Продажа», в кассу склада продажи, с флагом is_migration=true:
такой приход закрывает долг, но НЕ меняет остаток кассы (решение D-B05 по умолчанию).

  python manage.py fix_sale_money_documents                    # отчёт
  python manage.py fix_sale_money_documents --company <uuid>   # одна компания
  python manage.py fix_sale_money_documents --apply            # записать

Идемпотентна: продажа, у которой уже есть денежный документ, пропускается.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.warehouse import models as m
from apps.warehouse import services
from apps.warehouse.utils import effective_payment_kind, system_payment_category

NOTE = "Миграция B05: оплата старой продажи (касса не меняется)."


class Command(BaseCommand):
    help = "Создаёт миграционные MONEY_RECEIPT по старым оплаченным продажам без денег. По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--apply", action="store_true", help="Записать изменения в БД.")
        parser.add_argument("--details", action="store_true", help="Печатать каждую продажу.")

    def _candidates(self, company_id):
        qs = (
            m.Document.objects.filter(
                doc_type=m.Document.DocType.SALE,
                status=m.Document.Status.POSTED,
                agent__isnull=True,
            )
            .filter(money_document__isnull=True)
            .select_related("warehouse_from__company", "warehouse_from__branch", "counterparty", "cash_register")
            .order_by("date")
        )
        if company_id:
            qs = qs.filter(company_id=company_id)
        for doc in qs.iterator(chunk_size=500):
            kind = effective_payment_kind(doc.payment_kind)
            if kind == m.Document.PaymentKind.CASH:
                paid = Decimal(doc.total or 0)
            else:
                paid = Decimal(doc.prepayment_amount or 0)
            paid = paid.quantize(Decimal("0.01"))
            if paid <= 0 or not doc.counterparty_id:
                continue
            yield doc, paid

    def handle(self, *args, **opts):
        apply = opts["apply"]
        report = defaultdict(lambda: {"count": 0, "sum": Decimal("0.00"), "first": None, "last": None, "errors": 0})
        created = 0
        for doc, paid in self._candidates(opts["company"]):
            wh = None
            try:
                wh = services.resolve_document_context_warehouse(doc)
            except ValueError:
                pass
            company = getattr(wh, "company", None) or doc.company
            key = (str(getattr(company, "pk", "")), getattr(company, "name", "—"))
            row = report[key]
            row["count"] += 1
            row["sum"] += paid
            d = services.document_local_date(doc.date)
            row["first"] = min(row["first"] or d, d)
            row["last"] = max(row["last"] or d, d)
            register = None
            try:
                register = services.resolve_cash_register(
                    company=company, branch=getattr(wh, "branch", None), explicit=doc.cash_register
                )
            except ValueError as exc:
                row["errors"] += 1
                if opts["details"]:
                    self.stdout.write(f"  ! {doc.number}: {exc}")
                continue
            if opts["details"]:
                self.stdout.write(
                    f"  {doc.number} {d} {doc.counterparty.name}: {paid} → касса «{register.name}»"
                )
            if not apply:
                continue
            with transaction.atomic():
                locked = m.Document.objects.select_for_update().get(pk=doc.pk)
                if m.MoneyDocument.objects.filter(source_document=locked).exists():
                    continue
                md = m.MoneyDocument.objects.create(
                    doc_type=m.MoneyDocument.DocType.MONEY_RECEIPT,
                    status=m.MoneyDocument.Status.DRAFT,
                    date=doc.date,
                    cash_register=register,
                    counterparty=doc.counterparty,
                    payment_category=system_payment_category(company, m.PaymentCategory.SystemCode.SALE),
                    payment_method=doc.payment_method,
                    amount=paid,
                    comment=f"{NOTE} {doc.number}",
                    company=company,
                    branch=getattr(wh, "branch", None),
                    source_document=doc,
                    is_migration=True,
                )
                from apps.warehouse import services_money

                services_money.post_money_document(md)
                created += 1

        total_count = sum(r["count"] for r in report.values())
        total_sum = sum((r["sum"] for r in report.values()), Decimal("0.00"))
        self.stdout.write("Компания | продаж | сумма | период | без кассы")
        for (cid, name), r in sorted(report.items(), key=lambda kv: -kv[1]["sum"]):
            self.stdout.write(
                f"{name} ({cid}) | {r['count']} | {r['sum']} | {r['first']} — {r['last']} | {r['errors']}"
            )
        self.stdout.write(f"Итого: {total_count} продаж на {total_sum} сом.")
        if apply:
            from apps.warehouse.analytics_cache import bump_analytics_version

            for (cid, _name) in report:
                if cid:
                    bump_analytics_version(cid)
            self.stdout.write(self.style.SUCCESS(f"Создано миграционных приходов: {created}."))
        else:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))
