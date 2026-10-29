"""
Разбор проведённых документов без warehouse_from и без строк (A13).

Такие документы (на проде — 44 продажи май–август 2026, 685 903 сом) нельзя приписать
ни компании, ни складу: у них нет ни склада, ни товаров. В аналитику они не попадают,
но висят как «проведённые».

По умолчанию — СУХОЙ ПРОГОН: печатает id, компанию (по складу-приёмнику, денежному
документу, контрагенту или агенту), дату, тип и сумму. Ничего не пишет.

  python manage.py cleanup_empty_posted_documents                    # отчёт
  python manage.py cleanup_empty_posted_documents --company <uuid>   # отчёт по одной компании
  python manage.py cleanup_empty_posted_documents --apply            # распровести (status → draft)

--apply только РАСПРОВОДИТ документ (status → draft, пометка в комментарии): строк нет,
поэтому движения остатков нет и откатывать нечего. Документы не удаляются — это
обратимо. Документы, к которым привязан проведённый денежный документ или начисления
зарплаты агенту, НЕ трогаются (выводятся отдельным списком для ручного разбора).
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef

from apps.warehouse import models as m
from apps.warehouse.analytics_cache import bump_analytics_version

NOTE = "Исправление: проведённый документ без склада и без строк распроведён (A13)."


def _doc_company(doc):
    """(company_id, company_name) — по складу-приёмнику, денежному документу, контрагенту или агенту."""
    candidates = [
        getattr(doc.warehouse_to, "company", None) if doc.warehouse_to_id else None,
        getattr(getattr(doc, "money_document", None), "company", None),
        getattr(doc.counterparty, "company", None) if doc.counterparty_id else None,
        getattr(doc.agent, "company", None) if doc.agent_id else None,
    ]
    for c in candidates:
        if c is not None:
            return c.pk, c.name
    return None, "?"


class Command(BaseCommand):
    help = "Отчёт/распроведение проведённых документов без warehouse_from и без строк. По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--dry-run", action="store_true", default=True, help="Только отчёт (по умолчанию).")
        parser.add_argument("--apply", action="store_true", help="Распровести найденные документы.")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        has_items = m.DocumentItem.objects.filter(document_id=OuterRef("pk"))
        qs = (
            m.Document.objects.filter(
                status__in=(m.Document.Status.POSTED, m.Document.Status.CASH_PENDING),
                warehouse_from__isnull=True,
            )
            .exclude(doc_type__in=(m.Document.DocType.COMMERCIAL_OFFER, m.Document.DocType.TRANSFER))
            .annotate(_has_items=Exists(has_items))
            .filter(_has_items=False)
            .select_related("warehouse_to__company", "counterparty__company", "agent__company")
            .order_by("date")
        )

        rows, blocked = [], []
        for doc in qs:
            company_id, company_name = _doc_company(doc)
            if options["company"] and str(company_id) != str(options["company"]):
                continue
            money = m.MoneyDocument.objects.filter(source_document=doc, status=m.MoneyDocument.Status.POSTED).first()
            accruals = m.AgentSalaryAccrual.objects.filter(sale=doc).count()
            row = (doc, company_id, company_name, money, accruals)
            (blocked if (money is not None or accruals) else rows).append(row)

        total = sum((Decimal(r[0].total or 0) for r in rows + blocked), Decimal("0.00"))
        self.stdout.write("doc_id | компания (id) | номер | тип | статус | дата | сумма | агент | денежный док. | начисления")
        for doc, cid, cname, money, accruals in rows + blocked:
            self.stdout.write(
                f"{doc.pk} | {cname} ({cid}) | {doc.number or '—'} | {doc.doc_type} | {doc.status} | "
                f"{doc.date:%Y-%m-%d %H:%M} | {doc.total} | {doc.agent_id or '—'} | "
                f"{money.pk if money else '—'} | {accruals}"
            )
        self.stdout.write(f"Итого: {len(rows) + len(blocked)} док., {total} сом. К распроведению: {len(rows)}.")
        if blocked:
            self.stdout.write(self.style.WARNING(
                f"{len(blocked)} док. с проведённым денежным документом или начислениями — не трогаются, разобрать вручную."
            ))

        if not apply:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))
            return

        fixed, companies = 0, set()
        for doc, cid, _cname, _money, _acc in rows:
            with transaction.atomic():
                locked = m.Document.objects.select_for_update(of=("self",)).filter(pk=doc.pk).first()
                if locked is None or locked.status not in (m.Document.Status.POSTED, m.Document.Status.CASH_PENDING):
                    continue
                if locked.items.exists():
                    continue  # за время прогона появились строки — не трогаем
                comment = locked.comment or ""
                if NOTE not in comment:
                    comment = f"{comment}\n{NOTE}".strip()
                m.Document.objects.filter(pk=doc.pk).update(status=m.Document.Status.DRAFT, comment=comment)
                m.CashApprovalRequest.objects.filter(
                    document_id=doc.pk, status=m.CashApprovalRequest.Status.PENDING
                ).update(status=m.CashApprovalRequest.Status.REJECTED, decision_note=NOTE)
            fixed += 1
            if cid:
                companies.add(cid)
        for cid in companies:
            bump_analytics_version(cid)
        self.stdout.write(self.style.SUCCESS(f"Распроведено: {fixed}."))
