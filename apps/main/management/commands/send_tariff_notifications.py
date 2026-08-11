from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone as django_timezone
from apps.users.models import Company, User
from apps.main.models import Notification


class Command(BaseCommand):
    help = "Проверяет company.end_date и отправляет тарифные уведомления (category=tariff) за 7, 3 и 1 день."

    def handle(self, *args, **options):
        now = django_timezone.now()
        thresholds = [7, 3, 1]

        companies = Company.objects.filter(end_date__isnull=False, end_date__gt=now)

        created_count = 0
        for company in companies:
            delta_seconds = (company.end_date - now).total_seconds()
            days_left_float = delta_seconds / 86400.0
            days_left = max(1, int(round(days_left_float)))

            for threshold in thresholds:
                if days_left == threshold:
                    # Проверяем, отправляли ли уже уведомление для этого порога
                    already_sent = Notification.objects.filter(
                        company=company,
                        category=Notification.Category.TARIFF,
                        data__days_left=threshold,
                    ).exists()

                    if not already_sent:
                        users = User.objects.filter(company=company, is_active=True)
                        for user in users:
                            Notification.objects.create(
                                company=company,
                                user=user,
                                category=Notification.Category.TARIFF,
                                type="subscription",
                                title=f"Срок действия тарифа истекает через {threshold} дн.",
                                message=f"До окончания подписки компании '{company.name}' осталось {threshold} дн. Продлите тариф для бесперебойной работы.",
                                url="/crm/settings/subscription",
                                level=Notification.Level.HIGH if threshold <= 3 else Notification.Level.WARNING,
                                data={
                                    "days_left": threshold,
                                    "cta_label": "Продлить",
                                    "cta_url": "/crm/settings/subscription",
                                },
                            )
                            created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Успешно создано {created_count} тарифных уведомлений."))
