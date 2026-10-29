from datetime import timedelta
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

    def test_owner_analytics_warehouses_purchase_amount(self):
        """
        A5: остаток склада — по StockBalance, у агентов — по AgentStockBalance, раздельно.
        on_hand_purchase_amount в складах = warehouse_on_hand_purchase_amount (ТЗ закупочной цены);
        on_hand_qty / on_hand_amount — deprecated-алиасы agent_on_hand_*.
        (Раньше тест ждал on_hand_* из AgentStockBalance — это и был баг A5.)
        """
        wh_empty = wm.Warehouse.objects.create(
            name="WH Empty",
            company=self.company,
            branch=self.branch,
            location="loc2",
            status=wm.Warehouse.Status.active,
        )

        # На складе: 5 × p_a (закупка 10, цена 100) + 2 × p_b (закупка 10, цена 50)
        # → purchase_amount = 70.00, retail_amount = 600.00
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.p_a, qty=Decimal("5.000"))
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.p_b, qty=Decimal("2.000"))
        # У агента на руках: 1 × p_a → 100.00
        wm.AgentStockBalance.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            agent=self.agent,
            product=self.p_a,
            qty=Decimal("1.000"),
        )

        cache.clear()
        today = timezone.localdate()
        data = build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )

        warehouses = {w["warehouse_id"]: w for w in data["details"]["warehouses"]}

        wh_row = warehouses[str(self.wh.id)]
        self.assertEqual(wh_row["warehouse_name"], "WH")
        self.assertEqual(Decimal(wh_row["warehouse_on_hand_qty"]), Decimal("7.000"))
        self.assertEqual(wh_row["warehouse_on_hand_amount"], "600.00")
        self.assertEqual(wh_row["warehouse_on_hand_purchase_amount"], "70.00")
        self.assertEqual(wh_row["on_hand_purchase_amount"], "70.00")
        self.assertEqual(Decimal(wh_row["agent_on_hand_qty"]), Decimal("1.000"))
        self.assertEqual(wh_row["agent_on_hand_amount"], "100.00")
        # deprecated-алиасы agent_on_hand_*
        self.assertEqual(Decimal(wh_row["on_hand_qty"]), Decimal("1.000"))
        self.assertEqual(wh_row["on_hand_amount"], "100.00")

        wh_empty_row = warehouses[str(wh_empty.id)]
        self.assertEqual(wh_empty_row["warehouse_name"], "WH Empty")
        self.assertEqual(Decimal(wh_empty_row["warehouse_on_hand_qty"]), Decimal("0.000"))
        self.assertEqual(wh_empty_row["on_hand_purchase_amount"], "0.00")
        self.assertEqual(Decimal(wh_empty_row["on_hand_qty"]), Decimal("0.000"))
        self.assertEqual(wh_empty_row["on_hand_amount"], "0.00")

        summary = data["summary"]
        self.assertEqual(Decimal(summary["warehouse_on_hand_qty"]), Decimal("7.000"))
        self.assertEqual(summary["warehouse_on_hand_amount"], "600.00")
        self.assertEqual(summary["warehouse_on_hand_purchase_amount"], "70.00")
        self.assertEqual(summary["on_hand_purchase_amount"], "70.00")
        self.assertEqual(Decimal(summary["agent_on_hand_qty"]), Decimal("1.000"))
        self.assertEqual(Decimal(summary["on_hand_qty"]), Decimal("1.000"))
        self.assertEqual(summary["on_hand_amount"], "100.00")

    def test_owner_analytics_includes_purchase_and_salary_summary(self):
        purchase = wm.Document.objects.create(
            doc_type=wm.Document.DocType.PURCHASE,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh,
            counterparty=self.client,
            payment_kind=wm.Document.PaymentKind.CASH,
            total=Decimal("125.00"),
        )
        purchase.date = timezone.now()
        purchase.save(update_fields=["date"])
        wm.DocumentItem.objects.create(document=purchase, product=self.p_a, qty=Decimal("5"), price=Decimal("25.00"))

        sale = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh,
            counterparty=self.client,
            agent=self.agent,
            total=Decimal("200.00"),
        )
        sale.date = timezone.now()
        sale.save(update_fields=["date"])
        wm.DocumentItem.objects.create(document=sale, product=self.p_b, qty=Decimal("2"), price=Decimal("100.00"))

        wm.AgentSalaryAccrual.objects.create(
            company=self.company,
            agent=self.agent,
            sale=sale,
            warehouse=self.wh,
            sale_type=wm.AgentSalaryAccrual.SaleType.RETAIL,
            sale_amount=Decimal("200.00"),
            percent=Decimal("10.00"),
            amount=Decimal("20.00"),
            status=wm.AgentSalaryAccrual.Status.ACCRUED,
        )

        today = timezone.localdate()
        data = build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )

        self.assertEqual(data["summary"]["purchases_count"], 1)
        self.assertEqual(data["summary"]["purchases_amount"], "125.00")
        self.assertEqual(data["summary"]["purchase_returns_amount"], "0.00")
        self.assertEqual(data["summary"]["net_purchases_amount"], "125.00")
        self.assertEqual(data["summary"]["salary_accrued_amount"], "20.00")
        self.assertEqual(data["summary"]["salary_paid_amount"], "0.00")
        self.assertEqual(data["summary"]["salary_payable_amount"], "20.00")
        self.assertEqual(data["summary"]["revenue_amount"], "200.00")
        self.assertEqual(data["summary"]["cogs_amount"], "20.00")
        self.assertEqual(data["summary"]["gross_profit_amount"], "180.00")
        self.assertIn("purchases_by_date", data["charts"])
        self.assertIn("purchases_by_supplier", data["details"])
        self.assertIn("salary_by_agent", data["details"])

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

    def test_owner_cash_excludes_counterparty_operations_from_saldo(self):
        cp = wm.Counterparty.objects.create(
            name="Контрагент",
            phone="+996700000022",
            company=self.company,
            branch=self.branch,
            type=wm.Counterparty.Type.CLIENT,
        )
        cash = wm.CashRegister.objects.create(
            company=self.company, branch=self.branch, name="Основная касса"
        )

        # Обычный приход (без контрагента) — формирует сальдо.
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            amount=Decimal("100.00"),
        )
        # Операция с контрагентом — НЕ должна попадать в сальдо, отдельная графа.
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            counterparty=cp,
            amount=Decimal("70.00"),
        )
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE,
            status=wm.MoneyDocument.Status.POSTED,
            counterparty=cp,
            amount=Decimal("30.00"),
        )

        cache.clear()
        today = timezone.localdate()
        data = build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )

        summary = data["summary"]
        # Сальдо считается только по обычным операциям (без контрагентов).
        self.assertEqual(summary["money_receipt_amount"], "100.00")
        self.assertEqual(summary["money_expense_amount"], "0.00")
        self.assertEqual(summary["money_net_amount"], "100.00")
        # Операции с контрагентами — в отдельной графе.
        self.assertEqual(summary["money_counterparty_receipt_amount"], "70.00")
        self.assertEqual(summary["money_counterparty_expense_amount"], "30.00")
        self.assertEqual(summary["money_counterparty_net_amount"], "40.00")

        registers = {r["account_name"]: r for r in data["details"]["cash_by_register"]}
        reg = registers["Основная касса"]
        self.assertEqual(reg["money_net_amount"], "100.00")
        self.assertEqual(reg["money_counterparty_net_amount"], "40.00")

    def test_owner_cash_counts_debt_operations_separately(self):
        cp = wm.Counterparty.objects.create(
            name="Должник по кассе",
            phone="+996700000023",
            company=self.company,
            branch=self.branch,
            type=wm.Counterparty.Type.CLIENT,
        )
        cash = wm.CashRegister.objects.create(
            company=self.company, branch=self.branch, name="Основная касса"
        )
        debt_cat, _ = wm.PaymentCategory.objects.get_or_create(
            company=self.company,
            branch=self.branch,
            system_code=wm.PaymentCategory.SystemCode.DEBT,
            defaults={"title": "Долги"},
        )

        # Обычный приход/расход — формируют сальдо кассы.
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            amount=Decimal("500.00"),
        )
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE,
            status=wm.MoneyDocument.Status.POSTED,
            amount=Decimal("200.00"),
        )
        # Долги без контрагента — графа «долг».
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE,
            status=wm.MoneyDocument.Status.POSTED,
            payment_category=debt_cat,
            amount=Decimal("80.00"),
        )
        # Долги с контрагентом — тоже графа «долг», а не расход и не «операции с контрагентами».
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE,
            status=wm.MoneyDocument.Status.POSTED,
            payment_category=debt_cat,
            counterparty=cp,
            amount=Decimal("50.00"),
        )
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            payment_category=debt_cat,
            counterparty=cp,
            amount=Decimal("30.00"),
        )

        cache.clear()
        today = timezone.localdate()
        data = build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )

        summary = data["summary"]
        # Долги не попадают в обычный приход/расход.
        self.assertEqual(summary["money_receipt_amount"], "500.00")
        self.assertEqual(summary["money_expense_amount"], "200.00")
        self.assertEqual(summary["money_net_amount"], "300.00")
        # Все операции по категории «Долги» — в отдельной графе.
        self.assertEqual(summary["money_debt_receipt_amount"], "30.00")
        self.assertEqual(summary["money_debt_expense_amount"], "130.00")
        self.assertEqual(summary["money_debt_net_amount"], "-100.00")
        # В графу «операции с контрагентами» долги не попадают.
        self.assertEqual(summary["money_counterparty_receipt_amount"], "0.00")
        self.assertEqual(summary["money_counterparty_expense_amount"], "0.00")

        # Долги не должны попадать в расход по категориям.
        exp_categories = {r["category_title"] for r in data["details"]["money_expenses_by_category"]}
        self.assertNotIn("Долги", exp_categories)

        registers = {r["account_name"]: r for r in data["details"]["cash_by_register"]}
        reg = registers["Основная касса"]
        self.assertEqual(reg["money_expense_amount"], "200.00")
        self.assertEqual(reg["money_debt_expense_amount"], "130.00")
        self.assertEqual(reg["money_debt_receipt_amount"], "30.00")
        self.assertEqual(reg["money_counterparty_expense_amount"], "0.00")

        by_date = data["charts"]["money_by_date"]
        self.assertEqual(len(by_date), 1)
        self.assertEqual(by_date[0]["money_expense_amount"], "200.00")
        self.assertEqual(by_date[0]["money_debt_expense_amount"], "130.00")
        self.assertEqual(by_date[0]["money_counterparty_expense_amount"], "0.00")


class WarehousePartnerAnalyticsTests(TestCase):
    def setUp(self):
        # Company.owner — OneToOne (UNIQUE users_company.owner_id): у каждой компании свой владелец.
        self.owner = User.objects.create_user(email="owner2@example.com", password="pass123")
        self.owner_b = User.objects.create_user(email="owner2b@example.com", password="pass123")
        self.company_a = Company.objects.create(name="Company A", owner=self.owner)
        self.company_b = Company.objects.create(name="Company B", owner=self.owner_b)

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
        # Проведённая продажа всегда имеет пересчитанный total (KPI считаются по Document.total > 0).
        warehouse_services.recalc_document_totals(d)

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

    def test_partner_analytics_sees_new_sale_without_waiting_for_cache_ttl(self):
        today = timezone.localdate()
        kwargs = dict(owner_company_id=str(self.company_a.id), period="day", date_from=today, date_to=today)
        cache.clear()
        build_owner_partners_warehouse_analytics_list_payload(**kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            d = wm.Document.objects.create(
                doc_type=wm.Document.DocType.SALE,
                status=wm.Document.Status.POSTED,
                warehouse_from=self.wh_b,
                counterparty=self.client_cp,
                total=Decimal("100.00"),
            )
        wm.Document.objects.filter(pk=d.pk).update(date=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            d.save(update_fields=["comment"])  # любое сохранение документа сбрасывает версию кэша
        data = build_owner_partners_warehouse_analytics_list_payload(**kwargs)
        self.assertEqual(data["partners"][0]["summary"]["sales_count"], 2)
        self.assertEqual(data["partners"][0]["summary"]["sales_amount"], "700.00")


class WarehouseAnalyticsCalculationFixesTests(TestCase):
    """Сценарии из analytics-calculation-fixes.md §6."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(email="calc-owner@example.com", password="pass123", first_name="Owner")
        self.company = Company.objects.create(name="Calc Co", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.agent = User.objects.create_user(email="calc-agent@example.com", password="pass123", first_name="Agent")
        self.wh = wm.Warehouse.objects.create(
            name="WH",
            company=self.company,
            branch=self.branch,
            location="loc",
            status=wm.Warehouse.Status.active,
        )
        self.product = wm.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Prod",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("6.00"),
            price=Decimal("10.00"),
            quantity=Decimal("0.000"),
        )
        self.client = wm.Counterparty.objects.create(
            name="Client",
            phone="+996700000099",
            type=wm.Counterparty.Type.CLIENT,
        )

    def _sale(self, total, *, agent=None, status=wm.Document.Status.POSTED):
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=status,
            warehouse_from=self.wh,
            counterparty=self.client,
            agent=agent,
            total=Decimal(total),
        )
        wm.Document.objects.filter(pk=doc.pk).update(date=timezone.now(), total=Decimal(total))
        return doc

    def _owner_summary(self):
        today = timezone.localdate()
        return build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )["summary"]

    def test_receipt_maps_to_money_expense_and_write_off_to_none(self):
        resolve = warehouse_services._resolve_money_doc_type
        self.assertEqual(resolve(wm.Document.DocType.RECEIPT), wm.MoneyDocument.DocType.MONEY_EXPENSE)
        self.assertIsNone(resolve(wm.Document.DocType.WRITE_OFF))

    def test_sales_include_own_sales_and_split_by_agent(self):
        self._sale("1000.00", agent=self.agent)
        self._sale("200.00")
        summary = self._owner_summary()
        self.assertEqual(summary["sales_amount"], "1200.00")
        self.assertEqual(summary["agent_sales_amount"], "1000.00")
        self.assertEqual(summary["own_sales_amount"], "200.00")

    def test_cash_pending_sale_counted_in_sales(self):
        self._sale("400.00", status=wm.Document.Status.CASH_PENDING)
        summary = self._owner_summary()
        self.assertEqual(summary["sales_amount"], "400.00")
        self.assertEqual(summary["pending_cash_sales_count"], 1)
        self.assertEqual(summary["pending_cash_sales_amount"], "400.00")
        self.assertEqual(summary["money_receipt_amount"], "0.00")

    def test_net_amount_includes_line_and_document_discounts(self):
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.DRAFT,
            warehouse_from=self.wh,
            counterparty=self.client,
            discount_amount=Decimal("18.00"),
        )
        wm.DocumentItem.objects.create(
            document=doc,
            product=self.product,
            qty=Decimal("2"),
            price=Decimal("100.00"),
            discount_percent=Decimal("10"),
        )
        warehouse_services.recalc_document_totals(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.total, Decimal("162.00"))
        net_sum = sum((i.net_amount for i in doc.items.all()), Decimal("0.00"))
        self.assertEqual(net_sum, Decimal("162.00"))

        wm.Document.objects.filter(pk=doc.pk).update(status=wm.Document.Status.POSTED, date=timezone.now())
        cache.clear()
        data = self._owner_payload()
        self.assertEqual(data["summary"]["sales_amount"], "162.00")
        products = {r["product_id"]: r for r in data["details"]["sales_by_product"]}
        self.assertEqual(products[str(self.product.id)]["amount"], "162.00")
        self.assertEqual(data["details"]["sales_by_group"][0]["amount"], "162.00")
        profit = {r["product_id"]: r for r in data["details"]["profit_by_product"]}
        self.assertEqual(profit[str(self.product.id)]["revenue"], "162.00")

    def test_new_sale_visible_without_waiting_for_cache_ttl(self):
        self._owner_summary()
        with self.captureOnCommitCallbacks(execute=True):
            self._sale("300.00")
        self.assertEqual(self._owner_summary()["sales_amount"], "300.00")

    def test_document_date_in_future_rejected(self):
        now = timezone.now()
        self.assertFalse(warehouse_services.is_document_date_in_future(now))
        self.assertFalse(warehouse_services.is_document_date_in_future(now + timedelta(hours=20)))
        self.assertTrue(warehouse_services.is_document_date_in_future(now + timedelta(days=2)))
        self.assertTrue(warehouse_services.is_document_date_in_future(timezone.localdate() + timedelta(days=3)))

    def _owner_payload(self):
        today = timezone.localdate()
        return build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )

    def test_receipt_without_category_gets_system_purchase_category(self):
        wm.PaymentCategory.objects.create(company=self.company, branch=self.branch, title="Продажа (ручная)")
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.RECEIPT,
            status=wm.Document.Status.DRAFT,
            warehouse_to=self.wh,
        )
        category = warehouse_services._resolve_document_payment_category(doc, self.company, self.branch)
        self.assertIsNotNone(category)
        self.assertEqual(category.system_code, wm.PaymentCategory.SystemCode.PURCHASE)

    def test_document_payment_counts_as_cash_not_counterparty(self):
        cash = wm.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса")
        sale = self._sale("500.00")
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            counterparty=self.client,
            source_document=sale,
            amount=Decimal("500.00"),
        )
        wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            counterparty=self.client,
            amount=Decimal("300.00"),
        )
        cache.clear()
        summary = self._owner_payload()["summary"]
        # QA B11: оплата документа с контрагентом — в блоке «контрагенты», а не «касса без контрагентов».
        self.assertEqual(summary["money_receipt_amount"], "0.00")
        self.assertEqual(summary["money_counterparty_receipt_amount"], "800.00")

    def test_returns_subtracted_in_summary_and_top_agents(self):
        self._sale("1000.00", agent=self.agent)
        ret = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE_RETURN,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh,
            warehouse_to=self.wh,
            counterparty=self.client,
            agent=self.agent,
            total=Decimal("200.00"),
        )
        wm.Document.objects.filter(pk=ret.pk).update(date=timezone.now(), total=Decimal("200.00"))
        cache.clear()
        data = self._owner_payload()
        self.assertEqual(data["summary"]["sales_amount"], "800.00")
        agents = {r["agent_id"]: r for r in data["top_agents"]["by_sales"]}
        self.assertEqual(agents[str(self.agent.id)]["sales_amount"], "800.00")

    def test_agent_share_percent_counts_all_agents(self):
        agents = [
            User.objects.create_user(email=f"share-{i}@example.com", password="pass123", first_name=f"A{i}")
            for i in range(11)
        ]
        for a in agents:
            self._sale("100.00", agent=a)
        cache.clear()
        top = self._owner_payload()["top_agents"]
        self.assertEqual(Decimal(top["total_sales_amount"]), Decimal("1100.00"))
        shares = [Decimal(str(r["share_percent"])) for r in top["by_sales"]]
        self.assertEqual(len(shares), 10)
        # доля — от ВСЕХ 11 агентов (1100), а не от топ-10 (1000)
        self.assertTrue(all(sh == Decimal("9.09") for sh in shares))
        self.assertEqual(sum(shares), Decimal("90.90"))
        self.assertEqual(self._owner_payload()["summary"]["agent_sales_amount"], "1100.00")

    def test_owner_agents_sales_analytics_endpoint_ok(self):
        # Раньше падал с FieldError: у User нет поля username (values("agent__username")).
        from rest_framework.test import APIClient

        self._sale("300.00", agent=self.agent)
        api = APIClient()
        api.force_authenticate(self.owner)
        resp = api.get("/api/warehouse/owner/agents/analytics/?period=day", secure=True)
        self.assertEqual(resp.status_code, 200, resp.content[:500])

    def test_warehouse_stock_from_stock_balance_and_agent_stock_separately(self):
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.product, qty=Decimal("50.000"))
        wm.AgentStockBalance.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            agent=self.agent,
            product=self.product,
            qty=Decimal("3.000"),
        )
        cache.clear()
        summary = self._owner_payload()["summary"]
        self.assertEqual(Decimal(summary["warehouse_on_hand_qty"]), Decimal("50"))
        self.assertEqual(summary["warehouse_on_hand_amount"], "500.00")
        self.assertEqual(summary["warehouse_on_hand_purchase_amount"], "300.00")
        self.assertEqual(Decimal(summary["agent_on_hand_qty"]), Decimal("3"))

    # ---- сценарии §6 с полным проведением и разрезами ----

    def _item_doc(self, doc_type, lines, *, agent=None, warehouse_from="default", status=wm.Document.Status.POSTED, **extra):
        doc = wm.Document.objects.create(
            doc_type=doc_type,
            status=wm.Document.Status.DRAFT,
            warehouse_from=self.wh if warehouse_from == "default" else warehouse_from,
            counterparty=self.client,
            agent=agent,
            **extra,
        )
        for product, qty, price in lines:
            wm.DocumentItem.objects.create(document=doc, product=product, qty=Decimal(qty), price=Decimal(price))
        warehouse_services.recalc_document_totals(doc)
        wm.Document.objects.filter(pk=doc.pk).update(status=status, date=timezone.now())
        doc.refresh_from_db()
        return doc

    def test_posted_cash_receipt_creates_money_expense_with_purchase_category(self):
        """§6 #1, #2: RECEIPT за наличные → расход денег 1000, категория — системная «Закупка»."""
        cash = wm.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса")
        wm.PaymentCategory.objects.create(company=self.company, branch=self.branch, title="Ручная категория")
        # B06: расход из кассы — только при достаточном остатке. Пополняем кассу датой «вчера»
        # вне периода сводки не нужно: проверяем только расход документа ниже.
        wm.MoneyDocument.objects.create(
            company=self.company, branch=self.branch, cash_register=cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT, status=wm.MoneyDocument.Status.POSTED,
            amount=Decimal("1000.00"), date=timezone.now() - timedelta(days=400),
        )
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.RECEIPT,
            status=wm.Document.Status.DRAFT,
            warehouse_from=self.wh,
            payment_kind=wm.Document.PaymentKind.CASH,
        )
        wm.DocumentItem.objects.create(document=doc, product=self.product, qty=Decimal("100"), price=Decimal("10.00"))
        warehouse_services.recalc_document_totals(doc)
        warehouse_services.post_document(doc)

        money = wm.MoneyDocument.objects.get(source_document=doc)
        self.assertEqual(money.doc_type, wm.MoneyDocument.DocType.MONEY_EXPENSE)
        self.assertEqual(money.amount, Decimal("1000.00"))
        self.assertEqual(money.status, wm.MoneyDocument.Status.POSTED)
        self.assertEqual(money.payment_category.system_code, wm.PaymentCategory.SystemCode.PURCHASE)

        cache.clear()
        summary = self._owner_summary()
        self.assertEqual(summary["money_receipt_amount"], "0.00")
        self.assertEqual(summary["money_expense_amount"], "1000.00")
        self.assertEqual(summary["money_counterparty_expense_amount"], "0.00")

    def test_posted_cash_sale_with_counterparty_is_cash_receipt(self):
        """§6 #3: наличная продажа 500 с контрагентом (без агента) — в «Приход по кассе»."""
        wm.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса")
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.product, qty=Decimal("100.000"))
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.DRAFT,
            warehouse_from=self.wh,
            counterparty=self.client,
            payment_kind=wm.Document.PaymentKind.CASH,
        )
        wm.DocumentItem.objects.create(document=doc, product=self.product, qty=Decimal("50"), price=Decimal("10.00"))
        warehouse_services.recalc_document_totals(doc)
        warehouse_services.post_document(doc)

        money = wm.MoneyDocument.objects.get(source_document=doc)
        self.assertEqual(money.doc_type, wm.MoneyDocument.DocType.MONEY_RECEIPT)
        self.assertEqual(money.payment_category.system_code, wm.PaymentCategory.SystemCode.SALE)
        cache.clear()
        data = self._owner_payload()
        # QA B11: наличная продажа с контрагентом — «Приход от контрагентов».
        self.assertEqual(data["summary"]["money_receipt_amount"], "0.00")
        self.assertEqual(data["summary"]["money_counterparty_receipt_amount"], "500.00")
        self.assertEqual(data["summary"]["own_sales_amount"], "500.00")
        cats = {r["category_title"]: r["amount"] for r in data["details"]["money_receipts_by_category"]}
        self.assertEqual(cats.get("Продажа"), "500.00")

    def test_return_subtracted_in_every_block(self):
        """§6 #8: продажа 1000, возврат 200 (агент А, склад С, товар Т) → везде 800."""
        self._item_doc(wm.Document.DocType.SALE, [(self.product, "100", "10.00")], agent=self.agent)
        self._item_doc(
            wm.Document.DocType.SALE_RETURN, [(self.product, "20", "10.00")], agent=self.agent, warehouse_to=self.wh
        )
        cache.clear()
        data = self._owner_payload()
        self.assertEqual(data["summary"]["sales_amount"], "800.00")
        self.assertEqual(data["summary"]["gross_sales_amount"], "1000.00")
        self.assertEqual(data["summary"]["returns_amount"], "200.00")
        self.assertEqual(data["charts"]["sales_by_date"][0]["sales_amount"], "800.00")
        self.assertEqual(data["charts"]["sales_by_date"][0]["returns_amount"], "200.00")
        self.assertEqual(data["top_agents"]["by_sales"][0]["sales_amount"], "800.00")
        wh_row = {r["warehouse_id"]: r for r in data["details"]["warehouses"]}[str(self.wh.id)]
        self.assertEqual(wh_row["sales_amount"], "800.00")
        self.assertEqual(wh_row["returns_amount"], "200.00")
        products = {r["product_id"]: r for r in data["details"]["sales_by_product"]}
        self.assertEqual(products[str(self.product.id)]["amount"], "800.00")
        self.assertEqual(Decimal(products[str(self.product.id)]["qty"]), Decimal("80"))
        self.assertEqual(data["details"]["sales_by_group"][0]["amount"], "800.00")
        self.assertEqual(data["details"]["profit_by_agent"][0]["revenue"], "800.00")
        self.assertEqual(data["details"]["profit_by_agent"][0]["cogs"], "480.00")
        self.assertEqual(data["charts"]["profit_by_date"][0]["revenue"], "800.00")
        self.assertEqual(data["charts"]["profit_by_date"][0]["cogs"], "480.00")

        today = timezone.localdate()
        agent_data = build_agent_warehouse_analytics_payload(
            company_id=str(self.company.id),
            branch_id=str(self.branch.id),
            agent_id=str(self.agent.id),
            period="day",
            date_from=today,
            date_to=today,
            group_by="day",
        )
        self.assertEqual(agent_data["summary"]["sales_amount"], "800.00")
        self.assertEqual(agent_data["charts"]["sales_by_date"][0]["sales_amount"], "800.00")
        self.assertEqual(agent_data["details"]["sales_by_warehouse"][0]["sales_amount"], "800.00")
        self.assertEqual(agent_data["details"]["sales_by_product"][0]["amount"], "800.00")

    def test_profit_by_agent_not_multiplied_by_line_count(self):
        other = wm.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, name="Other", unit="шт",
            is_weight=False, purchase_price=Decimal("1.00"), price=Decimal("5.00"), quantity=Decimal("0.000"),
        )
        self._item_doc(
            wm.Document.DocType.SALE, [(self.product, "10", "10.00"), (other, "10", "5.00")], agent=self.agent
        )
        cache.clear()
        row = self._owner_payload()["details"]["profit_by_agent"][0]
        self.assertEqual(row["revenue"], "150.00")
        self.assertEqual(row["cogs"], "70.00")
        self.assertEqual(row["profit"], "80.00")

    def test_warehouse_row_has_stock_and_agent_stock(self):
        """§6 #10 в разрезе details.warehouses[]."""
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.product, qty=Decimal("50.000"))
        wm.AgentStockBalance.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, agent=self.agent,
            product=self.product, qty=Decimal("3.000"),
        )
        cache.clear()
        row = {r["warehouse_id"]: r for r in self._owner_payload()["details"]["warehouses"]}[str(self.wh.id)]
        self.assertEqual(Decimal(row["warehouse_on_hand_qty"]), Decimal("50"))
        self.assertEqual(row["warehouse_on_hand_amount"], "500.00")
        self.assertEqual(row["warehouse_on_hand_purchase_amount"], "300.00")
        self.assertEqual(row["on_hand_purchase_amount"], "300.00")
        self.assertEqual(Decimal(row["agent_on_hand_qty"]), Decimal("3"))
        self.assertEqual(row["agent_on_hand_amount"], "30.00")

    def test_multi_warehouse_sale_without_warehouse_from_counted_by_line_warehouse(self):
        """§6 #12: мультискладская продажа с warehouse_from = NULL → KPI и «Склады» по складу строки."""
        wh2 = wm.Warehouse.objects.create(
            name="WH2", company=self.company, branch=self.branch, location="loc2", status=wm.Warehouse.Status.active
        )
        product2 = wm.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=wh2, name="Prod2", unit="шт",
            is_weight=False, purchase_price=Decimal("6.00"), price=Decimal("100.00"), quantity=Decimal("0.000"),
        )
        self._item_doc(
            wm.Document.DocType.SALE,
            [(self.product, "10", "10.00"), (product2, "3", "100.00")],
            warehouse_from=None,
        )
        # warehouse_from задан, но строка — с другого склада: тоже по складу строки
        self._item_doc(wm.Document.DocType.SALE, [(product2, "1", "100.00")], warehouse_from=self.wh)
        cache.clear()
        data = self._owner_payload()
        self.assertEqual(data["summary"]["sales_count"], 2)
        self.assertEqual(data["summary"]["sales_amount"], "500.00")
        rows = {r["warehouse_id"]: r for r in data["details"]["warehouses"]}
        self.assertEqual(rows[str(self.wh.id)]["sales_amount"], "100.00")
        self.assertEqual(rows[str(wh2.id)]["sales_amount"], "400.00")
        self.assertEqual(rows[str(wh2.id)]["sales_count"], 2)

    def test_multi_warehouse_agent_sale_without_warehouse_from_in_agent_analytics(self):
        self._item_doc(wm.Document.DocType.SALE, [(self.product, "5", "10.00")], agent=self.agent, warehouse_from=None)
        today = timezone.localdate()
        data = build_agent_warehouse_analytics_payload(
            company_id=str(self.company.id), branch_id=str(self.branch.id), agent_id=str(self.agent.id),
            period="day", date_from=today, date_to=today, group_by="day",
        )
        self.assertEqual(data["summary"]["sales_amount"], "50.00")
        self.assertEqual(data["details"]["sales_by_warehouse"][0]["warehouse_id"], str(self.wh.id))

    def test_post_document_with_future_date_rejected(self):
        """§6 #13: проведение документа с датой «завтра + 2 дня» → ошибка."""
        wm.StockBalance.objects.create(warehouse=self.wh, product=self.product, qty=Decimal("10.000"))
        doc = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE,
            status=wm.Document.Status.DRAFT,
            warehouse_from=self.wh,
            counterparty=self.client,
            payment_kind=wm.Document.PaymentKind.CREDIT,
        )
        wm.DocumentItem.objects.create(document=doc, product=self.product, qty=Decimal("1"), price=Decimal("10.00"))
        warehouse_services.recalc_document_totals(doc)
        wm.Document.objects.filter(pk=doc.pk).update(date=timezone.now() + timedelta(days=3))
        doc.refresh_from_db()
        with self.assertRaisesMessage(ValueError, warehouse_services.DOCUMENT_FUTURE_DATE_ERROR):
            warehouse_services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, wm.Document.Status.DRAFT)

    def test_serializer_rejects_future_document_date(self):
        """§6 #13: создание документа с датой в будущем → 400 (ValidationError по полю date)."""
        from rest_framework import serializers as drf_serializers
        from apps.warehouse.serializers_documents import DocumentSerializer

        with self.assertRaises(drf_serializers.ValidationError) as ctx:
            DocumentSerializer(context={}).validate({"date": timezone.now() + timedelta(days=3)})
        self.assertIn("date", ctx.exception.detail)


class WarehouseDataFixCommandsTests(TestCase):
    """Команды исправления данных: по умолчанию dry-run, запись — только с --apply."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(email="fix-owner@example.com", password="pass123")
        self.company = Company.objects.create(name="Fix Co", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.wh = wm.Warehouse.objects.create(
            name="WH", company=self.company, branch=self.branch, location="loc", status=wm.Warehouse.Status.active
        )
        self.cash = wm.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса")
        self.receipt = wm.Document.objects.create(
            doc_type=wm.Document.DocType.RECEIPT,
            status=wm.Document.Status.POSTED,
            warehouse_from=self.wh,
            payment_kind=wm.Document.PaymentKind.CASH,
            total=Decimal("4608665.00"),
        )
        self.wrong = wm.MoneyDocument.objects.create(
            company=self.company,
            branch=self.branch,
            cash_register=self.cash,
            doc_type=wm.MoneyDocument.DocType.MONEY_RECEIPT,
            status=wm.MoneyDocument.Status.POSTED,
            source_document=self.receipt,
            amount=Decimal("4608665.00"),
            comment="АВТО: RECEIPT",
        )

    def _call(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("fix_receipt_money_documents", *args, stdout=out)
        return out.getvalue()

    def test_fix_receipt_money_documents_dry_run_by_default(self):
        out = self._call()
        self.assertIn(str(self.wrong.pk), out)
        self.assertIn("4608665.00", out)
        self.assertIn("Сухой прогон", out)
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.status, wm.MoneyDocument.Status.POSTED)

    def test_fix_receipt_money_documents_apply_is_idempotent_and_regenerates_expense(self):
        from apps.warehouse import services_money

        self._call("--apply")
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.status, wm.MoneyDocument.Status.REJECTED)
        self.assertIn("ошибочно", self.wrong.comment)
        self.assertEqual(services_money.cash_register_balance(self.cash), Decimal("0.00"))
        self.assertIn("не найдено", self._call("--apply"))  # повторный запуск ничего не меняет

        self._call("--regenerate-expense", str(self.receipt.pk))  # dry-run
        self.assertFalse(wm.MoneyDocument.objects.filter(doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE).exists())

        self._call("--regenerate-expense", str(self.receipt.pk), "--apply")
        expense = wm.MoneyDocument.objects.get(source_document=self.receipt)
        self.assertEqual(expense.doc_type, wm.MoneyDocument.DocType.MONEY_EXPENSE)
        self.assertEqual(expense.status, wm.MoneyDocument.Status.POSTED)
        self.assertEqual(services_money.cash_register_balance(self.cash), Decimal("-4608665.00"))
        self._call("--regenerate-expense", str(self.receipt.pk), "--apply")  # второй раз — пропуск
        self.assertEqual(wm.MoneyDocument.objects.filter(doc_type=wm.MoneyDocument.DocType.MONEY_EXPENSE).count(), 1)

    def test_mark_external(self):
        self._call("--mark-external", str(self.receipt.pk))
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.payment_kind, wm.Document.PaymentKind.CASH)
        self._call("--mark-external", str(self.receipt.pk), "--apply")
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.payment_kind, wm.Document.PaymentKind.EXTERNAL)

    def test_cleanup_empty_posted_documents(self):
        from io import StringIO
        from django.core.management import call_command

        cp = wm.Counterparty.objects.create(
            name="Client", phone="+996700000111", company=self.company, type=wm.Counterparty.Type.CLIENT
        )
        empty = wm.Document.objects.create(
            doc_type=wm.Document.DocType.SALE, status=wm.Document.Status.POSTED, counterparty=cp,
            total=Decimal("15000.00"),
        )
        out = StringIO()
        call_command("cleanup_empty_posted_documents", stdout=out)
        self.assertIn(str(empty.pk), out.getvalue())
        self.assertIn("Fix Co", out.getvalue())
        empty.refresh_from_db()
        self.assertEqual(empty.status, wm.Document.Status.POSTED)

        call_command("cleanup_empty_posted_documents", "--apply", stdout=StringIO())
        empty.refresh_from_db()
        self.assertEqual(empty.status, wm.Document.Status.DRAFT)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, wm.Document.Status.POSTED)  # со складом — не трогаем

    def test_report_future_dated_documents(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError

        wm.Document.objects.filter(pk=self.receipt.pk).update(date=timezone.now() + timedelta(days=5))
        out = StringIO()
        call_command("report_future_dated_documents", stdout=out)
        self.assertIn(str(self.receipt.pk), out.getvalue())
        with self.assertRaises(CommandError):
            call_command("report_future_dated_documents", "--apply", stdout=StringIO())
        call_command("report_future_dated_documents", "--ids", str(self.receipt.pk), "--apply", stdout=StringIO())
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.date, self.receipt.created_at)
