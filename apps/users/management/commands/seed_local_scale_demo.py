"""Локальная демо-БД для теста весов (идемпотентно)."""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.main.models import Product
from apps.users.models import Company, User

# Токен и company_id как в «Агент печати» на вашем ноутбуке
DEFAULT_COMPANY_ID = "83963228-e7ca-4132-bdc6-2c7f4a34014"
DEFAULT_SCALE_TOKEN = "932e6c0e3b0066e15f58b323adeebf10442a94249cb8da7ca6f105e3e1b513c9"
DEFAULT_EMAIL = "scales-local@local.test"
DEFAULT_PASSWORD = "scales123"

WEIGHT_PRODUCTS = [
    {"plu": 1, "name": "Тест NurCRM", "price": "100"},
    {"plu": 2, "name": "Гречка", "price": "120"},
    {"plu": 3, "name": "Яблоко", "price": "85"},
    {"plu": 4, "name": "Банан", "price": "95"},
    {"plu": 5, "name": "Молоко 1л", "price": "75"},
    {"plu": 10, "name": "Картофель", "price": "45"},
]


class Command(BaseCommand):
    help = "Создать локальную компанию, пользователя и весовые товары для теста весов."

    def add_arguments(self, parser):
        parser.add_argument("--token", default=DEFAULT_SCALE_TOKEN)
        parser.add_argument("--company-id", default=DEFAULT_COMPANY_ID)
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument("--password", default=DEFAULT_PASSWORD)

    @transaction.atomic
    def handle(self, *args, **opts):
        token = opts["token"].strip()
        company_id = opts["company_id"].strip()
        email = opts["email"].strip().lower()
        password = opts["password"]

        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": "Весы",
                "last_name": "Локально",
                "is_active": True,
                "is_staff": True,
                "can_view_products": True,
                "can_view_market_scales": True,
                "can_view_settings": True,
            },
        )
        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(f"Создан пользователь: {email} / {password}")
        else:
            user.set_password(password)
            user.can_view_market_scales = True
            user.can_view_products = True
            user.save(update_fields=["password", "can_view_market_scales", "can_view_products"])
            self.stdout.write(f"Пользователь есть: {email} / {password}")

        company, co_created = Company.objects.get_or_create(
            id=company_id,
            defaults={
                "name": "Локальный маркет (весы)",
                "slug": "local-scales-demo",
                "owner": user,
                "scale_api_token": token,
            },
        )
        if not co_created:
            company.scale_api_token = token
            company.name = company.name or "Локальный маркет (весы)"
            company.owner = company.owner or user
            company.save(update_fields=["scale_api_token", "name", "owner"])

        if user.company_id != company.id:
            user.company = company
            user.save(update_fields=["company"])

        created_products = 0
        for row in WEIGHT_PRODUCTS:
            _, created = Product.objects.update_or_create(
                company=company,
                plu=row["plu"],
                defaults={
                    "name": row["name"],
                    "price": Decimal(row["price"]),
                    "is_weight": True,
                    "kind": Product.Kind.PRODUCT,
                    "code": str(row["plu"]).zfill(4),
                    "unit": "кг",
                },
            )
            if created:
                created_products += 1

        self.stdout.write(self.style.SUCCESS(
            f"Компания {company.id}\n"
            f"  scale_api_token: {company.scale_api_token}\n"
            f"  весовых товаров: {Product.objects.filter(company=company, is_weight=True).count()} "
            f"(новых: {created_products})\n"
            f"Логин CRM: {email} / {password}"
        ))
