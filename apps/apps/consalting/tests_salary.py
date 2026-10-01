from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    ServicesConsalting, SaleConsalting, ServiceSalaryRateConsalting,
    SalaryAccrualConsalting, SalaryPayoutConsalting, SalarySchemeConsalting,
    SalarySchemeServiceOverrideConsalting, SalaryDefaultsConsalting,
    BonusRuleConsalting, BonusTierConsalting, SalaryAdjustmentConsalting
)
from apps.consalting.funnel.completion import accrue_salary_for_sale, resolve_rate


class SalarySystemTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@salary.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Salary Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.employee = User.objects.create(
            email="emp@salary.com", password="password123", company=self.company
        )
        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("10000.00")
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_rate_resolution_priority(self):
        # 1. Company default
        SalaryDefaultsConsalting.objects.create(
            company=self.company, percent=Decimal("5.00"), fixed_amount=Decimal("100.00")
        )
        pct, fix = resolve_rate(self.employee, self.service)
        self.assertEqual(pct, Decimal("5.00"))
        self.assertEqual(fix, Decimal("100.00"))

        # 2. Service rate override
        ServiceSalaryRateConsalting.objects.create(
            company=self.company, service=self.service, percent=Decimal("10.00"), fixed_amount=Decimal("200.00")
        )
        pct, fix = resolve_rate(self.employee, self.service)
        self.assertEqual(pct, Decimal("10.00"))
        self.assertEqual(fix, Decimal("200.00"))

        # 3. Employee Scheme
        scheme = SalarySchemeConsalting.objects.create(
            company=self.company, user=self.employee,
            percent_enabled=True, percent=Decimal("15.00"),
            fixed_enabled=True, fixed_amount=Decimal("300.00")
        )
        pct, fix = resolve_rate(self.employee, self.service)
        self.assertEqual(pct, Decimal("15.00"))
        self.assertEqual(fix, Decimal("300.00"))

        # 4. Scheme Service Override
        SalarySchemeServiceOverrideConsalting.objects.create(
            scheme=scheme, service=self.service, percent=Decimal("20.00"), fixed_amount=Decimal("500.00")
        )
        pct, fix = resolve_rate(self.employee, self.service)
        self.assertEqual(pct, Decimal("20.00"))
        self.assertEqual(fix, Decimal("500.00"))

    def test_accrue_salary_for_sale_snapshots(self):
        SalarySchemeConsalting.objects.create(
            company=self.company, user=self.employee,
            percent_enabled=True, percent=Decimal("10.00"),
            fixed_enabled=True, fixed_amount=Decimal("500.00")
        )
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.employee, services=self.service, total=Decimal("10000.00")
        )

        accruals = accrue_salary_for_sale(sale)
        self.assertIsNotNone(accruals)

        acc_pct = SalaryAccrualConsalting.objects.get(sale=sale, kind=SalaryAccrualConsalting.Kind.PERCENT)
        self.assertEqual(acc_pct.amount, Decimal("1000.00"))

        acc_fix = SalaryAccrualConsalting.objects.get(sale=sale, kind=SalaryAccrualConsalting.Kind.FIXED)
        self.assertEqual(acc_fix.amount, Decimal("500.00"))

    def test_scheme_and_defaults_endpoints(self):
        # Defaults API
        res = self.client.get("/api/consalting/salary/defaults/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.put("/api/consalting/salary/defaults/", {"percent": 7.5, "fixed_amount": 150}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["percent"], "7.50")

        # Scheme API
        res = self.client.get(f"/api/consalting/salary/schemes/{self.employee.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        payload = {
            "base_salary_enabled": True, "base_salary": 30000, "base_salary_period": "month",
            "percent_enabled": True, "percent": 12,
            "fixed_enabled": False, "fixed_amount": 0,
            "service_overrides": [
                {"service": str(self.service.id), "percent": 15, "fixed_amount": 200}
            ]
        }
        res = self.client.put(f"/api/consalting/salary/schemes/{self.employee.id}/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["base_salary"], "30000.00")
        self.assertEqual(len(res.data["service_overrides"]), 1)

    def test_bonus_rules_and_progress(self):
        # Create Revenue Ladder Bonus Rule
        payload = {
            "name": "Выручка 50 тыс",
            "condition": "revenue_ladder",
            "period": "month",
            "applies_to": "all",
            "is_active": True,
            "tiers": [
                {"from_amount": 10000, "to_amount": 50000, "percent": 5},
                {"from_amount": 50000, "to_amount": None, "percent": 10}
            ]
        }
        res = self.client.post("/api/consalting/salary/bonus-rules/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Create a sale to generate revenue
        SaleConsalting.objects.create(
            company=self.company, user=self.employee, services=self.service, total=Decimal("60000.00")
        )

        res = self.client.get(f"/api/consalting/salary/bonus-progress/?user={self.employee.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertTrue(res.data["results"][0]["achieved"])
        self.assertEqual(res.data["results"][0]["reward"], 6000.0)

    def test_salary_adjustments_and_payslip(self):
        # Create Fine
        payload = {
            "user": str(self.employee.id),
            "kind": "fine",
            "amount": 1000,
            "reason": "late",
            "comment": "Опоздание на 20 мин",
            "date": str(timezone.now().date())
        }
        res = self.client.post("/api/consalting/salary/adjustments/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        adj_id = res.data["id"]

        # Accrual should exist for fine
        fine_accrual = SalaryAccrualConsalting.objects.filter(user=self.employee, kind="fine").first()
        self.assertIsNotNone(fine_accrual)

        # Test Payslip API
        month_str = timezone.now().strftime("%Y-%m")
        res = self.client.get(f"/api/consalting/salary/payslip/?user={self.employee.id}&month={month_str}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["accrued"], -1000.0)

        # Cancel Fine
        res = self.client.post(f"/api/consalting/salary/adjustments/{adj_id}/cancel/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "canceled")
