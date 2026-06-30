"""Отправить весовые товары из локальной БД на агент (без фронта)."""
from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import User
from apps.users.scale_views import send_products_to_scale


class Command(BaseCommand):
    help = "Локально: read Product is_weight=True из БД → WebSocket plu_batch → агент."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="scales-local@local.test")
        parser.add_argument("--plu-start", type=int, default=1)

    def handle(self, *args, **opts):
        email = opts["email"].strip().lower()
        try:
            user = User.objects.select_related("company", "owned_company").get(email=email)
        except User.DoesNotExist as exc:
            raise CommandError(
                f"Пользователь {email} не найден. Сначала: python manage.py seed_local_scale_demo"
            ) from exc

        factory = APIRequestFactory()
        request = factory.post(
            "/api/users/scales/send-products/",
            {"plu_start": opts["plu_start"]},
            format="json",
        )
        force_authenticate(request, user=user)
        response = send_products_to_scale(request)

        if response.status_code >= 400:
            raise CommandError(f"Ошибка {response.status_code}: {response.data}")

        data = response.data
        self.stdout.write(self.style.SUCCESS(f"Отправлено на WebSocket: {data.get('sent')} товаров"))
        for item in data.get("items") or []:
            self.stdout.write(
                f"  ПЛУ {item['plu_number']:02d} — {item.get('name')} — {item.get('price')} сом"
            )
