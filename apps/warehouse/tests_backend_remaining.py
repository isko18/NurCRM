"""
Пункты backend-remaining-2026-10: R2 (удаление только черновиков), R12 (?source у
agents/me/products), R13 (касса для склада филиала), R14/R15 (движение товара,
себестоимость), R16 (выплата ЗП из кассы), R20 (Document.company).
"""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.warehouse import models, services, services_money, salary_services
from apps.warehouse import stock as stock_service
from apps.warehouse.analytics import build_owner_warehouse_analytics_payload

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="rem-owner@example.com", password="x", first_name="O")
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="Rem Co", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Филиал 1")
        self.wh = models.Warehouse.objects.create(
            name="Склад филиала", company=self.company, branch=self.branch,
            status=models.Warehouse.Status.active,
        )
        self.product = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh,
            name="Товар", code="REM1", unit="шт", is_weight=False,
            purchase_price=Decimal("100.00"), price=Decimal("150.00"), quantity=Decimal("50"),
        )
        stock_service.init_opening_for_pair(warehouse=self.wh, product=self.product)
        self.cp = models.Counterparty.objects.create(
            name="Клиент", phone="+996700000101", type=models.Counterparty.Type.CLIENT,
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.owner)

    def _doc(self, doc_type, qty, *, price="150.00", **extra):
        doc = models.Document.objects.create(doc_type=doc_type, warehouse_from=self.wh, **extra)
        models.DocumentItem.objects.create(document=doc, product=self.product, qty=Decimal(qty), price=Decimal(price))
        return doc

    def _on_hand(self):
        return stock_service.get_on_hand(warehouse=self.wh, product=self.product)


class DeleteDocumentTests(_Base):
    """R2: DELETE — только черновик без движений и денег."""

    def _url(self, doc):
        return f"/api/warehouse/documents/{doc.pk}/"

    def test_delete_draft(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "1")
        resp = self.api.delete(self._url(doc), secure=True)
        self.assertEqual(resp.status_code, 204, getattr(resp, "data", None))
        self.assertFalse(models.Document.objects.filter(pk=doc.pk).exists())
        resp = self.api.delete(self._url(doc), secure=True)
        self.assertEqual(resp.status_code, 404)

    def test_delete_posted_is_rejected_and_stock_untouched(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "5")
        services.post_document(doc)
        self.assertEqual(self._on_hand(), Decimal("45.000"))
        moves_before = models.StockMove.objects.count()

        resp = self.api.delete(self._url(doc), secure=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("черновик", str(resp.data["detail"]))
        self.assertTrue(models.Document.objects.filter(pk=doc.pk).exists())
        self.assertEqual(models.StockMove.objects.count(), moves_before)
        self.assertEqual(self._on_hand(), Decimal("45.000"))

    def test_delete_unposted_draft_keeps_history(self):
        """Распроведённый документ (снова DRAFT) удаляется, история движений и остаток остаются."""
        doc = self._doc(models.Document.DocType.WRITE_OFF, "5")
        services.post_document(doc)
        services.unpost_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
        moves_before = models.StockMove.objects.count()

        resp = self.api.delete(self._url(doc), secure=True)
        self.assertEqual(resp.status_code, 204, getattr(resp, "data", None))
        self.assertEqual(models.StockMove.objects.count(), moves_before)
        self.assertEqual(self._on_hand(), Decimal("50.000"))

    def test_delete_sale_request_is_rejected(self):
        doc = self._doc(models.Document.DocType.SALE, "1", counterparty=self.cp, is_sale_request=True)
        resp = self.api.delete(self._url(doc), secure=True)
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(models.Document.objects.filter(pk=doc.pk).exists())


class CashRegisterFallbackTests(_Base):
    """R13: склад филиала без своей кассы проводит деньги через кассу компании."""

    def _cash_sale(self):
        return self._doc(
            models.Document.DocType.SALE, "2", counterparty=self.cp,
            payment_kind=models.Document.PaymentKind.CASH,
        )

    def test_no_cash_register_gives_error_with_code(self):
        doc = self._cash_sale()
        resp = self.api.post(f"/api/warehouse/documents/{doc.pk}/post/", {}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "cash_register_not_found")
        self.assertIn("нет кассы", resp.data["detail"])
        self.assertEqual(self._on_hand(), Decimal("50.000"))

    def test_falls_back_to_company_cash_register(self):
        company_reg = models.CashRegister.objects.create(company=self.company, branch=None, name="Касса компании")
        doc = self._cash_sale()
        services.post_document(doc)
        money = models.MoneyDocument.objects.get(source_document=doc)
        self.assertEqual(money.cash_register_id, company_reg.id)

    def test_branch_cash_register_has_priority(self):
        models.CashRegister.objects.create(company=self.company, branch=None, name="Касса компании")
        branch_reg = models.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса филиала")
        doc = self._cash_sale()
        services.post_document(doc)
        self.assertEqual(models.MoneyDocument.objects.get(source_document=doc).cash_register_id, branch_reg.id)

    def test_for_warehouse_filter(self):
        company_reg = models.CashRegister.objects.create(company=self.company, branch=None, name="Касса компании")
        resp = self.api.get(f"/api/warehouse/cash-registers/?for_warehouse={self.wh.pk}", secure=True)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        rows = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        self.assertEqual([str(r["id"]) for r in rows], [str(company_reg.id)])


class CostAndMovementAnalyticsTests(_Base):
    """R14/R15: себестоимость фиксируется при проведении; списание — убыток."""

    def _payload(self):
        today = timezone.localdate()
        return build_owner_warehouse_analytics_payload(
            company_id=str(self.company.id), branch_id=str(self.branch.id),
            period="day", date_from=today, date_to=today, group_by="day",
            cache_ver=timezone.now().timestamp(),
        )

    def test_cost_price_frozen_at_posting(self):
        sale = self._doc(models.Document.DocType.SALE, "2", counterparty=self.cp,
                         payment_kind=models.Document.PaymentKind.CREDIT)
        services.post_document(sale)
        self.product.purchase_price = Decimal("130.00")
        self.product.save(update_fields=["purchase_price"])

        item = sale.items.get()
        self.assertEqual(item.cost_price, Decimal("100.00"))
        data = self._payload()["summary"]
        self.assertEqual(data["cogs_amount"], "200.00")
        self.assertFalse(data["cost_is_estimated"])

    def test_cost_is_estimated_for_lines_without_cost_price(self):
        sale = self._doc(models.Document.DocType.SALE, "1", counterparty=self.cp,
                         payment_kind=models.Document.PaymentKind.CREDIT)
        services.post_document(sale)
        models.DocumentItem.objects.filter(document=sale).update(cost_price=None)
        self.assertTrue(self._payload()["summary"]["cost_is_estimated"])

    def test_write_off_and_inventory_in_movement_and_loss(self):
        services.post_document(self._doc(models.Document.DocType.WRITE_OFF, "3"))  # 50 → 47
        services.post_document(self._doc(models.Document.DocType.INVENTORY, "45", price="0"))  # 47 → 45
        summary = self._payload()["summary"]
        self.assertEqual(summary["written_off_qty"], "3.000")
        self.assertEqual(summary["written_off_cost"], "300.00")
        self.assertEqual(summary["inventory_shortage_qty"], "2.000")
        self.assertEqual(summary["inventory_shortage_cost"], "200.00")
        self.assertEqual(summary["writeoff_loss_amount"], "500.00")
        self.assertEqual(summary["operating_profit_amount"], "-500.00")


class SalaryPayoutCashTests(_Base):
    """R16: выплата ЗП из кассы — проведённый MONEY_EXPENSE «Зарплата»."""

    def setUp(self):
        super().setUp()
        self.agent = User.objects.create_user(email="rem-agent@example.com", password="x", first_name="Агент")
        sale = self._doc(models.Document.DocType.SALE, "1", counterparty=self.cp,
                         payment_kind=models.Document.PaymentKind.CREDIT)
        models.AgentSalaryAccrual.objects.create(
            company=self.company, agent=self.agent, sale=sale, warehouse=self.wh,
            sale_type=models.AgentSalaryAccrual.SaleType.choices[0][0],
            sale_amount=Decimal("1000.00"), percent=Decimal("10"), amount=Decimal("100.00"),
        )
        self.reg = models.CashRegister.objects.create(company=self.company, branch=self.branch, name="Касса")

    def _fund(self, amount):
        services_money.post_money_document(models.MoneyDocument.objects.create(
            doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT, status=models.MoneyDocument.Status.DRAFT,
            cash_register=self.reg, company=self.company, branch=self.branch,
            payment_category=models.PaymentCategory.objects.create(
                company=self.company, branch=self.branch, title="Прочее"),
            amount=Decimal(amount),
        ))

    def test_payout_from_cash_register_creates_expense(self):
        self._fund("500.00")
        payout = salary_services.create_payout(
            company=self.company, agent=self.agent, amount=Decimal("60"), cash_register=self.reg,
        )
        money = payout.money_document
        self.assertIsNotNone(money)
        self.assertEqual(money.doc_type, models.MoneyDocument.DocType.MONEY_EXPENSE)
        self.assertEqual(money.status, models.MoneyDocument.Status.POSTED)
        self.assertEqual(money.payment_category.system_code, models.PaymentCategory.SystemCode.SALARY)
        self.assertEqual(services_money.cash_register_balance(self.reg), Decimal("440.00"))

    def test_payout_over_cash_balance_is_rejected(self):
        self._fund("10.00")
        with self.assertRaises(salary_services.SalaryCashError):
            salary_services.create_payout(
                company=self.company, agent=self.agent, amount=Decimal("60"), cash_register=self.reg,
            )
        self.assertFalse(models.AgentSalaryPayout.objects.exists())

    def test_payout_without_cash_register_as_before(self):
        payout = salary_services.create_payout(company=self.company, agent=self.agent, amount=Decimal("60"))
        self.assertIsNone(payout.money_document)


class DocumentCompanyTests(_Base):
    """R20: Document.company заполняется по складу, а для мультискладских — при проведении."""

    def test_company_from_warehouse(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "1")
        self.assertEqual(doc.company_id, self.company.id)

    def test_company_from_items_on_post(self):
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE, counterparty=self.cp,
            payment_kind=models.Document.PaymentKind.CREDIT,
        )
        models.DocumentItem.objects.create(document=doc, product=self.product, qty=Decimal("1"), price=Decimal("150"))
        self.assertIsNone(doc.company_id)
        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.company_id, self.company.id)


class AgentMeProductsSourceTests(_Base):
    """R12: ?source=personal|common у agents/me/products."""

    def setUp(self):
        super().setUp()
        self.agent = User.objects.create_user(email="rem-agent2@example.com", password="x", first_name="Агент")
        models.CompanyWarehouseAgent.objects.create(
            company=self.company, user=self.agent, status=models.CompanyWarehouseAgent.Status.ACTIVE,
            common_access_enabled=True, common_all_warehouses=True,
        )
        models.AgentStockBalance.objects.create(
            agent=self.agent, warehouse=self.wh, product=self.product, qty=Decimal("3"),
            company=self.company, branch=self.branch,
        )
        self.api.force_authenticate(user=self.agent)

    def _get(self, qs=""):
        return self.api.get(f"/api/warehouse/agents/me/products/{qs}", secure=True)

    def test_default_is_common_catalog_for_common_access(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(Decimal(str(resp.data["results"][0]["qty"])), Decimal("50"))

    def test_personal_source_returns_agent_stock(self):
        resp = self._get("?source=personal")
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(Decimal(str(resp.data["results"][0]["qty"])), Decimal("3"))

    def test_invalid_source(self):
        self.assertEqual(self._get("?source=x").status_code, 400)
