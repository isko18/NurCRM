from django.core.management.base import BaseCommand

from apps.main.realtime import check_and_create_tariff_notifications


class Command(BaseCommand):
    help = (
        "Проверяет company.end_date и отправляет уведомления tariff.expiring "
        "(category=tariff) владельцу за 7, 3 и 1 день до окончания подписки."
    )

    def handle(self, *args, **options):
        # Раньше команда создавала записи напрямую, мимо WS: тип был "subscription",
        # ссылка — /crm/settings/subscription, и копия уходила каждому сотруднику.
        # Это расходилось с контрактом фронта (enum tariff.expiring) и не давало
        # ни тоста, ни бейджа. Теперь единая точка — check_and_create_tariff_notifications.
        created = check_and_create_tariff_notifications()
        self.stdout.write(self.style.SUCCESS(f"Создано тарифных уведомлений: {len(created)}."))
