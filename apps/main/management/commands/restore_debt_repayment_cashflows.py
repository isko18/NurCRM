"""
ТЗ ч.12, 2.1: восстановить приходы в кассу по оплатам долга, «склеенным» защитой от дублей.

Раньше каждая оплата взноса (deals/{id}/pay/) и оплата старого «Долга» (debts/{id}/pay/, PATCH debts/{id}/)
писала приход без ключа, а create_auto_cashflow считал дублем любой приход по той же сделке на ту же сумму.
Итог: из 30 взносов по 1,33 в кассу попадал один. Команда находит платежи без своего прихода
и создаёт приход в ту смену кассы, которая была открыта в момент оплаты.

  python manage.py restore_debt_repayment_cashflows              # только показать
  python manage.py restore_debt_repayment_cashflows --apply      # записать
  python manage.py restore_debt_repayment_cashflows --company-id <uuid> --apply
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.construction.models import CashFlow, CashShift
from apps.main.models import Client, ClientDeal, DealPayment, Debt, DebtPayment


def _find_shift(cashbox_id, at, cashier_id=None):
    qs = CashShift.objects.filter(cashbox_id=cashbox_id, opened_at__lte=at).filter(
        **{}
    )
    qs = [s for s in qs.order_by("-opened_at")[:20] if s.closed_at is None or s.closed_at >= at]
    if cashier_id:
        own = [s for s in qs if s.cashier_id == cashier_id]
        if own:
            return own[0]
    return qs[0] if qs else None


class Command(BaseCommand):
    help = "Восстановить потерянные приходы по оплатам долга (ТЗ ч.12, 2.1)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--company-id")
        parser.add_argument("--before", help="ISO-время: брать платежи до него (по умолчанию — сейчас)")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        before = timezone.now()
        if opts.get("before"):
            from django.utils.dateparse import parse_datetime
            before = parse_datetime(opts["before"])
        stats = defaultdict(lambda: {"count": 0, "amount": Decimal("0.00")})
        touched_closed = set()
        skipped = []

        with transaction.atomic():
            # ---- сделки (ClientDeal)
            pays = (
                DealPayment.objects.filter(kind=DealPayment.Kind.PAY, deal__kind=ClientDeal.Kind.DEBT, created_at__lt=before)
                .select_related("deal", "deal__client")
                .order_by("created_at")
            )
            if opts.get("company_id"):
                pays = pays.filter(company_id=opts["company_id"])
            by_deal = defaultdict(list)
            for p in pays:
                by_deal[p.deal_id].append(p)
            for deal_id, plist in by_deal.items():
                deal = plist[0].deal
                is_supplier = bool(deal.client) and str(getattr(deal.client, "type", "")) in (
                    str(Client.StatusClient.SUPPLIERS), "suppliers"
                )
                kind = CashFlow.SourceKind.SUPPLIER_DEBT_PAYMENT if is_supplier else CashFlow.SourceKind.DEBT_REPAYMENT
                typ = CashFlow.Type.EXPENSE if is_supplier else CashFlow.Type.INCOME
                own_ids = {str(p.id) for p in plist}
                covered = set(
                    CashFlow.objects.filter(company_id=deal.company_id, source_id__in=own_ids).values_list("source_id", flat=True)
                ) | {
                    k.split(":", 1)[1]
                    for k in CashFlow.objects.filter(
                        company_id=deal.company_id, idempotency_key__in=[f"deal-pay:{i}" for i in own_ids]
                    ).values_list("idempotency_key", flat=True)
                }
                legacy = list(
                    CashFlow.objects.filter(
                        company_id=deal.company_id, source_kind=kind, type=typ, source_id=str(deal.id),
                        idempotency_key__isnull=True,
                    ).order_by("created_at")
                )
                self._restore(deal.company, kind, typ, str(deal.id),
                              [p for p in plist if str(p.id) not in covered],
                              legacy, "deal-pay", lambda p: p.created_by_id,
                              f"Оплата долга: {deal.title or (deal.client.full_name if deal.client else 'Сделка')}",
                              apply, stats, touched_closed, skipped)

            # ---- старые долги (Debt)
            dpays = DebtPayment.objects.filter(created_at__lt=before).select_related("debt").order_by("created_at")
            if opts.get("company_id"):
                dpays = dpays.filter(company_id=opts["company_id"])
            by_debt = defaultdict(list)
            for p in dpays:
                by_debt[p.debt_id].append(p)
            for debt_id, plist in by_debt.items():
                debt = plist[0].debt
                kind, typ = CashFlow.SourceKind.DEBT_REPAYMENT, CashFlow.Type.INCOME
                keyed = {
                    k.split(":", 1)[1]
                    for k in CashFlow.objects.filter(
                        company_id=debt.company_id, idempotency_key__in=[f"debt-pay:{p.id}" for p in plist]
                    ).values_list("idempotency_key", flat=True)
                }
                legacy = list(
                    CashFlow.objects.filter(
                        company_id=debt.company_id, source_kind=kind, type=typ, source_id=str(debt.id),
                        idempotency_key__isnull=True,
                    ).order_by("created_at")
                )
                self._restore(debt.company, kind, typ, str(debt.id),
                              [p for p in plist if str(p.id) not in keyed],
                              legacy, "debt-pay", lambda p: None,
                              f"Оплата долга: {debt.name or 'Долг'}",
                              apply, stats, touched_closed, skipped)

            for sh in CashShift.objects.filter(id__in=touched_closed):
                sh.recalc_totals_for_close()
                sh.save(update_fields=[
                    "income_total", "expense_total", "sales_count", "sales_total",
                    "cash_sales_total", "noncash_sales_total",
                ])

            real = {k: v for k, v in stats.items() if k[0] != "—"}
            total_n = sum(v["count"] for v in real.values())
            total_a = sum(v["amount"] for v in real.values())
            for (company_name, kind), v in sorted(stats.items(), key=lambda x: str(x[0])):
                self.stdout.write(f"  {company_name} / {kind}: {v['count']} записей на {v['amount']}")
            self.stdout.write(f"ИТОГО: {total_n} приходов на {total_a}; закрытых смен пересчитано: {len(touched_closed)}")
            if skipped:
                self.stdout.write(f"Пропущено (по сделке нет ни одного прихода — не эта ошибка): {len(skipped)}")
                for s in skipped[:20]:
                    self.stdout.write(f"    {s}")
            if not apply:
                transaction.set_rollback(True)
                self.stdout.write("ПРОБНЫЙ ЗАПУСК — ничего не записано. Для записи: --apply")
            else:
                self.stdout.write("ГОТОВО — записано.")

    def _restore(self, company, kind, typ, source_id, candidates, legacy, key_prefix, cashier_of, name,
                 apply, stats, touched_closed, skipped):
        if not candidates or not legacy:
            if candidates and not legacy:
                skipped.append(f"{company.name}: {source_id} — {len(candidates)} платежей, приходов по сделке нет")
            return
        # старые приходы без ключа достаются платежам той же суммы по порядку — остальные потеряны
        # Ошибка теряла приход, только если по этой сделке УЖЕ был приход на ту же сумму.
        # Платёж на сумму, по которой приходов нет вовсе, потерян не из-за неё — не трогаем.
        pool = defaultdict(list)
        for cf in legacy:
            pool[cf.amount].append(cf)
        glued_amounts = set(pool)
        missing = []
        for p in candidates:
            amt = Decimal(p.amount).quantize(Decimal("0.01"))
            if pool[amt]:
                pool[amt].pop(0)
            elif amt in glued_amounts:
                missing.append(p)
            else:
                stats[("—", "не эта ошибка (сумма без единого прихода)")]["count"] += 1
                stats[("—", "не эта ошибка (сумма без единого прихода)")]["amount"] += amt
        if not missing:
            return
        templates = legacy
        if not templates:
            skipped.append(f"{company.name}: {source_id} — {len(missing)} платежей, образца прихода нет")
            return
        for p in missing:
            at = p.created_at
            tpl = min(templates, key=lambda c: abs((c.created_at - at).total_seconds()))
            shift = None
            if kind != CashFlow.SourceKind.SUPPLIER_DEBT_PAYMENT:
                shift = _find_shift(tpl.cashbox_id, at, cashier_of(p) or tpl.cashier_id)
            pm = (getattr(p, "payment_method", None) or tpl.payment_method or "cash").lower()
            cf = CashFlow(
                company=company,
                branch=tpl.branch,
                cashbox_id=tpl.cashbox_id,
                shift=shift,
                cashier_id=(shift.cashier_id if shift else tpl.cashier_id),
                type=typ,
                name=name,
                amount=Decimal(p.amount).quantize(Decimal("0.01")),
                status=tpl.status,
                affects_shift_drawer=bool(shift) and pm == "cash" and typ == CashFlow.Type.INCOME,
                source_kind=kind,
                source_id=source_id,
                source_business_operation_id=tpl.source_business_operation_id or "Оплата долга",
                idempotency_key=f"{key_prefix}:{p.id}",
                payment_method=pm,
            )
            cf.save()
            CashFlow.objects.filter(pk=cf.pk).update(created_at=at)
            if shift is not None and shift.status == CashShift.Status.CLOSED:
                touched_closed.add(shift.id)
            k = (company.name, kind)
            stats[k]["count"] += 1
            stats[k]["amount"] += cf.amount
