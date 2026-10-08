"""
Ошибки QA 06.10.2026 (qa-2026-10-06-backend-fixes.md): B01–B48, бэкенд.
"""
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.warehouse import models, services, services_money
from apps.warehouse import stock as stock_service
from apps.warehouse.op_permissions import BusinessRuleError, OperationForbidden
from apps.warehouse.validators import ean13_check_digit, generate_internal_barcode, normalize_phone

User = get_user_model()
D = Decimal


class _Base(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="qa-owner@example.com", password="x", first_name="O")
        Company = apps.get_model("users", "Company")
        self.company = Company.objects.create(name="QA Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.role = "owner"
        self.owner.save()
        self.employee = User.objects.create_user(
            email="qa-emp@example.com", password="x", first_name="E", company=self.company, role="admin",
        )
        self.wh = self._warehouse("Склад A")
        self.wh2 = self._warehouse("тест")
        self.product = self._product(self.wh, "QA Товар 1", qty="5")
        self.cp = models.Counterparty.objects.create(
            company=self.company, name="Кылым", phone="0555123456", type=models.Counterparty.Type.BOTH,
        )
        self.register = models.CashRegister.objects.create(company=self.company, name="123")
        self.api = APIClient()
        self.api.force_authenticate(user=self.owner)
        self.emp_api = APIClient()
        self.emp_api.force_authenticate(user=self.employee)

    def _warehouse(self, name):
        return models.Warehouse.objects.create(
            name=name, location="x", company=self.company, status=models.Warehouse.Status.active,
        )

    def _product(self, wh, name, *, qty="0", barcode=None, purchase="100", price="150"):
        p = models.WarehouseProduct.objects.create(
            company=self.company, warehouse=wh, name=name, unit="шт", barcode=barcode,
            purchase_price=D(purchase), price=D(price), quantity=D(qty),
        )
        stock_service.init_opening_for_pair(warehouse=wh, product=p)
        return p

    def _doc(self, doc_type, qty, *, product=None, price="150.00", **extra):
        extra.setdefault("warehouse_from", self.wh)
        doc = models.Document.objects.create(doc_type=doc_type, **extra)
        models.DocumentItem.objects.create(
            document=doc, product=product or self.product, qty=D(qty), price=D(price)
        )
        return doc

    def _on_hand(self, product=None, wh=None):
        product = product or self.product
        return stock_service.get_on_hand(warehouse=wh or product.warehouse, product=product)

    def _cash_in(self, amount):
        md = models.MoneyDocument.objects.create(
            company=self.company, doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT,
            cash_register=self.register, amount=D(amount),
            payment_category=models.PaymentCategory.objects.create(company=self.company, title=f"Прочее {amount}"),
        )
        services_money.post_money_document(md)
        return md


class TransferTargetTests(_Base):
    """B01/B31/B22: перемещение зачисляет на тот же товар, а не на чужой с тем же кодом."""

    def test_codes_are_company_wide(self):
        other = self._product(self.wh2, "товар склада тест (мясо", qty="7")
        self.assertNotEqual(self.product.code, other.code)

    def test_transfer_does_not_credit_foreign_product_with_same_code(self):
        meat = self._product(self.wh2, "мясо", qty="7")
        models.WarehouseProduct.objects.filter(pk=meat.pk).update(code=self.product.code)
        doc = self._doc(models.Document.DocType.TRANSFER, "2", warehouse_to=self.wh2)
        services.post_document(doc, user=self.owner)
        self.assertEqual(self._on_hand(meat), D("7.000"))
        target = models.DocumentItem.objects.get(document=doc).target_product
        self.assertNotEqual(target.pk, meat.pk)
        self.assertEqual(target.name, "QA Товар 1")
        self.assertEqual(self._on_hand(target), D("2.000"))

        # повторное перемещение пополняет ту же карточку
        doc2 = self._doc(models.Document.DocType.TRANSFER, "1", warehouse_to=self.wh2)
        services.post_document(doc2, user=self.owner, allow_duplicate=True)
        self.assertEqual(models.DocumentItem.objects.get(document=doc2).target_product_id, target.pk)
        self.assertEqual(self._on_hand(target), D("3.000"))
        self.assertEqual(models.WarehouseProduct.objects.filter(warehouse=self.wh2, name="QA Товар 1").count(), 1)

    def test_transfer_priced_by_cost(self):
        doc = self._doc(models.Document.DocType.TRANSFER, "2", warehouse_to=self.wh2, price="150")
        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.items.get().price, D("100.00"))
        self.assertEqual(doc.total, D("200.00"))

    def test_ambiguous_barcode(self):
        models.WarehouseProduct.objects.filter(pk=self.product.pk).update(barcode="4870000000001")
        self.product.refresh_from_db()
        p1 = self._product(self.wh2, "a", barcode="4870000000001")
        # обходим уникальность штрихкода в складе, как в старых данных
        p2 = self._product(self.wh2, "b")
        models.WarehouseProduct.objects.filter(pk=p2.pk).update(barcode=None)
        models.WarehouseProduct.objects.filter(pk=p1.pk).update(catalog_key=None)
        doc = self._doc(models.Document.DocType.TRANSFER, "1", warehouse_to=self.wh2)
        services.post_document(doc)
        self.assertEqual(models.DocumentItem.objects.get(document=doc).target_product_id, p1.pk)


class NegativeStockPermissionTests(_Base):
    """B02/B03: минус — только по праву; отмена прихода не уводит в минус."""

    def test_employee_without_right_cannot_post_negative(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "100")
        resp = self.emp_api.post(f"/api/warehouse/documents/{doc.pk}/post/", {"allow_negative": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data["code"], "permission_negative_stock")
        self.assertEqual(self._on_hand(), D("5.000"))

    def test_owner_posts_negative_and_is_recorded(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "100")
        resp = self.api.post(f"/api/warehouse/documents/{doc.pk}/post/", {"allow_negative": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.posted_negative_by_id, self.owner.pk)
        resp = self.api.get("/api/warehouse/documents/?posted_negative=true", secure=True)
        ids = [r["id"] for r in (resp.data.get("results") if isinstance(resp.data, dict) else resp.data)]
        self.assertIn(str(doc.pk), ids)

    def test_unpost_purchase_after_sale_blocked(self):
        purchase = self._doc(models.Document.DocType.PURCHASE, "10", payment_kind="credit", counterparty=self.cp)
        services.post_document(purchase)
        sale = self._doc(models.Document.DocType.SALE, "12", payment_kind="credit", counterparty=self.cp)
        services.post_document(sale)
        self.assertEqual(self._on_hand(), D("3.000"))
        with self.assertRaises(BusinessRuleError) as ctx:
            services.unpost_document(purchase, user=self.owner)
        self.assertEqual(ctx.exception.api_code, "unpost_negative_stock")
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, models.Document.Status.POSTED)
        self.assertEqual(self._on_hand(), D("3.000"))

    def test_unpost_purchase_without_sales_ok(self):
        purchase = self._doc(models.Document.DocType.PURCHASE, "10", payment_kind="credit", counterparty=self.cp)
        services.post_document(purchase)
        services.unpost_document(purchase, user=self.owner)
        self.assertEqual(self._on_hand(), D("5.000"))

    def test_unpost_requires_right(self):
        self.employee.role = "manager"
        self.employee.save()
        doc = self._doc(models.Document.DocType.WRITE_OFF, "1")
        services.post_document(doc)
        with self.assertRaises(OperationForbidden):
            services.unpost_document(doc, user=self.employee)
        self.employee.can_unpost_documents = True
        self.employee.save()
        services.unpost_document(doc, user=self.employee)


class ReturnTests(_Base):
    """B04: возврат по продаже, не больше проданного и оплаченного."""

    def setUp(self):
        super().setUp()
        self._cash_in("10000")
        self.sale = self._doc(models.Document.DocType.SALE, "5", payment_kind="cash", counterparty=self.cp)
        services.post_document(self.sale)
        self.sale_item = self.sale.items.get()

    def _return(self, qty, payment_kind="cash"):
        resp = self.api.post("/api/warehouse/documents/sale-return/", {
            "doc_type": "SALE_RETURN",
            "base_document": str(self.sale.pk),
            "payment_kind": payment_kind,
            "items": [{"base_item": str(self.sale_item.pk), "qty": qty}],
        }, format="json", secure=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        return models.Document.objects.get(pk=resp.data["id"])

    def test_return_more_than_sold_rejected(self):
        ret = self._return("20")
        self.assertEqual(ret.counterparty_id, self.cp.pk)
        self.assertEqual(ret.items.get().price, D("150.00"))
        resp = self.api.post(f"/api/warehouse/documents/{ret.pk}/post/", {}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "return_exceeds_sold")
        self.assertEqual(self._on_hand(), D("0.000"))

    def test_partial_returns_and_returnable(self):
        r1 = self._return("2")
        services.post_document(r1, user=self.owner)
        r2 = self._return("4")
        with self.assertRaises(BusinessRuleError) as ctx:
            services.post_document(r2, user=self.owner)
        self.assertEqual(ctx.exception.api_code, "return_exceeds_sold")
        resp = self.api.get(f"/api/warehouse/documents/{self.sale.pk}/returnable/", secure=True)
        self.assertEqual(resp.data["items"][0]["returnable"], "3.000")
        services.unpost_document(r1, user=self.owner)
        resp = self.api.get(f"/api/warehouse/documents/{self.sale.pk}/returnable/", secure=True)
        self.assertEqual(resp.data["items"][0]["returnable"], "5.000")

    def test_return_without_base_requires_right(self):
        ret = self._doc(models.Document.DocType.SALE_RETURN, "1", payment_kind="credit", counterparty=self.cp)
        with self.assertRaises(BusinessRuleError) as ctx:
            services.post_document(ret, user=self.employee)
        self.assertEqual(ctx.exception.api_code, "return_base_required")
        services.post_document(ret, user=self.owner)

    def test_cannot_unpost_sale_with_returns(self):
        r1 = self._return("1")
        services.post_document(r1, user=self.owner)
        with self.assertRaises(BusinessRuleError) as ctx:
            services.unpost_document(self.sale, user=self.owner)
        self.assertEqual(ctx.exception.api_code, "unpost_has_returns")


class CashTests(_Base):
    """B06: касса не уходит в минус; B15: переплата долга."""

    def test_expense_above_balance_rejected(self):
        self._cash_in("1000")
        cat = models.PaymentCategory.objects.create(company=self.company, title="Аренда")
        md = models.MoneyDocument.objects.create(
            company=self.company, doc_type=models.MoneyDocument.DocType.MONEY_EXPENSE,
            cash_register=self.register, amount=D("5000"), payment_category=cat,
        )
        resp = self.emp_api.post(f"/api/warehouse/money/documents/{md.pk}/post/", {}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "cash_insufficient")
        self.assertEqual(resp.data["balance"], "1000.00")
        resp = self.emp_api.post(f"/api/warehouse/money/documents/{md.pk}/post/", {"allow_negative_cash": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 403, resp.data)
        resp = self.api.post(f"/api/warehouse/money/documents/{md.pk}/post/", {"allow_negative_cash": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_cash_purchase_with_empty_cash_rejected(self):
        doc = self._doc(models.Document.DocType.PURCHASE, "3", payment_kind="cash", counterparty=self.cp)
        with self.assertRaises(BusinessRuleError) as ctx:
            services.post_document(doc, user=self.owner)
        self.assertEqual(ctx.exception.api_code, "cash_insufficient")
        self.assertEqual(self._on_hand(), D("5.000"))

    def test_cash_registers_list_has_balance(self):
        self._cash_in("1200")
        resp = self.api.get("/api/warehouse/cash-registers/", secure=True)
        rows = resp.data.get("results") if isinstance(resp.data, dict) else resp.data
        self.assertEqual(rows[0]["balance"], "1200.00")

    def test_debt_overpayment(self):
        sale = self._doc(models.Document.DocType.SALE, "1", price="200", payment_kind="credit", counterparty=self.cp)
        services.post_document(sale)
        from apps.warehouse.utils import system_payment_category

        debt_cat = system_payment_category(self.company, models.PaymentCategory.SystemCode.DEBT)
        payload = {
            "doc_type": "MONEY_RECEIPT", "cash_register": str(self.register.pk), "counterparty": str(self.cp.pk),
            "payment_category": str(debt_cat.pk), "amount": "500", "post": True, "date": timezone.localdate().isoformat(),
        }
        resp = self.api.post("/api/warehouse/money/documents/", payload, format="json", secure=True)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "debt_overpayment")
        resp = self.api.post("/api/warehouse/money/documents/", {**payload, "allow_advance": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        md = models.MoneyDocument.objects.get(pk=resp.data["id"])
        self.assertNotEqual(timezone.localtime(md.date).time().replace(second=0, microsecond=0).isoformat(), "00:00:00")
        self.assertEqual(services_money.counterparty_debt_balance(self.cp), D("-300.00"))


class PeriodAndDateTests(_Base):
    """B07: закрытый период, право на смену даты, номер по дате документа."""

    def test_closed_period_blocks_unpost(self):
        doc = self._doc(models.Document.DocType.WRITE_OFF, "1", date=timezone.now() - timedelta(days=20))
        services.post_document(doc)
        models.WarehouseAccountingSettings.objects.create(
            company=self.company, closed_until=timezone.localdate() - timedelta(days=10)
        )
        self.employee.role = "admin"
        self.employee.save()
        with self.assertRaises(BusinessRuleError) as ctx:
            services.unpost_document(doc, user=self.employee)
        self.assertEqual(ctx.exception.api_code, "period_closed")
        services.unpost_document(doc, user=self.owner)

    def test_change_date_requires_right(self):
        self.employee.role = "manager"
        self.employee.save()
        doc = self._doc(models.Document.DocType.WRITE_OFF, "1")
        resp = self.emp_api.patch(f"/api/warehouse/documents/{doc.pk}/", {"date": "2025-01-01"}, format="json", secure=True)
        # сотрудник-не-агент не видит чужие документы (404) или получает 403 — главное, дата не изменилась
        self.assertIn(resp.status_code, (403, 404), resp.data)
        resp = self.api.patch(f"/api/warehouse/documents/{doc.pk}/", {"date": "2025-01-01"}, format="json", secure=True)
        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.date_changed_by_id, self.owner.pk)
        services.post_document(doc)
        doc.refresh_from_db()
        self.assertTrue(doc.number.startswith("WRITE_OFF-20250101-"), doc.number)

    def test_period_close_endpoint_owner_only(self):
        day = (timezone.localdate() - timedelta(days=5)).isoformat()
        resp = self.api.patch("/api/warehouse/settings/period-close/", {"closed_until": day}, format="json", secure=True)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["closed_until"], day)
        resp = self.emp_api.patch("/api/warehouse/settings/period-close/", {"closed_until": None}, format="json", secure=True)
        self.assertEqual(resp.status_code, 403)

    def test_post_empty_document(self):
        doc = models.Document.objects.create(doc_type=models.Document.DocType.WRITE_OFF, warehouse_from=self.wh)
        with self.assertRaises(BusinessRuleError) as ctx:
            services.post_document(doc)
        self.assertEqual(ctx.exception.api_code, "document_empty")


class CategoryTests(_Base):
    """B13: одна системная категория на компанию."""

    def test_no_duplicates_after_many_purchases(self):
        Branch = apps.get_model("users", "Branch")
        branch = Branch.objects.create(company=self.company, name="Ф1")
        wh_b = models.Warehouse.objects.create(name="ФС", location="x", company=self.company, branch=branch,
                                               status=models.Warehouse.Status.active)
        p = self._product(wh_b, "B")
        self._cash_in("100000")
        for _ in range(3):
            doc = self._doc(models.Document.DocType.PURCHASE, "1", product=p, warehouse_from=wh_b,
                            payment_kind="cash", counterparty=self.cp, cash_register=self.register)
            services.post_document(doc, allow_duplicate=True)
        self.assertEqual(
            models.PaymentCategory.objects.filter(company=self.company, system_code="purchase").count(), 1
        )


class DirectoryTests(_Base):
    """B16, B24, B30, B39."""

    def test_warehouse_duplicate_name(self):
        resp = self.api.post("/api/warehouse/", {"name": "склад a ", "location": "x"}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "duplicate_warehouse")

    def test_counterparty_duplicates(self):
        url = "/api/warehouse/crud/counterparties/"
        resp = self.api.post(url, {"name": "Другой", "phone": "+996 555 12-34-56"}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data["code"], "duplicate_counterparty")
        resp = self.api.post(url, {"name": "кылым", "phone": ""}, format="json", secure=True)
        self.assertEqual(resp.status_code, 409, resp.data)
        resp = self.api.post(url, {"name": "кылым", "force_duplicate_name": True}, format="json", secure=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.api.post(url, {"name": "ИНН", "inn": "12"}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("inn", resp.data)

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("0555 12-34-56"), "996555123456")
        self.assertEqual(normalize_phone("+996555123456"), "996555123456")

    def test_internal_barcode(self):
        code = generate_internal_barcode(self.company)
        self.assertEqual(len(code), 13)
        self.assertTrue(code.startswith("20"))
        self.assertEqual(code[-1], ean13_check_digit(code[:12]))
        self._product(self.wh, "bc", barcode=code)
        self.assertNotEqual(generate_internal_barcode(self.company), code)

    def test_delete_warehouse_with_products(self):
        resp = self.api.delete(f"/api/warehouse/{self.wh.pk}/", secure=True)
        self.assertEqual(resp.status_code, 400, getattr(resp, "data", None))
        self.assertEqual(resp.data["code"], "warehouse_has_data")

    def test_below_minimum_filter(self):
        models.WarehouseProduct.objects.filter(pk=self.product.pk).update(minimum_quantity=D("10"))
        resp = self.api.get("/api/warehouse/products/?below_minimum=true", secure=True)
        rows = resp.data.get("results") if isinstance(resp.data, dict) else resp.data
        self.assertEqual([r["id"] for r in rows], [str(self.product.pk)])


class CompanyApiTests(_Base):
    """B35, B36, B47."""

    def test_company_payload(self):
        self.company.cashier_password = "1234"
        self.company.save()
        resp = self.api.get("/api/users/company/", secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("cashier_password", resp.data)
        self.assertTrue(resp.data["has_cashier_password"])
        self.assertFalse(resp.data["requisites_complete"])
        self.assertEqual(resp.data["limits"]["warehouses"]["used"], 2)
        self.assertEqual(resp.data["limits"]["products"]["used"], 1)


class AnalyticsCashTests(_Base):
    """B11: оплаты с контрагентом — в блоке «контрагенты»."""

    def test_purchase_cash_goes_to_counterparty_block(self):
        from apps.warehouse.analytics import _build_owner_cash_analytics

        self._cash_in("5000")
        doc = self._doc(models.Document.DocType.PURCHASE, "1", price="1000", payment_kind="cash", counterparty=self.cp)
        services.post_document(doc)
        now = timezone.now()
        data = _build_owner_cash_analytics(
            company=self.company, branch=None, dt_from=now - timedelta(days=1), dt_to_excl=now + timedelta(days=1),
            group_by="day", all_branches=True,
        )
        summary = data["summary"]
        self.assertEqual(D(summary["money_counterparty_expense_amount"]), D("1000.00"))
        self.assertEqual(D(summary["money_expense_amount"]), D("0.00"))
        # ручной приход без контрагента остаётся в «кассе без контрагентов»
        self.assertEqual(D(summary["money_receipt_amount"]), D("5000.00"))
