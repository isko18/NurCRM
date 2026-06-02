from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.core.cache import cache

from apps.users.models import Company, Branch, User
from apps.warehouse import models as wm
from apps.warehouse import services as warehouse_services
from apps.warehouse.analytics import (
    build_agent_warehouse_analytics_payload,
    build_owner_partner_warehouse_analytics_payload,
    build_owner_partners_warehouse_analytics_list_payload,
    build_owner_warehouse_analytics_payload,
)
from apps.warehouse.models import canonical_company_pair_ids


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

    def test_agent_analytics_includes_counterparty_debts(self):
        cp = wm.Counterparty.objects.create(
            name="Должник",
            phone="+996700000021",
            company=self.company,
            branch=self.branch,
            agent=self.agent,
            type=wm.Counterparty.Type.CLIENT,
        )
        d = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.POSTED,
            payment_kind=wm.Document.PaymentKind.CREDIT,
            warehouse_from=self.wh,
            counterparty=cp,
            agent=self.agent,
        )
        wm.Document.objects.filter(pk=d.pk).update(date=timezone.now())
        wm.DocumentItem.objects.create(document=d, product=self.p_a, qty=Decimal("1"), price=Decimal("100.00"))
        warehouse_services.recalc_document_totals(d)

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
        self.assertEqual(data["summary"]["counterparties_debt_total"], "100.00")
        self.assertEqual(data["summary"]["counterparties_payable_total"], "0.00")
        self.assertEqual(data["summary"]["counterparty_debts_company_name"], "Test Co")
        self.assertEqual(data["summary"]["counterparty_debts_branch_name"], "Main")
        self.assertIn("formula_ru", data["details"]["counterparties_debt_notes"])
        debts = data["details"]["counterparties_debt"]
        self.assertEqual(len(debts), 1)
        self.assertEqual(debts[0]["counterparty_id"], str(cp.id))
        self.assertEqual(debts[0]["balance"], "100.00")
        self.assertEqual(debts[0]["abs_amount"], "100.00")
        self.assertEqual(debts[0]["direction"], "counterparty_owes_company")
        self.assertEqual(debts[0]["debtor"]["role"], "counterparty")
        self.assertEqual(debts[0]["creditor"]["role"], "company")
        self.assertIn("Должник", debts[0]["summary_ru"])
        self.assertEqual(debts[0]["breakdown"]["sale_and_purchase_return"], "100.00")
        self.assertEqual(debts[0]["breakdown"]["money_receipt"], "0.00")

        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            counterparty=cp,
            amount=Decimal("40.00"),
        )
        cache.clear()
        data2 = build_agent_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            agent_id=str(self.agent.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )
        self.assertEqual(data2["summary"]["counterparties_debt_total"], "60.00")
        row = data2["details"]["counterparties_debt"][0]
        self.assertEqual(row["balance"], "60.00")
        self.assertEqual(row["breakdown"]["money_receipt"], "40.00")


class WarehousePartnerAnalyticsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner2@example.com", password="pass123")
        self.company_a = Company.objects.create(name="Company A", owner=self.owner)
        self.company_b = Company.objects.create(name="Company B")

        self.branch_b = Branch.objects.create(company=self.company_b, name="B Branch")
        self.wh_b = wm.Warehouse.objects.create(
            name="WH B",
            company=self.company_b,
            branch=self.branch_b,
            location="loc",
            status=wm.Warehouse.Status.active,
        )
        self.agent_b = User.objects.create_user(email="agentb@example.com", password="pass123")
        self.product_b = wm.WarehouseProduct.objects.create(
            company=self.company_b,
            branch=self.branch_b,
            warehouse=self.wh_b,
            name="Prod B",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("10.00"),
            price=Decimal("200.00"),
            quantity=Decimal("0.000"),
        )
        self.client_cp = wm.Counterparty.objects.create(
            name="Client B",
            phone="+996700000030",
            type=wm.Counterparty.Type.CLIENT,
        )
        d = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh_b,
            counterparty=self.client_cp,
            agent=self.agent_b,
        )
        wm.Document.objects.filter(pk=d.pk).update(date=timezone.now())
        wm.DocumentItem.objects.create(document=d, product=self.product_b, qty=Decimal("3"), price=Decimal("200.00"))

        id_lo, id_hi = canonical_company_pair_ids(self.company_a.id, self.company_b.id)
        wm.CompanyStockPartnership.objects.create(company_a_id=id_lo, company_b_id=id_hi)

    def test_partners_list_analytics(self):
        today = timezone.localdate()
        data = build_owner_partners_warehouse_analytics_list_payload(
            owner_company_id=str(self.company_a.id),
            period="day",
            date_from=today,
            date_to=today,
        )
        self.assertEqual(data["partners_count"], 1)
        self.assertEqual(len(data["partners"]), 1)
        self.assertEqual(data["partners"][0]["partner_company_name"], "Company B")
        self.assertEqual(data["partners"][0]["summary"]["sales_count"], 1)
        self.assertEqual(data["partners"][0]["summary"]["sales_amount"], "600.00")

    def test_partner_detail_analytics_all_branches(self):
        today = timezone.localdate()
        cache.clear()
        data = build_owner_partner_warehouse_analytics_payload(
            owner_company_id=str(self.company_a.id),
            partner_company_id=str(self.company_b.id),
            branch_id=None,
            period="day",
            date_from=today,
            date_to=today,
            all_branches=True,
        )
        self.assertEqual(data["partner_company"]["name"], "Company B")
        self.assertTrue(data["all_branches"])
        self.assertEqual(data["summary"]["sales_count"], 1)
        self.assertEqual(data["summary"]["sales_amount"], "600.00")
