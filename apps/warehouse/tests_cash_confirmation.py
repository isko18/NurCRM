from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from django.apps import apps

from apps.warehouse import models, services

User = get_user_model()


class WarehouseCashConfirmationTests(APITestCase):
    def setUp(self):
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")

        self.owner = User.objects.create_user(
            email="wh_owner@example.com", password="password123", role="owner"
        )
        self.company = Company.objects.create(name="WH Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.manager = User.objects.create_user(
            email="wh_manager@example.com", password="password123", role="manager", company=self.company
        )

        self.branch = Branch.objects.create(company=self.company, name="Main Branch")
        self.wh = models.Warehouse.objects.create(name="Central", company=self.company, branch=self.branch)
        self.cash = models.CashRegister.objects.create(name="Main Cash", company=self.company, branch=self.branch)
        self.paycat = models.PaymentCategory.objects.create(title="Sales", company=self.company, branch=self.branch)

        cat = models.WarehouseProductCategory.objects.create(name="Default Cat", company=self.company, branch=self.branch)
        self.product = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            category=cat,
            name="Widget",
            code="W1",
            unit="pcs",
            quantity=Decimal("100"),
            purchase_price=Decimal("10.00"),
            price=Decimal("20.00"),
        )
        models.StockBalance.objects.create(warehouse=self.wh, product=self.product, qty=Decimal("100.000"))

        self.counterparty = models.Counterparty.objects.create(
            name="Customer One",
            phone="+996555111222",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )

    def test_get_settings_defaults_to_disabled(self):
        """GET /warehouse/cash/confirmation-settings/ returns enabled=False by default."""
        self.client.force_authenticate(user=self.owner)
        res = self.client.get("/warehouse/cash/confirmation-settings/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"enabled": False})

    def test_patch_settings_requires_owner_or_admin(self):
        """PATCH /warehouse/cash/confirmation-settings/ is forbidden for non-owner/non-admin."""
        self.client.force_authenticate(user=self.manager)
        res = self.client.patch("/warehouse/cash/confirmation-settings/", {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 403)

        # Owner can patch
        self.client.force_authenticate(user=self.owner)
        res = self.client.patch("/warehouse/cash/confirmation-settings/", {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"enabled": True})

        # Check DB
        settings_obj = models.WarehouseCashConfirmationSettings.objects.get(company=self.company)
        self.assertTrue(settings_obj.enabled)

    def test_post_cash_document_when_disabled_by_default_posts_immediately(self):
        """When enabled=False (default), cash document posts immediately to POSTED and creates MoneyDocument."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.counterparty,
            payment_kind=models.Document.PaymentKind.CASH,
            company=self.company,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.product, qty=Decimal("5"), price=Decimal("20.00")
        )

        services.post_document(doc, user=self.manager)
        doc.refresh_from_db()

        # Document must be POSTED immediately, not CASH_PENDING
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        # MoneyDocument must be created and POSTED
        money_doc = models.MoneyDocument.objects.filter(source_document=doc).first()
        self.assertIsNotNone(money_doc)
        self.assertEqual(money_doc.status, models.MoneyDocument.Status.POSTED)
        self.assertEqual(Decimal(money_doc.amount), Decimal("100.00"))
        self.assertEqual(money_doc.cash_register, self.cash)

        # CashApprovalRequest should not be in PENDING
        req = getattr(doc, "cash_request", None)
        if req is not None:
            self.assertEqual(req.status, models.CashApprovalRequest.Status.APPROVED)

    def test_post_cash_document_when_enabled_requires_confirmation(self):
        """When enabled=True, cash document transitions to CASH_PENDING and awaits approval."""
        models.WarehouseCashConfirmationSettings.objects.create(company=self.company, enabled=True)

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.counterparty,
            payment_kind=models.Document.PaymentKind.CASH,
            company=self.company,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.product, qty=Decimal("2"), price=Decimal("20.00")
        )

        services.post_document(doc, user=self.manager)
        doc.refresh_from_db()

        # Document must be CASH_PENDING
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)
        req = doc.cash_request
        self.assertEqual(req.status, models.CashApprovalRequest.Status.PENDING)

        # Money document not yet created
        self.assertFalse(models.MoneyDocument.objects.filter(source_document=doc).exists())

        # Now approve
        services.approve_cash_request(doc, decided_by=self.owner)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        req.refresh_from_db()
        self.assertEqual(req.status, models.CashApprovalRequest.Status.APPROVED)

        money_doc = req.money_document
        self.assertIsNotNone(money_doc)
        self.assertEqual(money_doc.status, models.MoneyDocument.Status.POSTED)
        self.assertEqual(Decimal(money_doc.amount), Decimal("40.00"))

    def test_non_cash_document_unaffected_by_toggle(self):
        """Credit and external documents post directly to POSTED regardless of toggle."""
        models.WarehouseCashConfirmationSettings.objects.create(company=self.company, enabled=True)

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.counterparty,
            payment_kind=models.Document.PaymentKind.CREDIT,
            company=self.company,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.product, qty=Decimal("1"), price=Decimal("20.00")
        )

        services.post_document(doc, user=self.manager)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
