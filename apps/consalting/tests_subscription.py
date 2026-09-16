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
    SubscriptionConsalting, SubscriptionPaymentConsalting
)
from apps.consalting.funnel.completion import apply_completion_side_effects, create_sale_side_effects
from apps.consalting.tasks import process_subscription_schedules


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
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
        self.assertEqual(SubscriptionConsalting.objects.filter(sale=sale).count(), 1)

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

    def test_post_sales_creates_subscription_and_schedule(self):
        """POST /api/consalting/sales/ с тарифом абонентки создает Subscription и 12 платежей."""
        url = "/api/consalting/sales/"
        payload = {
            "services": str(self.service.id),
            "tariff": str(self.tariff_month.id),
            "client": str(self.client_entity.id),
            "total": "150000.00",
            "subscription_amount": "10000.00",
            "subscription_period": "month",
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sale_id = res.data["id"]
        sub = SubscriptionConsalting.objects.filter(sale_id=sale_id).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.amount, Decimal("10000.00"))
        self.assertEqual(sub.payments.count(), 12)

    def test_lead_register_payment_creates_subscription(self):
        """POST /api/consalting/leads/{id}/register-payment/ создает Subscription с параметрами из запроса."""
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=self.tariff_month,
            title="Лид на абонентку",
            estimated_value=Decimal("150000.00")
        )

        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "transfer",
            "amount": "150000.00",
            "subscription_enabled": True,
            "subscription_amount": "12000.00",
            "subscription_period": "month",
            "subscription_start": "2026-10-01"
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.amount, Decimal("12000.00"))
        self.assertEqual(str(sub.start_date), "2026-10-01")
        self.assertEqual(sub.payments.count(), 12)
        p1 = sub.payments.order_by("due_date").first()
        self.assertEqual(p1.period_month, "2026-10")

    def test_batch_pay_periods_endpoint(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)

        # Pay 3 periods forward
        res = self.client.post(f"/api/consalting/subscriptions/{sub.id}/pay-periods/", {
            "count": 3,
            "payment_method": "transfer",
            "note": "Оплата за 3 месяца вперед"
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["periods_count"], 3)
        self.assertEqual(res.data["amount"], 30000.0)

        sub.refresh_from_db()
        self.assertIsNotNone(sub.paid_through)
        paid_payments = sub.payments.filter(status="paid")
        self.assertEqual(paid_payments.count(), 3)
        self.assertEqual(paid_payments.last().due_date, sub.paid_through)
        for p in paid_payments:
            self.assertEqual(p.paid_via, "batch_pay")

    def test_scenario_a_tariff_with_prepaid_periods(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=self.tariff_month,
            title="Лид тариф предоплата",
            estimated_value=Decimal("150000.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "cash",
            "amount": "150000.00",
            "subscription_enabled": True,
            "subscription_prepaid_periods": 3,
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sub)
        self.assertTrue(sub.autorenew)
        paid_p = sub.payments.filter(status="paid")
        self.assertEqual(paid_p.count(), 3)
        for p in paid_p:
            self.assertEqual(p.paid_via, "lead_prepayment")
        self.assertEqual(sub.paid_through, paid_p.last().due_date)

        # Remaining 9 payments are planned
        planned_p = sub.payments.filter(status="planned")
        self.assertEqual(planned_p.count(), 9)

        # Appears in matrix
        res_mat = self.client.get("/api/consalting/subscription-matrix/")
        self.assertTrue(any(row["subscription_id"] == str(sub.id) for row in res_mat.data["rows"]))

    def test_scenario_b_manual_subscription_without_tariff(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=None,
            title="Лид без тарифа подписка",
            estimated_value=Decimal("50000.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "transfer",
            "amount": "50000.00",
            "subscription_enabled": True,
            "subscription_amount": "7500.00",
            "subscription_period": "month",
            "subscription_prepaid_periods": 2,
            "subscription_autorenew": True,
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.amount, Decimal("7500.00"))
        self.assertIsNone(sub.tariff)
        self.assertTrue(sub.autorenew)
        self.assertEqual(sub.payments.count(), 12)
        self.assertEqual(sub.payments.filter(status="paid").count(), 2)

        # Appears in matrix
        res_mat = self.client.get("/api/consalting/subscription-matrix/")
        self.assertTrue(any(row["subscription_id"] == str(sub.id) for row in res_mat.data["rows"]))

    def test_scenario_c_fixed_schedule_without_autorenew(self):
        """Сценарий C: фиксированный график ровно на N периодов, все paid, без матрицы, без автопродления."""
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=None,
            title="Лид фикс график на 5 месяцев",
            estimated_value=Decimal("101200.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "cash",
            "amount": "100000.00",
            "paid_months": 5,
            "subscription_enabled": True,
            "subscription_amount": "20000.00",
            "subscription_period": "month",
            "subscription_prepaid_periods": 5,
            "subscription_autorenew": False,
            "items": [
                {"name": "Умные весы", "price": 1200, "quantity": 1}
            ]
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sale = SaleConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.paid_months, 5)
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.items.first().name, "Умные весы")

        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sub)
        self.assertFalse(sub.autorenew)
        # Exactly 5 payments, all paid, no planned
        self.assertEqual(sub.payments.count(), 5)
        self.assertEqual(sub.payments.filter(status="paid").count(), 5)
        self.assertEqual(sub.payments.filter(status="planned").count(), 0)
        self.assertEqual(sub.paid_through, sub.payments.order_by("due_date").last().due_date)

        # NOT in matrix (§5.6)
        res_mat = self.client.get("/api/consalting/subscription-matrix/")
        self.assertFalse(any(row["subscription_id"] == str(sub.id) for row in res_mat.data["rows"]))

        # IS in client card (§5.6)
        res_cli = self.client.get(f"/api/consalting/clients/{self.client_entity.id}/subscriptions/")
        self.assertEqual(res_cli.status_code, status.HTTP_200_OK)
        found_in_card = any(s["id"] == str(sub.id) for s in res_cli.data["results"])
        self.assertTrue(found_in_card)

    def test_scenario_c_when_n_greater_than_one_without_checkbox(self):
        """Требование продукта: график появляется всегда когда N > 1, даже при subscription_enabled=False."""
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=None,
            title="Лид N>1 без галочки",
            estimated_value=Decimal("60000.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "cash",
            "amount": "60000.00",
            "paid_months": 3,
            "subscription_enabled": False,
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sub)
        self.assertFalse(sub.autorenew)
        self.assertEqual(sub.payments.count(), 3)
        self.assertEqual(sub.payments.filter(status="paid").count(), 3)
        self.assertEqual(sub.amount, Decimal("20000.00"))  # 60000 / 3

        # Visible in client card
        res_cli = self.client.get(f"/api/consalting/clients/{self.client_entity.id}/subscriptions/")
        self.assertTrue(any(s["id"] == str(sub.id) for s in res_cli.data["results"]))

    def test_single_period_without_subscription_creates_no_subscription(self):
        """При N=1 и subscription_enabled=False подписка не создаётся."""
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=None,
            title="Обычный разовый лид",
            estimated_value=Decimal("10000.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "cash",
            "amount": "10000.00",
            "paid_months": 1,
            "subscription_enabled": False,
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(SubscriptionConsalting.objects.filter(lead=lead).first())

    def test_repeated_lead_payment_is_idempotent(self):
        """Повторный register-payment не дублирует подписку и платежи."""
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            client=self.client_entity,
            service=self.service,
            tariff=self.tariff_month,
            title="Лид идемпотентность",
            estimated_value=Decimal("150000.00")
        )
        url = f"/api/consalting/leads/{lead.id}/register-payment/"
        payload = {
            "payment_mode": "cash",
            "amount": "150000.00",
            "subscription_enabled": True,
            "subscription_prepaid_periods": 2,
        }
        res1 = self.client.post(url, payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = self.client.post(url, payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        self.assertEqual(SubscriptionConsalting.objects.filter(lead=lead).count(), 1)
        sub = SubscriptionConsalting.objects.filter(lead=lead).first()
        self.assertEqual(sub.payments.count(), 12)
        self.assertEqual(sub.payments.filter(status="paid").count(), 2)

    def test_sale_cancel_cancels_future_subscription_payments(self):
        from apps.consalting.funnel.sale_cancel import cancel_sale
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        p1 = sub.payments.first()
        p1.status = "paid"
        p1.paid_at = timezone.now()
        p1.save()

        cancel_sale(sale, user=self.owner, reason="Клиент передумал", comment="Тест отмены", refund_mode="cash")

        sub.refresh_from_db()
        self.assertEqual(sub.status, "canceled")
        p1.refresh_from_db()
        self.assertEqual(p1.status, "paid")
        future_payments = sub.payments.exclude(id=p1.id)
        for p in future_payments:
            self.assertEqual(p.status, "canceled")

    def test_extend_subscription_appends_periods_after_current_tail(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        old_last = sub.payments.order_by("-due_date").first()

        response = self.client.post(
            f"/api/consalting/subscriptions/{sub.id}/extend/",
            {"periods": 3, "amount": "12500.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertEqual(sub.amount, Decimal("12500.00"))
        self.assertEqual(sub.payments.count(), 15)
        new_rows = sub.payments.filter(due_date__gt=old_last.due_date)
        self.assertEqual(new_rows.count(), 3)
        self.assertTrue(all(p.amount == Decimal("12500.00") for p in new_rows))

    def test_update_subscription_amount_keeps_paid_history(self):
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            tariff=self.tariff_month, client=self.client_entity,
            subscription_amount=Decimal("10000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        paid = sub.payments.order_by("due_date").first()
        paid.status = SubscriptionPaymentConsalting.Status.PAID
        paid.paid_at = timezone.now()
        paid.save(update_fields=["status", "paid_at"])

        response = self.client.patch(
            f"/api/consalting/subscriptions/{sub.id}/",
            {"amount": "15000.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        paid.refresh_from_db()
        self.assertEqual(paid.amount, Decimal("10000.00"))
        self.assertTrue(all(
            payment.amount == Decimal("15000.00")
            for payment in sub.payments.exclude(status=SubscriptionPaymentConsalting.Status.PAID)
        ))
