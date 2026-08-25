from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.construction.models import Cashbox
from apps.main.models import Branch, PosPrinterSetting
from apps.main.pos_views import PosPrinterSettingAPIView
from apps.users.models import Company, SubscriptionPlan

User = get_user_model()


class PosPrinterSettingAPITests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_pr_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Printer Test Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashbox = Cashbox.objects.create(name="Main Cashbox", company=self.company, branch=self.branch)

    def test_get_nonexistent_returns_404(self):
        req = self.factory.get("/api/main/pos/printer-settings/?device_key=unknown-dev")
        force_authenticate(req, user=self.owner)
        view = PosPrinterSettingAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 404)

    def test_save_and_retrieve_printer_setting(self):
        payload = {
            "device_key": "cashier-front-1",
            "settings": {
                "escpos_dpl": "384",
                "escpos_cpl": "42",
                "escpos_font": "B",
                "escpos_drawer_enabled": "1",
                "escpos_drawer_when": "after",
                "escpos_drawer_cmd": "27, 112, 0, 25, 250",
            },
            "branch": str(self.branch.id),
            "cashbox": str(self.cashbox.id),
        }
        req = self.factory.post("/api/main/pos/printer-settings/", payload, format="json")
        force_authenticate(req, user=self.owner)
        view = PosPrinterSettingAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["device_key"], "cashier-front-1")
        self.assertEqual(resp.data["settings"]["escpos_cpl"], "42")
        self.assertEqual(resp.data["branch"], self.branch.id)
        self.assertEqual(resp.data["cashbox"], self.cashbox.id)

        # GET by device_key
        req_get = self.factory.get("/api/main/pos/printer-settings/?device_key=cashier-front-1")
        force_authenticate(req_get, user=self.owner)
        resp_get = view(req_get)
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(resp_get.data["device_key"], "cashier-front-1")
        self.assertEqual(resp_get.data["settings"]["escpos_dpl"], "384")

    def test_update_existing_setting(self):
        # Create initial
        PosPrinterSetting.objects.create(
            company=self.company,
            branch=self.branch,
            device_key="pos-pc-2",
            settings={"escpos_cpl": "32"},
        )

        update_payload = {
            "device_key": "pos-pc-2",
            "settings": {"escpos_cpl": "48", "escpos_font": "A"},
        }
        req = self.factory.put("/api/main/pos/printer-settings/", update_payload, format="json")
        force_authenticate(req, user=self.owner)
        view = PosPrinterSettingAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["settings"]["escpos_cpl"], "48")
        self.assertEqual(resp.data["settings"]["escpos_font"], "A")

    def test_list_all_printer_settings(self):
        PosPrinterSetting.objects.create(
            company=self.company,
            branch=self.branch,
            device_key="dev-1",
            settings={"k": "v1"},
        )
        PosPrinterSetting.objects.create(
            company=self.company,
            branch=self.branch,
            device_key="dev-2",
            settings={"k": "v2"},
        )

        req = self.factory.get("/api/main/pos/printer-settings/")
        force_authenticate(req, user=self.owner)
        view = PosPrinterSettingAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        keys = [item["device_key"] for item in resp.data]
        self.assertIn("dev-1", keys)
        self.assertIn("dev-2", keys)

    def test_delete_printer_setting(self):
        PosPrinterSetting.objects.create(
            company=self.company,
            branch=self.branch,
            device_key="dev-to-del",
            settings={},
        )
        req = self.factory.delete("/api/main/pos/printer-settings/?device_key=dev-to-del")
        force_authenticate(req, user=self.owner)
        view = PosPrinterSettingAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(PosPrinterSetting.objects.filter(company=self.company, device_key="dev-to-del").exists())

    def test_validation_errors(self):
        view = PosPrinterSettingAPIView.as_view()

        # Missing device_key
        req = self.factory.post("/api/main/pos/printer-settings/", {"settings": {}}, format="json")
        force_authenticate(req, user=self.owner)
        resp = view(req)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("device_key", resp.data)

        # Invalid settings type (not a dict)
        req2 = self.factory.post("/api/main/pos/printer-settings/", {"device_key": "d1", "settings": "string"}, format="json")
        force_authenticate(req2, user=self.owner)
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("settings", resp2.data)
