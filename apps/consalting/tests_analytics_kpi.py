from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company, Branch
from apps.main.models import Client
from apps.consalting.models import (
    ServicesConsalting, TariffConsalting, SaleConsalting,
    CashRequestConsalting, CashOperationConsalting, SaleRefundConsalting,
    SubscriptionConsalting,
)
from apps.consalting.funnel.completion import create_sale_side_effects
from apps.consalting.funnel.cash_confirmation import confirm_request
from apps.consalting.funnel.analytics import SalesAnalytics
from apps.consalting.funnel.analytics_ops import DashboardAnalytics


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class AnalyticsKPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@analyticskpi.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Analytics KPI Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp = User.objects.create(
            email="emp@analyticskpi.com", password="password123", company=self.company
        )

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Иван Аналитиков"
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("100000.00")
        )

        self.tariff = TariffConsalting.objects.create(
            company=self.company,
            service=self.service,
            name="Пакет Аналитика",
            price=Decimal("100000.00"),
            subscription_amount=Decimal("12000.00"),
            subscription_period="year"  # year -> 1000 MRR
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_revenue_vs_net_revenue_and_cancellations(self):
        """Проверяет revenue, net_revenue, cancellations и cancel_rate."""
        today = timezone.localdate()

        # 1. Завершенная продажа на 100,000 с частичным возвратом 10,000
        sale1 = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity,
            total=Decimal("100000.00"), refunded_amount=Decimal("10000.00"),
            status="completed"
        )
        SaleRefundConsalting.objects.create(
            company=self.company, sale=sale1, amount=Decimal("10000.00"),
            reason=SaleConsalting.CancelReason.CLIENT_REFUSED,
            created_by=self.owner,
        )

        # 2. Отмененная продажа на 50,000
        sale2 = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity,
            total=Decimal("50000.00"),
            status="canceled"
        )

        res = SalesAnalytics.compute(self.company, date_from=today, date_to=today)
        kpis = res["kpis"]

        # gross revenue = 100,000
        self.assertEqual(kpis["revenue"], 100000.0)
        # cancellations = 10,000 (refund) + 50,000 (canceled) = 60,000
        self.assertEqual(kpis["cancellations"], 60000.0)
        # net_revenue = 100,000 - 10,000 = 90,000
        self.assertEqual(kpis["net_revenue"], 90000.0)
        # cancel_rate = 60,000 / 100,000 * 100 = 60.0% (§9.0 п.5 канона)
        self.assertEqual(kpis["cancel_rate"], 60.0)
        # subscription_mrr = 12000 / 12 = 1000.0
        self.assertEqual(kpis["subscription_mrr"], 1000.0)

    def test_pending_sale_is_excluded_from_revenue_and_sales_count(self):
        today = timezone.localdate()
        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("50000.00"), status="completed",
        )
        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("99999.00"), status="pending_confirmation",
        )

        kpis = SalesAnalytics.compute(self.company, date_from=today, date_to=today)["kpis"]
        self.assertEqual(kpis["revenue"], 50000.0)
        self.assertEqual(kpis["sales_count"], 1)

    def test_refunds_are_reported_on_refund_date_and_daily_net_revenue_reconciles(self):
        today = timezone.localdate()
        historical_sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("100000.00"), status="refunded",
        )
        SaleConsalting.objects.filter(pk=historical_sale.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=10)
        )
        SaleRefundConsalting.objects.create(
            company=self.company, sale=historical_sale, amount=Decimal("15000.00"),
            reason=SaleConsalting.CancelReason.CLIENT_REFUSED, created_by=self.owner,
        )

        current_sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("100000.00"), status="completed",
        )
        SaleRefundConsalting.objects.create(
            company=self.company, sale=current_sale, amount=Decimal("10000.00"),
            reason=SaleConsalting.CancelReason.CLIENT_REFUSED, created_by=self.owner,
        )

        data = SalesAnalytics.compute(self.company, date_from=today, date_to=today)
        self.assertEqual(data["kpis"]["revenue"], 100000.0)
        self.assertEqual(data["kpis"]["cancellations"], 25000.0)
        self.assertEqual(data["kpis"]["net_revenue"], 75000.0)
        self.assertEqual(sum(row["revenue"] for row in data["by_day"]), 75000.0)

    def test_cancellations_are_reported_on_cancellation_date(self):
        today = timezone.localdate()
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("50000.00"), status="completed",
        )
        SaleConsalting.objects.filter(pk=sale.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=10),
            status=SaleConsalting.Status.CANCELED,
            canceled_at=timezone.now(),
        )

        kpis = SalesAnalytics.compute(self.company, date_from=today, date_to=today)["kpis"]
        self.assertEqual(kpis["cancellations"], 50000.0)

    def test_repeat_clients_use_purchase_history_before_period(self):
        today = timezone.localdate()
        old_sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("1000.00"), status="completed",
        )
        SaleConsalting.objects.filter(pk=old_sale.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=10)
        )
        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("1000.00"), status="completed",
        )

        kpis = SalesAnalytics.compute(self.company, date_from=today, date_to=today)["kpis"]
        self.assertEqual(kpis["unique_clients"], 1)
        self.assertEqual(kpis["repeat_clients"], 1)

    def test_mrr_uses_branch_and_end_of_selected_period_as_snapshot(self):
        branch_one = Branch.objects.create(company=self.company, name="Первый")
        branch_two = Branch.objects.create(company=self.company, name="Второй")
        sale_one = SaleConsalting.objects.create(
            company=self.company, branch=branch_one, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("0"), status="completed",
        )
        sale_two = SaleConsalting.objects.create(
            company=self.company, branch=branch_two, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("0"), status="completed",
        )
        SubscriptionConsalting.objects.create(
            company=self.company, client=self.client_entity, service=self.service, sale=sale_one,
            amount=Decimal("1000.00"), period="month", start_date=timezone.localdate(), status="active",
        )
        SubscriptionConsalting.objects.create(
            company=self.company, client=self.client_entity, service=self.service, sale=sale_two,
            amount=Decimal("2000.00"), period="month", start_date=timezone.localdate(), status="active",
        )

        today = timezone.localdate()
        current = SalesAnalytics.compute(self.company, date_from=today, date_to=today, branch=branch_one.id)["kpis"]
        before_start = SalesAnalytics.compute(self.company, date_to=today - timezone.timedelta(days=1), branch=branch_one.id)["kpis"]
        self.assertEqual(current["subscription_mrr"], 1000.0)
        self.assertEqual(current["subscription_mrr_as_of"], today.isoformat())
        self.assertEqual(before_start["subscription_mrr"], 0.0)

    def test_pending_cash_and_paid_income_separation(self):
        """Pending cash заявки не входит в paid_income до confirm, а после confirm переходит в факт."""
        today = timezone.localdate()

        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            client=self.client_entity, total=Decimal("70000.00"), status="pending_confirmation"
        )
        req = CashRequestConsalting.objects.create(
            company=self.company, sale=sale, user=self.emp, client=self.client_entity,
            kind="sale", direction="income", amount=Decimal("70000.00"),
            payment_method="cash", status="pending"
        )

        # До confirm: pending_cash = 70000, paid_income = 0
        res1 = SalesAnalytics.compute(self.company, date_from=today, date_to=today)
        self.assertEqual(res1["kpis"]["pending_cash"], 70000.0)
        self.assertEqual(res1["kpis"]["paid_income"], 0.0)

        # Подтверждаем заявку
        confirm_request(req, user=self.owner)

        # После confirm: pending_cash = 0, paid_income = 70000
        res2 = SalesAnalytics.compute(self.company, date_from=today, date_to=today)
        self.assertEqual(res2["kpis"]["pending_cash"], 0.0)
        self.assertEqual(res2["kpis"]["paid_income"], 70000.0)

    def test_dashboard_analytics_api_endpoint(self):
        """GET /api/consalting/analytics/dashboard/ возвращает все KPI со структурой delta."""
        today = timezone.localdate()

        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity,
            total=Decimal("100000.00"), status="completed"
        )

        res = self.client.get(f"/api/consalting/analytics/dashboard/?date_from={today}&date_to={today}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        kpis = res.data["kpis"]
        for key in ["revenue", "net_revenue", "cancellations", "cancel_rate", "paid_income", "pending_cash", "subscription_mrr", "sales_count", "avg_check"]:
            self.assertIn(key, kpis)
            self.assertIn("current", kpis[key])
            self.assertIn("previous", kpis[key])
            self.assertIn("diff", kpis[key])
            self.assertIn("percent", kpis[key])

        self.assertEqual(kpis["revenue"]["current"], 100000.0)
        self.assertEqual(kpis["net_revenue"]["current"], 100000.0)
        self.assertEqual(kpis["subscription_mrr"]["current"], 1000.0)
