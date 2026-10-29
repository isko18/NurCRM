"""
QA B13: слияние дублей системных категорий платежей.

Раньше системные категории («Закупка», «Продажа», «Долги», …) создавались отдельно на
каждый филиал и на компанию, поэтому в списке были дубли, а отчёты по категориям
расходились. Теперь системная категория одна на компанию (branch=NULL).

Для каждой компании и system_code: оставляется одна категория компании (самая старая
по номеру первого документа, иначе любая), на неё перевешиваются денежные и товарные
документы дублей, дубли удаляются. Пользовательские категории (без system_code) с тем же
названием не трогаются — только отчёт.

  python manage.py merge_system_payment_categories            # отчёт (dry-run)
  python manage.py merge_system_payment_categories --apply    # записать
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Min

from apps.warehouse import models as m


class Command(BaseCommand):
    help = "Сливает дубли системных категорий платежей (одна на компанию). По умолчанию — dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="UUID компании (по умолчанию — все).")
        parser.add_argument("--apply", action="store_true", help="Записать изменения в БД.")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        qs = m.PaymentCategory.objects.filter(system_code__isnull=False).select_related("company", "branch")
        if opts["company"]:
            qs = qs.filter(company_id=opts["company"])
        groups = defaultdict(list)
        for cat in qs:
            groups[(cat.company_id, cat.system_code)].append(cat)

        merged_cats = moved_money = moved_docs = 0
        for (company_id, code), cats in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if len(cats) == 1 and cats[0].branch_id is None:
                continue
            usage = {
                r["payment_category_id"]: r
                for r in m.MoneyDocument.objects.filter(payment_category__in=cats)
                .values("payment_category_id")
                .annotate(n=Count("id"), first=Min("created_at"))
            }
            company_level = [c for c in cats if c.branch_id is None]
            pool = company_level or cats

            def _first_use(c):
                # Сначала категории с документами (по дате первого документа), потом неиспользуемые.
                first = (usage.get(c.id) or {}).get("first")
                return (first is None, first.isoformat() if first else "", str(c.id))

            keep = min(pool, key=_first_use)
            dups = [c for c in cats if c.id != keep.id]
            name = cats[0].company.name if cats[0].company_id else "—"
            self.stdout.write(
                f"{name} ({company_id}) [{code}]: оставить «{keep.title}» "
                f"({'компания' if keep.branch_id is None else 'филиал ' + str(keep.branch_id)}), "
                f"дублей: {len(dups)}, документов на дублях: "
                f"{sum(usage.get(c.id, {}).get('n', 0) for c in dups)}"
            )
            if not apply:
                continue
            with transaction.atomic():
                moved_money += m.MoneyDocument.objects.filter(payment_category__in=dups).update(payment_category=keep)
                moved_docs += m.Document.objects.filter(payment_category__in=dups).update(payment_category=keep)
                for c in dups:
                    c.delete()
                    merged_cats += 1
                if keep.branch_id is not None:
                    clash = m.PaymentCategory.objects.filter(
                        company_id=company_id, branch__isnull=True, title=keep.title
                    ).exclude(pk=keep.pk)
                    if clash.exists():
                        self.stdout.write(self.style.WARNING(
                            f"  «{keep.title}»: есть пользовательская категория компании с тем же названием — "
                            "системная оставлена на филиале."
                        ))
                    else:
                        keep.branch = None
                        keep.save(update_fields=["branch"])

        same_title = (
            m.PaymentCategory.objects.filter(system_code__isnull=True)
            .values("company_id", "title")
            .annotate(n=Count("id"))
            .filter(title__in=[c.label for c in m.PaymentCategory.SystemCode])
        )
        if opts["company"]:
            same_title = same_title.filter(company_id=opts["company"])
        for r in same_title:
            self.stdout.write(
                f"  отчёт: компания {r['company_id']} — пользовательская категория «{r['title']}» ×{r['n']} (не трогаем)"
            )

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"Удалено дублей: {merged_cats}; перевешено денежных документов: {moved_money}, товарных: {moved_docs}."
            ))
        else:
            self.stdout.write(self.style.WARNING("Сухой прогон: в БД ничего не записано. Для записи добавьте --apply."))
