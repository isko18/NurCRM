from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.construction.models import CashFlow, Cashbox
from apps.main.analytics_market import AnalyticsView, Period
from apps.main.models import Branch, Company, User


class AnalyticsCashflowSourceKindTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@cashflow.test",
            password="demo123",
            first_name="Owner",
            last_name="User",
            role="owner",
        )
        self.company = Company.objects.create(name="Test Company", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.user = User.objects.create_user(
            email="cashflow@example.com",
            password="demo123",
            first_name="Cash",
            last_name="Flow",
            company=self.company,
            role="cashier",
        )
        self.cashbox = Cashbox.objects.create(company=self.company, branch=self.branch, name="Main Cashbox")

        now = timezone.now()
        self.period = Period(start=now - timedelta(days=1), end=now + timedelta(days=1))
        wsgi_request = APIRequestFactory().get("/analytics/market/?tab=cashflow")
        self.request = Request(wsgi_request)
        self.request.user = self.user

    def test_cashflow_counts_debt_repayment_and_excludes_sale_like_income_from_other_income(self):
        CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            type=CashFlow.Type.INCOME,
            status=CashFlow.Status.APPROVED,
            amount=Decimal("111.11"),
            name="Погашение долга",
            source_kind=CashFlow.SourceKind.DEBT_REPAYMENT,
            source_id="debt-1",
        )
        CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            type=CashFlow.Type.INCOME,
            status=CashFlow.Status.APPROVED,
            amount=Decimal("88.00"),
            name="Предоплата",
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id="prepay-1",
        )

        result = AnalyticsView()._cashflow(self.request, self.company, self.branch, self.period)

        self.assertEqual(result["inflow"]["debt_repayments"], "111.11")
        self.assertEqual(result["inflow"]["other_income"], "0.00")
        # Предоплата по продаже в долг — это деньги от продаж (раньше терялась)
        self.assertEqual(result["inflow"]["sales_cash"], "88.00")
        self.assertEqual(result["inflow"]["total"], "199.11")

    def test_cashflow_dedupes_legacy_sale_rows_and_raw_cashbox_duplicates(self):
        CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            type=CashFlow.Type.INCOME,
            status=CashFlow.Status.APPROVED,
            amount=Decimal("1500.00"),
            name="Продажа",
            source_kind="pos_sale",
            source_id="sale-42",
        )
        # Legacy-строка source_kind='sale' есть в прод-БД, но уже не входит в choices:
        # CashFlow.save() вызывает full_clean(), поэтому вставляем в обход валидации,
        # как она и лежит в базе.
        CashFlow.objects.bulk_create([
            CashFlow(
                company=self.company,
                branch=self.branch,
                cashbox=self.cashbox,
                type=CashFlow.Type.INCOME,
                status=CashFlow.Status.APPROVED,
                amount=Decimal("1500.00"),
                name="Продажа дубль",
                source_kind="sale",
                source_id="sale-42",
            )
        ])
        CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            type=CashFlow.Type.INCOME,
            status=CashFlow.Status.APPROVED,
            amount=Decimal("1500.00"),
            name="Сырая запись кассы",
            source_kind=None,
            source_id=None,
            source_cashbox_flow_id="sale-42",
        )

        finance = AnalyticsView()._finance(self.request, self.company, self.branch, self.period)
        cashflow = AnalyticsView()._cashflow(self.request, self.company, self.branch, self.period)

        self.assertEqual(finance["cards"]["income_total"], "1500.00")
        self.assertEqual(cashflow["inflow"]["other_income"], "0.00")
        # Денежный поток строится по CashFlow: продажа учтена один раз
        self.assertEqual(cashflow["inflow"]["sales_cash"], "1500.00")
        self.assertEqual(cashflow["inflow"]["total"], "1500.00")
