from django.test import TestCase
from decimal import Decimal
from django.contrib.auth import get_user_model

from apps.warehouse import models
from apps.warehouse import services
from django.apps import apps


User = get_user_model()


class DocumentsTests(TestCase):
    def setUp(self):
        # create user, company, branch, warehouse, product
        self.user = User.objects.create(email="u@example.com", password="x", first_name="T", last_name="U")
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="C", owner=self.user)
        self.branch = Branch.objects.create(company=self.company, name="Main")

        self.wh = models.Warehouse.objects.create(name="W1", company=self.company, branch=self.branch, location="loc")
        # one cash register + one payment category, so автокасса can auto-pick
        self.cash = models.CashRegister.objects.create(company=self.company, branch=self.branch, name="Cash", location="")
        self.paycat = models.PaymentCategory.objects.create(company=self.company, branch=self.branch, title="Оплата")
        # create category required by WarehouseProduct
        cat = models.WarehouseProductCategory.objects.create(name="Cat1", company=self.company, branch=self.branch)
        self.prod = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, category=cat,
            name="P1", code="P1", unit="pcs", quantity=Decimal("0"), purchase_price=Decimal("10.00"), price=Decimal("15.00")
        )

    def test_post_sale_creates_cash_request_and_approve_posts_money(self):
        # seed balance 10
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))

        doc = models.Document.objects.create(doc_type=models.Document.DocType.SALE, warehouse_from=self.wh, counterparty=None)
        # add required counterparty for sale
        cp = models.Counterparty.objects.create(name="C1", type=models.Counterparty.Type.CLIENT)
        doc.counterparty = cp
        doc.save()
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("3"), price=Decimal("15"))

        services.post_document(doc)
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.prod)
        self.assertEqual(bal.qty, Decimal("7.000"))

        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)
        req = doc.cash_request
        self.assertEqual(req.status, models.CashApprovalRequest.Status.PENDING)
        self.assertEqual(req.requires_money, True)
        self.assertEqual(req.money_doc_type, models.MoneyDocument.DocType.MONEY_RECEIPT)

        services.approve_cash_request(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        req.refresh_from_db()
        self.assertEqual(req.status, models.CashApprovalRequest.Status.APPROVED)
        money_doc = req.money_document
        self.assertEqual(money_doc.doc_type, models.MoneyDocument.DocType.MONEY_RECEIPT)
        self.assertEqual(money_doc.status, models.MoneyDocument.Status.POSTED)
        self.assertEqual(money_doc.cash_register_id, self.cash.id)
        self.assertEqual(money_doc.payment_category_id, self.paycat.id)
        self.assertEqual(Decimal(money_doc.amount), Decimal("45.00"))

        services.unpost_document(doc)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("10.000"))
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)

    def test_reject_cash_request_sets_document_rejected(self):
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(name="C1", type=models.Counterparty.Type.CLIENT, company=self.company, branch=self.branch)
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=cp,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("2"), price=Decimal("15"))

        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)

        services.reject_cash_request(doc, note="Отказано кассиром")
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.REJECTED)
        req = doc.cash_request
        self.assertEqual(req.status, models.CashApprovalRequest.Status.REJECTED)
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.prod)
        self.assertEqual(bal.qty, Decimal("10.000"))

    def test_credit_sale_posts_immediately_and_creates_no_money_request(self):
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(name="C1", type=models.Counterparty.Type.CLIENT, company=self.company, branch=self.branch)
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=cp,
            payment_kind=models.Document.PaymentKind.CREDIT,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("3"), price=Decimal("15"))

        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        # Не должно требовать кассового подтверждения
        with self.assertRaises(models.CashApprovalRequest.DoesNotExist):
            _ = doc.cash_request

        # Денежного документа тоже не создаём
        self.assertFalse(models.MoneyDocument.objects.filter(source_document_id=doc.id).exists())

    def test_credit_sale_with_prepayment_creates_posted_money_document(self):
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(
            name="C1",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=cp,
            payment_kind=models.Document.PaymentKind.CREDIT,
            prepayment_amount=Decimal("10.00"),
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("3"), price=Decimal("15"))

        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        # Кассового подтверждения не требуется
        with self.assertRaises(models.CashApprovalRequest.DoesNotExist):
            _ = doc.cash_request

        money_doc = doc.money_document
        self.assertEqual(money_doc.doc_type, models.MoneyDocument.DocType.MONEY_RECEIPT)
        self.assertEqual(money_doc.status, models.MoneyDocument.Status.POSTED)
        self.assertEqual(Decimal(money_doc.amount), Decimal("10.00"))
        self.assertEqual(money_doc.cash_register_id, self.cash.id)
        self.assertEqual(money_doc.payment_category_id, self.paycat.id)

        services.unpost_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
        money_doc.refresh_from_db()
        self.assertEqual(money_doc.status, models.MoneyDocument.Status.DRAFT)

    def test_transfer_creates_two_moves(self):
        wh2 = models.Warehouse.objects.create(name="W2", company=self.company, branch=self.branch, location="loc2")
        doc = models.Document.objects.create(doc_type=models.Document.DocType.TRANSFER, warehouse_from=self.wh, warehouse_to=wh2)
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("5"), price=Decimal("0"))

        # seed balance from
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("6.000"))
        services.post_document(doc)
        moves = list(models.StockMove.objects.filter(document=doc))
        self.assertEqual(len(moves), 2)
        b1 = models.StockBalance.objects.get(warehouse=self.wh, product=self.prod)
        dest_prod = models.WarehouseProduct.objects.get(warehouse=wh2, barcode=self.prod.barcode)
        b2 = models.StockBalance.objects.get(warehouse=wh2, product=dest_prod)
        self.assertEqual(b1.qty, Decimal("1.000"))
        self.assertEqual(b2.qty, Decimal("5.000"))

    def test_inventory_sets_delta(self):
        # current 10
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        doc = models.Document.objects.create(doc_type=models.Document.DocType.INVENTORY, warehouse_from=self.wh)
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("7"), price=Decimal("0"))
        services.post_document(doc)
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.prod)
        self.assertEqual(bal.qty, Decimal("7.000"))

    def test_negative_blocked_when_setting_false(self):
        from django.conf import settings
        old = getattr(settings, "ALLOW_NEGATIVE_STOCK", False)
        try:
            settings.ALLOW_NEGATIVE_STOCK = False
            models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("1.000"))
            doc = models.Document.objects.create(doc_type=models.Document.DocType.SALE, warehouse_from=self.wh)
            cp = models.Counterparty.objects.create(name="C1", type=models.Counterparty.Type.CLIENT)
            doc.counterparty = cp
            doc.save()
            models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("5"), price=Decimal("0"))
            with self.assertRaises(Exception):
                services.post_document(doc)
        finally:
            settings.ALLOW_NEGATIVE_STOCK = old
