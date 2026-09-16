from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting, SaleConsalting,
    SalaryAccrualConsalting, SalaryAdjustmentConsalting
)
from apps.consalting.funnel.completion import create_sale_side_effects


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class SaleCancelTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@salecancel.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Cancel Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp = User.objects.create(
            email="emp@salecancel.com", password="password123", company=self.company
        )

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Пётр Петров"
        )
        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("100000.00")
        )
        self.tariff = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Абонентка", price=Decimal("100000.00"),
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="Воронка", is_final=True)
        self.stage = FunnelStageConsalting.objects.create(company=self.company, funnel=self.funnel, name="Выиграно", stage_type="won")

        self.lead = LeadConsalting.objects.create(
            company=self.company, owner=self.emp, client=self.client_entity,
            funnel=self.funnel, stage=self.stage, status=LeadConsalting.Status.WON,
            title="Лид под отмену"
        )

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.emp)

    def test_full_sale_cancellation_and_side_effects(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity, lead=self.lead,
            total=Decimal("100000.00")
        )
        sub = create_sale_side_effects(sale)
        self.assertIsNotNone(sub)

        # Accrual
        accrual = SalaryAccrualConsalting.objects.create(
            company=self.company, user=self.emp, sale=sale,
            kind=SalaryAccrualConsalting.Kind.PERCENT,
            period_month=timezone.localdate().strftime("%Y-%m"),
            amount=Decimal("10000.00"), status="accrued"
        )

        # Cancel sale as manager
        res = self.mgr_client.post(f"/api/consalting/sales/{sale.id}/cancel/", {
            "reason": "client_refused",
            "comment": "Клиент передумал",
            "refund_mode": "cash",
            "lead_action": "return_to_work"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sale.refresh_from_db()
        self.assertEqual(sale.status, "canceled")
        self.assertEqual(sale.cancel_reason, "client_refused")

        sub.refresh_from_db()
        self.assertEqual(sub.status, "canceled")

        accrual.refresh_from_db()
        self.assertEqual(accrual.status, "canceled")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "in_work")

        # Duplicate cancellation attempt returns 400
        res_dup = self.mgr_client.post(f"/api/consalting/sales/{sale.id}/cancel/", {
            "reason": "duplicate"
        })
        self.assertEqual(res_dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_paid_accrual_creates_deduction_on_cancel(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("50000.00")
        )
        accrual = SalaryAccrualConsalting.objects.create(
            company=self.company, user=self.emp, sale=sale,
            kind=SalaryAccrualConsalting.Kind.PERCENT,
            period_month=timezone.localdate().strftime("%Y-%m"),
            amount=Decimal("5000.00"), status="paid"
        )

        res = self.mgr_client.post(f"/api/consalting/sales/{sale.id}/cancel/", {
            "reason": "input_error"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        adj = SalaryAdjustmentConsalting.objects.filter(
            user=self.emp, reason=SalaryAdjustmentConsalting.Reason.SALE_CANCELED
        ).first()
        self.assertIsNotNone(adj)
        self.assertEqual(adj.amount, Decimal("5000.00"))

    def test_partial_refund(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("100000.00")
        )
        res = self.mgr_client.post(f"/api/consalting/sales/{sale.id}/refund/", {
            "amount": 20000,
            "reason": "warranty",
            "refund_mode": "cash"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sale.refresh_from_db()
        self.assertEqual(sale.status, "refunded")
        self.assertEqual(sale.refunded_amount, Decimal("20000.00"))

    def test_cancel_permissions_and_time_window(self):
        # Sale created over 30 mins ago
        sale_old = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("30000.00")
        )
        sale_old.created_at = timezone.now() - timedelta(minutes=45)
        sale_old.save()

        # Regular employee gets 403 when trying to cancel old sale
        res = self.emp_client.post(f"/api/consalting/sales/{sale_old.id}/cancel/", {
            "reason": "client_refused"
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_canceled_sale_cannot_be_edited(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            client=self.client_entity, total=Decimal("40000.00"),
            status=SaleConsalting.Status.CANCELED
        )
        res = self.mgr_client.patch(f"/api/consalting/sales/{sale.id}/", {"description": "Новое описание"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancellations_report_endpoint(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("60000.00")
        )
        self.mgr_client.post(f"/api/consalting/sales/{sale.id}/cancel/", {
            "reason": "client_refused",
            "comment": "Отказ"
        })

        res = self.mgr_client.get("/api/consalting/sales/cancellations/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data["count"], 1)
        item = next(r for r in res.data["results"] if r["id"] == str(sale.id))
        self.assertEqual(item["reason"], "client_refused")
        self.assertEqual(item["total"], 60000.0)

    def test_refund_amount_exceeding_total_fails(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("50000.00")
        )
        res = self.mgr_client.post(f"/api/consalting/sales/{sale.id}/refund/", {
            "amount": 60000,
            "reason": "warranty"
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("больше остатка", res.data["detail"])

