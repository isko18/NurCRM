from datetime import date, timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.construction.models import Cashbox, CashFlow
from apps.main.models import Client, ClientDeal, ClientDebtBulkPayment, DealInstallment, DealPayment
from apps.users.models import Branch, Company


User = get_user_model()


class ClientDealsPayAnyTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = User.objects.create_user(email=f"bulk-{uuid.uuid4().hex}@test.com", password="secret")
        self.company = Company.objects.create(name="Bulk payment test", owner=self.user)
        self.user.company = self.company
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.user.branch = self.branch
        self.user.save()
        self.cashbox = Cashbox.objects.create(
            company=self.company, branch=self.branch, name="POS", role=Cashbox.CashboxRole.POS_BRANCH
        )
        self.client = Client.objects.create(company=self.company, branch=self.branch, full_name="Client")
        self.api.force_authenticate(self.user)

    def debt(self, amount, due_date):
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client,
            title=f"Debt {amount}",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal(amount),
            debt_months=1,
            first_due_date=due_date,
        )
        return deal

    def test_distributes_oldest_first_and_is_idempotent(self):
        first = self.debt("301.00", date.today())
        second = self.debt("238.36", date.today() + timedelta(days=1))
        third = self.debt("236.84", date.today() + timedelta(days=2))
        key = str(uuid.uuid4())
        url = f"/main/clients/{self.client.id}/deals/pay-any/"
        payload = {"amount": "600.00", "idempotency_key": key, "cashbox_id": str(self.cashbox.id)}

        response = self.api.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["paid_total"], "600.00")
        first.refresh_from_db(); second.refresh_from_db(); third.refresh_from_db()
        self.assertEqual(first.remaining_debt, Decimal("0.00"))
        self.assertEqual(second.remaining_debt, Decimal("0.00"))
        self.assertEqual(third.remaining_debt, Decimal("176.20"))
        self.assertEqual(DealPayment.objects.count(), 3)
        self.assertEqual(CashFlow.objects.filter(source_kind=CashFlow.SourceKind.DEBT_REPAYMENT).count(), 3)
        self.assertEqual(ClientDebtBulkPayment.objects.count(), 1)

        replay = self.api.post(url, payload, format="json")
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.data["paid_total"], "600.00")
        self.assertEqual(DealPayment.objects.count(), 3)

    def test_rejects_amount_above_total_without_creating_anything(self):
        self.debt("100.00", date.today())
        response = self.api.post(
            f"/main/clients/{self.client.id}/deals/pay-any/",
            {"amount": "100.01", "idempotency_key": str(uuid.uuid4()), "cashbox_id": str(self.cashbox.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DealPayment.objects.count(), 0)
        self.assertEqual(ClientDebtBulkPayment.objects.count(), 0)
