from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.construction.models import Cashbox, CashShift
from apps.construction.views import CashShiftOpenView
from apps.users.models import Branch, Company, SubscriptionPlan

User = get_user_model()


class CashShiftOpenFixTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_shift_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Shift Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        self.branch1 = Branch.objects.create(name="Branch 1", company=self.company)
        self.cashbox1 = Cashbox.objects.create(
            name="Касса филиала номер 1",
            company=self.company,
            branch=self.branch1,
        )

    def test_open_shift_on_branched_cashbox_without_branch_header(self):
        # Request with cashbox ID from branch 1, without X-Branch-Id header
        req = self.factory.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.cashbox1.id), "opening_cash": "0.00"},
            format="json",
        )
        force_authenticate(req, user=self.owner)
        view = CashShiftOpenView.as_view()
        resp = view(req)

        self.assertEqual(resp.status_code, 201)
        self.assertIn("id", resp.data)
        self.assertEqual(str(resp.data["cashbox"]), str(self.cashbox1.id))
        self.assertEqual(resp.data["status"].lower(), "open")
