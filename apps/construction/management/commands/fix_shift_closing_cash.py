"""
Разовая правка конечной суммы смены, закрытой на чужой кассе.

Кейс: кассир открыл смену не на своей кассе (там уже была открыта чужая смена на
том же физическом ящике), при закрытии ввёл фактический остаток ЧУЖОГО ящика — и
получил расхождение на чужие деньги при нуле продаж и нуле операций.

Команда приводит такую смену к нулевому расхождению (closing_cash = opening_cash).
Защита: правим только смену, в которой нет ни одной продажи и ни одного движения
денег — иначе правка исказила бы реальную отчётность.

    manage.py fix_shift_closing_cash <shift_id>            # показать, что будет сделано
    manage.py fix_shift_closing_cash <shift_id> --apply    # применить
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.construction.models import CashFlow, CashShift


class Command(BaseCommand):
    help = "Обнуляет расхождение по кассе для пустой смены (closing_cash = opening_cash)."

    def add_arguments(self, parser):
        parser.add_argument("shift_id", help="UUID смены")
        parser.add_argument("--apply", action="store_true", help="Применить (без флага — только показать)")
        parser.add_argument(
            "--expect-closing", default=None,
            help="Ожидаемое текущее closing_cash — страховка от правки не той смены.",
        )
        parser.add_argument("--reason", default="", help="Метка в close_reason для аудита.")

    def handle(self, *args, **opts):
        from apps.main.models import Sale

        try:
            shift = CashShift.objects.select_related("cashbox", "cashier", "company").get(pk=opts["shift_id"])
        except (CashShift.DoesNotExist, ValueError, TypeError):
            raise CommandError(f"Смена {opts['shift_id']} не найдена.")

        sales = Sale.objects.filter(shift=shift).count()
        flows = CashFlow.objects.filter(shift=shift).count()

        self.stdout.write(
            f"Смена {shift.pk}\n"
            f"  компания : {shift.company.name}\n"
            f"  кассир   : {getattr(shift.cashier, 'email', None)}\n"
            f"  касса    : {getattr(shift.cashbox, 'name', None)}\n"
            f"  статус   : {shift.status}\n"
            f"  opening  : {shift.opening_cash}\n"
            f"  closing  : {shift.closing_cash}\n"
            f"  expected : {shift.expected_cash}\n"
            f"  diff     : {shift.cash_diff}\n"
            f"  продаж   : {sales}, операций: {flows}"
        )

        if sales or flows:
            raise CommandError("В смене есть продажи или движения денег — правка запрещена.")
        if shift.closing_cash is None:
            raise CommandError("Смена ещё не закрыта — править нечего.")

        expect = opts.get("expect_closing")
        if expect is not None and shift.closing_cash != Decimal(expect):
            raise CommandError(f"closing_cash={shift.closing_cash}, ожидалось {expect}. Отмена.")

        if shift.closing_cash == shift.opening_cash:
            self.stdout.write(self.style.SUCCESS("Расхождение уже нулевое — ничего делать не нужно."))
            return

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                f"DRY-RUN: closing_cash {shift.closing_cash} → {shift.opening_cash}, diff → 0.00. "
                f"Повторите с --apply."
            ))
            return

        old = shift.closing_cash
        with transaction.atomic():
            shift.closing_cash = shift.opening_cash
            reason = opts["reason"] or f"fix_closing_cash_was_{old}"
            shift.close_reason = reason[:64]
            shift.save(update_fields=["closing_cash", "close_reason"])

        shift.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f"Готово: closing_cash {old} → {shift.closing_cash}, diff = {shift.cash_diff}, "
            f"close_reason = {shift.close_reason!r}"
        ))
