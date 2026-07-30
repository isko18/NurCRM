from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    LeadConsalting, SaleConsalting, ServicesConsalting,
    SalesPlanConsalting, KpiWeightsConsalting
)
from apps.consalting.funnel.employee_stats import kpi_score, DefaultWeights


class EmployeeCardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@empcard.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Emp Card Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp1 = User.objects.create(
            email="emp1@empcard.com", password="password123", company=self.company
        )
        self.emp2 = User.objects.create(
            email="emp2@empcard.com", password="password123", company=self.company
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("50000.00")
        )

        # Sales Plan for emp1
        self.plan = SalesPlanConsalting.objects.create(
            company=self.company, user=self.emp1,
            period_month=timezone.localdate().strftime("%Y-%m"), amount=Decimal("100000.00")
        )

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp1_client = APIClient()
        self.emp1_client.force_authenticate(user=self.emp1)

    def test_employee_stats_view_permissions(self):
        # Manager can view emp1 stats
        res = self.mgr_client.get(f"/api/consalting/employees/{self.emp1.id}/stats/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("kpi", res.data)

        # Emp1 can view own stats
        res_own = self.emp1_client.get(f"/api/consalting/employees/{self.emp1.id}/stats/")
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)

        # Emp1 CANNOT view emp2 stats (403 Forbidden)
        res_other = self.emp1_client.get(f"/api/consalting/employees/{self.emp2.id}/stats/")
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

    def test_rating_view_permissions_and_sorting(self):
        # Manager can access rating
        res = self.mgr_client.get("/api/consalting/employees/rating/?ordering=-revenue")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)

        # Regular employee gets 403 for rating
        res_emp = self.emp1_client.get("/api/consalting/employees/rating/")
        self.assertEqual(res_emp.status_code, status.HTTP_403_FORBIDDEN)

    def test_kpi_score_formula_without_plan(self):
        stats_no_plan = {
            "sales": {"conversion": 40.0, "plan_done_percent": None},
            "speed": {"first_reply_avg_minutes": 10.0},
            "leads": {"deferred": 5, "overdue": 1}
        }
        res = kpi_score(stats_no_plan, DefaultWeights())
        self.assertIsNone(res["plan_score"])
        self.assertGreater(res["score"], 0)

    def test_sales_plans_endpoint(self):
        res = self.mgr_client.get("/api/consalting/sales-plans/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data["results"]), 1)
