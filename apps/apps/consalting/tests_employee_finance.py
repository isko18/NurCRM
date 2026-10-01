from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    SaleConsalting, ServicesConsalting, CashOperationConsalting,
    CashRequestConsalting, SalaryAdjustmentConsalting
)


class EmployeeFinanceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@empfin.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Emp Fin Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp1 = User.objects.create(
            email="emp1@empfin.com", password="password123", company=self.company
        )
        self.emp2 = User.objects.create(
            email="emp2@empfin.com", password="password123", company=self.company
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="CRM Бухгалтерия", price=Decimal("100000.00")
        )

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp1_client = APIClient()
        self.emp1_client.force_authenticate(user=self.emp1)

    def test_finance_summary_and_permissions(self):
        # Create cash sale for emp1
        SaleConsalting.objects.create(
            company=self.company, user=self.emp1, services=self.service,
            total=Decimal("50000.00")
        )

        # Manager can view emp1 finance
        res = self.mgr_client.get(f"/api/consalting/employees/{self.emp1.id}/finance/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["cash_received"], 50000.0)
        self.assertEqual(res.data["on_hands"], 50000.0)

        # Emp1 can view own finance
        res_own = self.emp1_client.get(f"/api/consalting/employees/{self.emp1.id}/finance/")
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)

        # Emp1 CANNOT view emp2 finance (403 Forbidden)
        res_other = self.emp1_client.get(f"/api/consalting/employees/{self.emp2.id}/finance/")
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

    def test_cash_handover_request_validation(self):
        SaleConsalting.objects.create(
            company=self.company, user=self.emp1, services=self.service,
            total=Decimal("30000.00")
        )

        # Attempt to handover more than on_hands (400 Bad Request)
        res_bad = self.emp1_client.post("/api/consalting/cashbox/handovers/", {"amount": 50000})
        self.assertEqual(res_bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Сумма больше", res_bad.data["detail"])

        # Valid handover request
        res_ok = self.emp1_client.post("/api/consalting/cashbox/handovers/", {
            "amount": 20000, "comment": "Выручка за день"
        })
        self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_ok.data["status"], "pending")

        # Before confirmation, pending_handover=20000, on_hands=10000
        res_fin = self.emp1_client.get(f"/api/consalting/employees/{self.emp1.id}/finance/")
        self.assertEqual(res_fin.data["pending_handover"], 20000.0)
        self.assertEqual(res_fin.data["on_hands"], 10000.0)

    def test_reconciliation_endpoint(self):
        res = self.mgr_client.get("/api/consalting/cashbox/reconciliation/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)

        # Regular employee gets 403
        res_emp = self.emp1_client.get("/api/consalting/cashbox/reconciliation/")
        self.assertEqual(res_emp.status_code, status.HTTP_403_FORBIDDEN)

    def test_shortage_deduction_endpoint(self):
        SaleConsalting.objects.create(
            company=self.company, user=self.emp1, services=self.service,
            total=Decimal("15000.00")
        )
        res = self.mgr_client.post(f"/api/consalting/employees/{self.emp1.id}/deduct-shortage/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["deducted_amount"], 15000.0)

        # SalaryAdjustment created
        adj = SalaryAdjustmentConsalting.objects.filter(user=self.emp1, reason=SalaryAdjustmentConsalting.Reason.SHORTAGE).first()
        self.assertIsNotNone(adj)
        self.assertEqual(adj.amount, Decimal("15000.00"))
