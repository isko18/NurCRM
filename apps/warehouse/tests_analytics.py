from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.users.models import Company, Branch, User
from apps.warehouse import models as wm
from apps.warehouse.analytics import build_agent_warehouse_analytics_payload, build_owner_warehouse_analytics_payload


class WarehouseAnalyticsByGroupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="owner@example.com", password="pass123", first_name="Owner")
        self.company = Company.objects.create(name="Test Co", owner=self.user)
        self.branch = Branch.objects.create(company=self.company, name="Main")

        self.agent = User.objects.create_user(email="agent@example.com", password="pass123", first_name="Agent")
        # NOTE: agent may be not employee; for analytics we filter by agent on Document

        self.wh = wm.Warehouse.objects.create(
            name="WH",
            company=self.company,
            branch=self.branch,
            location="loc",
            status=wm.Warehouse.Status.active,
        )

        self.group_a = wm.WarehouseProductGroup.objects.create(warehouse=self.wh, name="Group A")
        self.group_b = wm.WarehouseProductGroup.objects.create(warehouse=self.wh, name="Group B")

        self.p_a = wm.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Prod A",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("10.00"),
            price=Decimal("100.00"),
            quantity=Decimal("0.000"),
            product_group=self.group_a,
        )
        self.p_b = wm.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Prod B",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("10.00"),
            price=Decimal("50.00"),
            quantity=Decimal("0.000"),
            product_group=self.group_b,
        )
        self.p_none = wm.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Prod No Group",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("10.00"),
            price=Decimal("20.00"),
            quantity=Decimal("0.000"),
            product_group=None,
        )

        self.client = wm.Counterparty.objects.create(
            name="Client",
            phone="+996700000020",
            type=wm.Counterparty.Type.CLIENT,
        )

        # Make a posted SALE for the agent with items from different groups
        d = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh,
            counterparty=self.client,
            agent=self.agent,
        )
        # fix date inside period
        wm.Document.objects.filter(pk=d.pk).update(date=timezone.now())

        # line_total will be computed on save; use discounts to make sure we use line_total in analytics
        wm.DocumentItem.objects.create(document=d, product=self.p_a, qty=Decimal("2"), price=Decimal("100.00"))
        wm.DocumentItem.objects.create(document=d, product=self.p_b, qty=Decimal("1"), price=Decimal("50.00"))
        wm.DocumentItem.objects.create(document=d, product=self.p_none, qty=Decimal("10"), price=Decimal("20.00"))

    def test_owner_analytics_has_sales_by_group(self):
        today = timezone.localdate()
        data = build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )
        self.assertIn("details", data)
        self.assertIn("sales_by_group", data["details"])
        self.assertIn("top_sales_group", data["details"])

        rows = data["details"]["sales_by_group"]
        self.assertTrue(isinstance(rows, list))
        # Must contain our groups and "Без группы"
        names = {r["group_name"] for r in rows}
        self.assertIn("Group A", names)
        self.assertIn("Group B", names)
        self.assertIn("Без группы", names)

        top = data["details"]["top_sales_group"]
        # "Без группы" should win by amount: 10*20=200
        self.assertIsNotNone(top)
        self.assertEqual(top["group_name"], "Без группы")
        self.assertEqual(top["amount"], "200.00")

    def test_agent_analytics_has_sales_by_group(self):
        today = timezone.localdate()
        data = build_agent_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            agent_id=str(self.agent.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )
        rows = data["details"]["sales_by_group"]
        self.assertTrue(any(r["group_name"] == "Group A" for r in rows))
        self.assertTrue(any(r["group_name"] == "Group B" for r in rows))
        self.assertTrue(any(r["group_name"] == "Без группы" for r in rows))
