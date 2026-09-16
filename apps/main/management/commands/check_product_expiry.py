from django.core.management.base import BaseCommand
from apps.users.models import Company
from apps.main.services_expiry import (
    send_product_expiry_digest_for_all_companies,
    send_product_expiry_digest_for_company,
)


class Command(BaseCommand):
    help = "Проверяет товары с истекающим/истёкшим сроком годности и отправляет дайджест уведомлений."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=str,
            default=None,
            help="ID конкретной компании для проверки (опционально)",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        if company_id:
            try:
                company = Company.objects.get(id=company_id)
                notif = send_product_expiry_digest_for_company(company)
                if notif:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created expiry notification {notif.id} for company {company.id}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"No notification created (no expiring products or already sent today) for company {company.id}"
                        )
                    )
            except Company.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Company {company_id} not found"))
        else:
            count = send_product_expiry_digest_for_all_companies()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Product expiry check complete. Created {count} notification(s)."
                )
            )
