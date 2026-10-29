"""Экран «Магазин в приложении» (ТЗ Умара 06.10): поиск по адресу, права, ошибки, процент для кассы."""
from decimal import Decimal
from unittest import mock

from rest_framework.test import APIClient

from apps.clientapp.models import AppShopSettings
from apps.clientapp.tests import AppTestMixin
from apps.main.tests_kassa_api import KassaBase
from apps.users.models import Branch, User

S = "/api/main/app-shop-settings/"


class ShopScreenTests(AppTestMixin, KassaBase):
    def test_geocode_now(self):
        with mock.patch("apps.clientapp.geocode.geocode_query",
                        return_value=(Decimal("42.874600"), Decimal("74.569800"))):
            r = self.api.post(S + "geocode/", {"address": "Бишкек, Чуй 100"}, format="json")
        self.assertEqual(r.data, {"found": True, "latitude": "42.874600", "longitude": "74.569800"})
        with mock.patch("apps.clientapp.geocode.geocode_query", return_value=None):
            self.assertEqual(self.api.post(S + "geocode/", {"address": "нет"}, format="json").data, {"found": False})
        with mock.patch("apps.clientapp.geocode.geocode_query", side_effect=RuntimeError("busy")):
            r = self.api.post(S + "geocode/", {"address": "x"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (503, "geocoder_busy"))
        r = self.api.post(S + "geocode/", {}, format="json")
        self.assertEqual((r.status_code, r.data), (400, {"address": ["Укажите адрес."]}))

    def test_only_owner_and_admin(self):
        rop = User.objects.create_user(email="rop@t.kg", password="x", company=self.company, role="rop")
        for u in (self.cashier, rop):
            api = APIClient()
            api.force_authenticate(u)
            self.assertEqual(api.get(S).status_code, 403)
            self.assertEqual(api.patch(S, {"show_in_app": True}, format="json").status_code, 403)
        admin = User.objects.create_user(email="adm@t.kg", password="x", company=self.company, role="admin")
        api = APIClient()
        api.force_authenticate(admin)
        self.assertEqual(api.get(S).status_code, 200)

    def test_field_errors_shape(self):
        b = Branch.objects.create(company=self.company, name="Центр", address="Чуй 1")
        r = self.api.patch(S, {"points_percent": "150"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(list(r.data), ["points_percent"])
        self.assertIsInstance(r.data["points_percent"], list)
        r = self.api.patch(S, {"branches": [{"branch_id": str(b.id), "latitude": "100", "longitude": "74"}]},
                           format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("latitude", r.data["branches"][str(b.id)])
        self.assertEqual(self.api.patch(S, {"points_percent": "2.5"}, format="json").data["points_percent"], "2.50")

    def test_points_for_kassa(self):
        b = Branch.objects.create(company=self.company, name="Юг", address="Ахунбаева 5")
        AppShopSettings.objects.create(company=self.company, points_enabled=True, points_percent=Decimal("5"))
        AppShopSettings.objects.create(company=self.company, branch=b, points_percent=Decimal("3"))
        cashier = APIClient()
        cashier.force_authenticate(self.cashier)
        r = cashier.get(S + "points/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["points_enabled"], r.data["points_percent"], r.data["source"]), (True, "5.00", "company"))
        r = cashier.get(S + f"points/?branch={b.id}")
        self.assertEqual((r.data["points_percent"], r.data["source"]), ("3.00", "branch"))
