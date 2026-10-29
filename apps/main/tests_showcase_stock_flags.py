from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.main.models import Product
from apps.users.models import Company

User = get_user_model()


class PublicShowcaseStockFlagsTests(TestCase):
    """Открытая витрина: вместо точного остатка — in_stock / low_stock (≤ 5)."""

    def setUp(self):
        self.client = APIClient()
        owner = User.objects.create_user(email="stockflags@test.com", password="pass12345")
        self.company = Company.objects.create(name="Flags", slug="flags-shop", owner=owner)
        for name, qty in (("Много", "50"), ("Мало", "3"), ("Нет", "0")):
            Product.objects.create(company=self.company, name=name, price=Decimal("10"), quantity=Decimal(qty))

    def test_list_hides_quantity_and_returns_flags(self):
        resp = self.client.get("/api/main/public/companies/flags-shop/showcase/", secure=True)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        rows = {r["name"]: r for r in resp.json()["results"]}
        for r in rows.values():
            self.assertNotIn("quantity", r)
        self.assertEqual((rows["Много"]["in_stock"], rows["Много"]["low_stock"]), (True, False))
        self.assertEqual((rows["Мало"]["in_stock"], rows["Мало"]["low_stock"]), (True, True))
        self.assertEqual((rows["Нет"]["in_stock"], rows["Нет"]["low_stock"]), (False, False))
