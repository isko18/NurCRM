"""
QA B42: удаление пустых черновиков (без строк) старше N дней (по умолчанию 7).

Фронт создаёт черновик до добавления строк, поэтому пустые черновики копятся. Удаляются
только DRAFT без строк, без движений, без денежного документа, без кассового запроса,
не заявки агента.

  python manage.py cleanup_empty_drafts                 # отчёт (dry-run)
  python manage.py cleanup_empty_drafts --days 14
  python manage.py cleanup_empty_drafts --apply
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.warehouse import models as m


def empty_drafts_qs(days: int, company_id=None):
    border = timezone.now() - timedelta(days=days)
    qs = (
        m.Document.objects.filter(status=m.Document.Status.DRAFT, created_at__lt=border, is_sale_request=False)
        .annotate(
            has_items=Exists(m.DocumentItem.objects.filter(document_id=OuterRef("pk"))),
            has_moves=Exists(m.StockMove.objects.filter(document_id=OuterRef("pk"))),
            has_money=Exists(m.MoneyDocument.objects.filter(source_document_id=OuterRef("pk"))),
            has_cash_req=Exists(m.CashApprovalRequest.objects.filter(document_id=OuterRef("pk"))),
            has_cart=Exists(m.AgentRequestCart.objects.filter(sale_document_id=OuterRef("pk"))),
            has_returns=Exists(m.Document.objects.filter(base_document_id=OuterRef("pk"))),
        )
        .filter(has_items=False, has_moves=False, has_money=False, has_cash_req=False, has_cart=False, has_returns=False)
    )
    if company_id:
        qs = qs.filter(company_id=company_id)
    return qs


class Command(BaseCommand):
    help = "Удаляет черновики без строк старше N дней. По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--company", default=None)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        qs = empty_drafts_qs(opts["days"], opts["company"])
        count = qs.count()
        self.stdout.write(f"Пустых черновиков старше {opts['days']} дн.: {count}.")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))
            return
        deleted, _ = m.Document.objects.filter(pk__in=list(qs.values_list("pk", flat=True))).delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено записей: {deleted}."))
