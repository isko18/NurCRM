from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting, SaleConsalting,
    SubscriptionConsalting, SubscriptionPaymentConsalting
)
from apps.consalting.funnel.completion import apply_completion_side_effects, create_sale_side_effects
from apps.consalting.tasks import process_subscription_schedules


class SubscriptionSystemTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@sub.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Sub Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Иван Иванов", phone="+77011234567"
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="CRM Бухгалтерия", price=Decimal("150000.00")
        )

        self.tariff_month = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Ежемесячный", price=Decimal("150000.00"),
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )

        self.tariff_year = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Годовой", price=Decimal("150000.00"),
            subscription_amount=Decimal("100000.00"), subscription_period="year"
        )

        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Продажи (Финал)", is_final=True, is_main=True
        )
        self.stage_won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Выиграно", order=1, stage_type="won"
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_sale_creates_subscription_and_schedule(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )

        sub = create_sale_side_effects(sale)
        self.assertIsNotNone(sub)
        self.assertEqual(sub.amount, Decimal("10000.00"))
        self.assertEqual(sub.period, "month")

        payments = sub.payments.all()
        self.assertEqual(payments.count(), 12)
        self.assertEqual(payments.first().amount, Decimal("10000.00"))

        # Idempotency test: calling create_sale_side_effects again returns same subscription
        sub2 = create_sale_side_effects(sale)
        self.assertEqual(sub.id, sub2.id)
        self.assertEqual(SubscriptionConsalting.objects.count(), 1)

    def test_yearly_subscription_schedule(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_year, client=self.client_entity,
            subscription_amount=Decimal("100000.00"), subscription_period="year"
        )

        sub = create_sale_side_effects(sale)
        payments = sub.payments.all()
        self.assertEqual(payments.count(), 3)
        self.assertEqual(payments.first().amount, Decimal("100000.00"))

    def test_subscription_payment_pay_endpoint(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        p1 = sub.payments.order_by("due_date").first()

        res = self.client.post(f"/api/consalting/subscription-payments/{p1.id}/pay/", {
            "payment_method": "transfer"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        p1.refresh_from_db()
        self.assertEqual(p1.status, "paid")
        self.assertIsNotNone(p1.paid_at)

        # Duplicate payment attempt returns 400
        res_dup = self.client.post(f"/api/consalting/subscription-payments/{p1.id}/pay/")
        self.assertEqual(res_dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_client_subscriptions_and_matrix_endpoints(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        create_sale_side_effects(sale)

        # Client subscriptions API
        res_cli = self.client.get(f"/api/consalting/clients/{self.client_entity.id}/subscriptions/")
        self.assertEqual(res_cli.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_cli.data["results"]), 1)
        self.assertEqual(res_cli.data["results"][0]["amount"], "10000.00")

        # Subscription Matrix API
        res_mat = self.client.get("/api/consalting/subscription-matrix/")
        self.assertEqual(res_mat.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res_mat.data["count"], 1)
        self.assertEqual(res_mat.data["rows"][0]["subscription_amount"], 10000.0)

    def test_process_subscription_schedules_task(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        p1 = sub.payments.order_by("due_date").first()
        p1.due_date = timezone.localdate() - timedelta(days=5)
        p1.save()

        msg = process_subscription_schedules()
        self.assertIn("overdue marked", msg)

        p1.refresh_from_db()
        self.assertEqual(p1.status, "overdue")
