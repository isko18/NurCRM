"""Отчёт приложения клиентов по неделям: python manage.py client_app_weekly_report [--weeks 12]"""
from django.core.management.base import BaseCommand

from apps.clientapp.report import weekly_report


class Command(BaseCommand):
    help = "Магазины, покупатели и продажи через приложение по неделям"

    def add_arguments(self, parser):
        parser.add_argument("--weeks", type=int, default=12)

    def handle(self, *args, **opts):
        r = weekly_report(opts["weeks"])
        fp = r["free_period"]
        self.stdout.write(f"Бесплатный период: {'идёт' if fp['active'] else 'закончился'}, до {fp['free_until'] or 'без срока'}")
        n = r["now"]
        self.stdout.write(f"Сейчас на карте: {n['shops_on_map']} магазинов ({n['companies_on_map']} компаний), покупателей: {n['customers']}")
        self.stdout.write("неделя                  магазины(+нов) покупатели(+нов) продажи  сумма         покупали")
        for w in r["weeks"]:
            self.stdout.write(
                f"{w['week_start']}..{w['week_end'][5:]}  {w['shops_enabled']:>5} (+{w['shops_new']:<3})"
                f"  {w['customers_total']:>6} (+{w['customers_new']:<4})  {w['app_sales_count']:>6}"
                f"  {w['app_sales_total']:>12}  {w['app_buyers']:>6}"
            )
