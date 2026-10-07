"""
ФИО покупателя на старых кассах: клиентам касс без имени (пусто / номер / «Клиент») с телефоном,
который есть в приложении, ставит ФИО из профиля приложения. Новые записи заполняются сигналом сами.

  python manage.py fill_client_names_from_app            # только показать
  python manage.py fill_client_names_from_app --apply    # записать
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clientapp.models import AppCustomer
from apps.clientapp.services import fill_client_names_from_customer


class Command(BaseCommand):
    help = "Проставить ФИО из приложения клиентам касс без имени"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        total = 0
        with transaction.atomic():
            for customer in AppCustomer.objects.filter(deleted_at__isnull=True).exclude(full_name="").exclude(phone=None):
                total += fill_client_names_from_customer(customer)
            self.stdout.write(f"Клиентов касс с ФИО из приложения: {total}")
            if not opts["apply"]:
                transaction.set_rollback(True)
                self.stdout.write("ПРОБНЫЙ ЗАПУСК — ничего не записано. Для записи: --apply")
