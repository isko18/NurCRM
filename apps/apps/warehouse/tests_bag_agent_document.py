from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import Branch, Company, SubscriptionPlan
from apps.warehouse.models import Counterparty, Warehouse, WarehouseProduct, Document
from apps.warehouse.views_documents import AgentDocumentListCreateView

User = get_user_model()


class AgentDocumentBagFixTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_agent_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Agent Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.warehouse = Warehouse.objects.create(
            name="Main Warehouse",
            company=self.company,
            branch=self.branch,
        )
        self.counterparty = Counterparty.objects.create(
            name="Buyer",
            company=self.company,
            type=Counterparty.Type.CLIENT,
            agent=self.owner,
        )
        self.product = WarehouseProduct.objects.create(
            name="Test Product",
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            price=Decimal("100.00"),
            purchase_price=Decimal("50.00"),
            quantity=Decimal("50.000"),
        )
        from apps.warehouse.models import AgentStockBalance
        AgentStockBalance.objects.create(
            agent=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            qty=Decimal("20.000"),
            company=self.company,
            branch=self.branch,
        )

    def test_post_agent_sale_document_does_not_crash_500(self):
        # Reproduces the exact payload from bag.txt
        payload = {
            "doc_type": "SALE",
            "items": [
                {
                    "product": str(self.product.id),
                    "qty": "5",
                    "discount_percent": "0.00",
                }
            ],
            "is_sale_request": True,
            "is_wholesale": True,
            "warehouse_from": str(self.warehouse.id),
            "counterparty": str(self.counterparty.id),
            "payment_kind": "cash",
        }

        req = self.factory.post("/api/warehouse/agent/documents/", payload, format="json")
        force_authenticate(req, user=self.owner)
        view = AgentDocumentListCreateView.as_view()
        resp = view(req)

        if resp.status_code != 201:
            print("RESP DATA:", resp.data)
        self.assertEqual(resp.status_code, 201)
        self.assertIn("id", resp.data)
        self.assertIn("cashflows", resp.data)
        self.assertIsInstance(resp.data["cashflows"], list)
