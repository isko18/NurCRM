from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, SaleConsalting, CashRequestConsalting,
    CashOperationConsalting, CashConfirmationSettingsConsalting
)
from apps.consalting.funnel.completion import apply_completion_side_effects


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class CashConfirmationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@cashconfirm.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Cash Confirm Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp = User.objects.create(
            email="emp@cashconfirm.com", password="password123", company=self.company
        )

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Иван Кассовый"
        )
        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("50000.00")
        )

        self.settings = CashConfirmationSettingsConsalting.objects.create(
            company=self.company, mode="cash_only", skip_for_cashier=True
        )

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="Воронка", is_final=True)
        self.stage = FunnelStageConsalting.objects.create(company=self.company, funnel=self.funnel, name="Выиграно", stage_type="won")

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.emp)

    def test_cash_sale_creates_pending_request(self):
        lead = LeadConsalting.objects.create(
            company=self.company, owner=self.emp, client=self.client_entity,
            service=self.service, funnel=self.funnel, stage=self.stage,
            status=LeadConsalting.Status.WON, payment_mode="cash", title="Продажа за нал",
            estimated_value=Decimal("50000.00")
        )

        sale = apply_completion_side_effects(lead)
        self.assertIsNotNone(sale)
        self.assertEqual(sale.status, "pending_confirmation")

        # Check pending request created
        req = CashRequestConsalting.objects.filter(sale=sale, status="pending").first()
        self.assertIsNotNone(req)
        self.assertEqual(req.amount, Decimal("50000.00"))
        self.assertEqual(req.user, self.emp)

        # Counters view reflects pending_amount
        res_counters = self.mgr_client.get("/api/consalting/cashbox/requests/counters/")
        self.assertEqual(res_counters.status_code, status.HTTP_200_OK)
        self.assertEqual(res_counters.data["pending"], 1)
        self.assertEqual(res_counters.data["pending_amount"], 50000.0)

    def test_confirm_cash_request(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("30000.00"), status="pending_confirmation"
        )
        req = CashRequestConsalting.objects.create(
            company=self.company, sale=sale, user=self.emp, client=self.client_entity,
            kind="sale", direction="income", amount=Decimal("30000.00"),
            payment_method="cash", status="pending"
        )

        res = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/", {
            "comment": "Всё получено"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        req.refresh_from_db()
        self.assertEqual(req.status, "confirmed")

        sale.refresh_from_db()
        self.assertEqual(sale.status, "completed")

        op = CashOperationConsalting.objects.filter(user=self.emp, amount=Decimal("30000.00")).first()
        self.assertIsNotNone(op)
        self.assertEqual(op.user, self.emp)

        # Duplicate confirm -> 400
        res_dup = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/")
        self.assertEqual(res_dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_cash_request_validation(self):
        req = CashRequestConsalting.objects.create(
            company=self.company, user=self.emp, client=self.client_entity,
            kind="handover", direction="income", amount=Decimal("10000.00"), status="pending"
        )

        # Reject without reason -> 400
        res_bad = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/reject/", {})
        self.assertEqual(res_bad.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid reject
        res_ok = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/reject/", {
            "reason": "no_money", "comment": "Курьер не довёз"
        })
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)

        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(req.reject_reason, "no_money")

    def test_confirmation_settings_api(self):
        res_get = self.mgr_client.get("/api/consalting/cashbox/confirmation-settings/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_get.data["mode"], "cash_only")

        res_put = self.mgr_client.put("/api/consalting/cashbox/confirmation-settings/", {
            "mode": "required", "skip_for_cashier": False, "overdue_hours": 12
        })
        self.assertEqual(res_put.status_code, status.HTTP_200_OK)
        self.assertEqual(res_put.data["mode"], "required")
        self.assertFalse(res_put.data["skip_for_cashier"])
        self.assertEqual(res_put.data["overdue_hours"], 12)

        # POST also works as canon (§9.0 п.2)
        res_post = self.mgr_client.post("/api/consalting/cashbox/confirmation-settings/", {
            "mode": "off", "skip_for_cashier": True, "overdue_hours": 48
        })
        self.assertEqual(res_post.status_code, status.HTTP_200_OK)
        self.assertEqual(res_post.data["mode"], "off")
        self.assertTrue(res_post.data["skip_for_cashier"])
        self.assertEqual(res_post.data["overdue_hours"], 48)

    def test_skip_for_cashier_false_prevents_self_confirmation(self):
        """Если skip_for_cashier=False, автор не может подтвердить собственную заявку."""
        self.settings.skip_for_cashier = False
        self.settings.save()

        # Owner creates request for himself
        req = CashRequestConsalting.objects.create(
            company=self.company, user=self.owner, client=self.client_entity,
            kind="handover", direction="income", amount=Decimal("15000.00"), status="pending"
        )
        res = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cash_request_overdue_computed_flag(self):
        """Заявки старше overdue_hours получают is_overdue=True в API."""
        from datetime import timedelta
        req = CashRequestConsalting.objects.create(
            company=self.company, user=self.emp, client=self.client_entity,
            kind="handover", direction="income", amount=Decimal("5000.00"), status="pending"
        )
        req.created_at = timezone.now() - timedelta(hours=30)
        req.save()

        res = self.mgr_client.get("/api/consalting/cashbox/requests/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        req_item = next(r for r in res.data["results"] if r["id"] == str(req.id))
        self.assertTrue(req_item["is_overdue"])

    def test_consulting_cashboxes_api(self):
        """GET и POST /api/consalting/cashbox/cashboxes/ с аналитикой."""
        # 1. Создание кассы
        res = self.mgr_client.post("/api/consalting/cashbox/cashboxes/", {"name": "Касса Бишкек"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        cb_id = res.data["id"]

        # 2. Получение списка касс
        res_list = self.mgr_client.get("/api/consalting/cashbox/cashboxes/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        cb_item = next(c for c in res_list.data if c["id"] == cb_id)
        self.assertEqual(cb_item["name"], "Касса Бишкек")
        self.assertEqual(cb_item["balance"], 0.0)

        # 3. Детальная касса
        res_det = self.mgr_client.get(f"/api/consalting/cashbox/cashboxes/{cb_id}/")
        self.assertEqual(res_det.status_code, status.HTTP_200_OK)
        self.assertEqual(res_det.data["name"], "Касса Бишкек")

