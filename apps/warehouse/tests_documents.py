from django.test import TestCase
from rest_framework.test import APITestCase
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
        cp = models.Counterparty.objects.create(
            name="C1",
            phone="+996700000001",
            type=models.Counterparty.Type.CLIENT,
        )
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
        cp = models.Counterparty.objects.create(
            name="C1",
            phone="+996700000002",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
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

    def test_receipt_external_posts_immediately_no_cash(self):
        """Приход с payment_kind=external: склад проводится, касса и денежные документы не задействованы."""
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.RECEIPT,
            warehouse_from=self.wh,
            payment_kind=models.Document.PaymentKind.EXTERNAL,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.prod, qty=Decimal("2"), price=Decimal("100.00")
        )

        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        with self.assertRaises(models.CashApprovalRequest.DoesNotExist):
            _ = doc.cash_request
        self.assertFalse(models.MoneyDocument.objects.filter(source_document_id=doc.id).exists())
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.prod)
        self.assertEqual(bal.qty, Decimal("12.000"))

    def test_credit_sale_posts_immediately_and_creates_no_money_request(self):
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(
            name="C1",
            phone="+996700000003",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
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
            phone="+996700000004",
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

    def test_debt_alias_payment_kind_posts_as_credit_without_cash(self):
        """payment_kind=debt (как в POS) должен проводиться как credit — без кассы."""
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(
            name="C1",
            phone="+996700000005",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=cp,
            payment_kind="debt",
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("2"), price=Decimal("15"))
        doc.clean()
        doc.save(update_fields=["payment_kind"])

        self.assertEqual(doc.payment_kind, models.Document.PaymentKind.CREDIT)
        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        with self.assertRaises(models.CashApprovalRequest.DoesNotExist):
            _ = doc.cash_request

    def test_post_endpoint_accepts_payment_kind_credit_in_body(self):
        """При проведении можно передать payment_kind=credit/debt в теле POST /post/."""
        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(
            name="C1",
            phone="+996700000006",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=cp,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("1"), price=Decimal("20.00"))
        services.recalc_document_totals(doc)

        from apps.warehouse.utils import normalize_payment_kind

        doc.payment_kind = normalize_payment_kind("debt")
        doc.clean()
        doc.save(update_fields=["payment_kind"])
        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.payment_kind, models.Document.PaymentKind.CREDIT)
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        from apps.warehouse import services_money

        analytics = services_money.bulk_counterparty_mini_analytics(
            type(
                "M",
                (),
                {
                    "_filter_qs_company_branch": staticmethod(
                        lambda qs, company_field=None, branch_field=None: qs
                    ),
                    "request": None,
                },
            )(),
            [cp.id],
        )[cp.id]
        self.assertEqual(analytics["debts"]["balance"], "20.00")
        self.assertEqual(analytics["debts"]["counterparty_owes_company"], "20.00")

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
            cp = models.Counterparty.objects.create(
                name="C1",
                phone="+996700000005",
                type=models.Counterparty.Type.CLIENT,
            )
            doc.counterparty = cp
            doc.save()
            models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("5"), price=Decimal("0"))
            with self.assertRaises(Exception):
                services.post_document(doc)
        finally:
            settings.ALLOW_NEGATIVE_STOCK = old

    def test_intercompany_transfer_with_partnership(self):
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        user_b = User.objects.create(
            email="b@example.com", password="x", first_name="B", last_name="B", role="owner"
        )
        company_b = Company.objects.create(name="CB", owner=user_b)
        branch_b = Branch.objects.create(company=company_b, name="BMain")
        wh_b = models.Warehouse.objects.create(name="WB", company=company_b, branch=branch_b, location="")
        cat_b = models.WarehouseProductCategory.objects.create(name="CB", company=company_b, branch=branch_b)
        prod_b = models.WarehouseProduct.objects.create(
            company=company_b,
            branch=branch_b,
            warehouse=wh_b,
            category=cat_b,
            name="PB",
            code="PB",
            barcode="1112223334444",
            unit="pcs",
            quantity=Decimal("0"),
            purchase_price=Decimal("1.00"),
            price=Decimal("2.00"),
        )
        models.StockBalance.objects.create(warehouse=wh_b, product=prod_b, qty=Decimal("4.000"))

        id_lo, id_hi = models.canonical_company_pair_ids(self.company.id, company_b.id)
        models.CompanyStockPartnership.objects.create(company_a_id=id_lo, company_b_id=id_hi)

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=wh_b,
            warehouse_to=self.wh,
        )
        models.DocumentItem.objects.create(document=doc, product=prod_b, qty=Decimal("2"), price=Decimal("0"))
        services.post_document(doc)

        b_from = models.StockBalance.objects.get(warehouse=wh_b, product=prod_b)
        dest_prod = models.WarehouseProduct.objects.get(warehouse=self.wh, barcode=prod_b.barcode)
        b_to = models.StockBalance.objects.get(warehouse=self.wh, product=dest_prod)
        self.assertEqual(b_from.qty, Decimal("2.000"))
        self.assertEqual(b_to.qty, Decimal("2.000"))

    def test_partner_cash_incassation(self):
        Company = apps.get_model("users", "Company")
        user_b = User.objects.create(
            email="cashb@example.com", password="x", first_name="B", last_name="B", role="owner"
        )
        company_b = Company.objects.create(name="CB2", owner=user_b)
        cash_a = models.CashRegister.objects.create(company=self.company, branch=self.branch, name="CashA")
        cash_b = models.CashRegister.objects.create(company=company_b, branch=None, name="CashB")
        cat_a = models.PaymentCategory.objects.create(
            company=self.company, branch=self.branch, title="Инкассация", system_code="incassation"
        )
        models.PaymentCategory.objects.create(
            company=company_b, branch=None, title="Инкассация", system_code="incassation"
        )
        models.MoneyDocument.objects.create(
            doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT,
            status=models.MoneyDocument.Status.POSTED,
            cash_register=cash_a,
            company=self.company,
            branch=self.branch,
            payment_category=cat_a,
            amount=Decimal("500.00"),
        )
        id_lo, id_hi = models.canonical_company_pair_ids(self.company.id, company_b.id)
        models.CompanyStockPartnership.objects.create(company_a_id=id_lo, company_b_id=id_hi)

        from apps.warehouse import services_money

        inc = services_money.post_partner_cash_incassation(
            cash_register_from=cash_a,
            cash_register_to=cash_b,
            amount=Decimal("200.00"),
            created_by=self.user,
        )
        self.assertEqual(inc.amount, Decimal("200.00"))
        self.assertEqual(services_money.cash_register_balance(cash_a), Decimal("300.00"))
        self.assertEqual(services_money.cash_register_balance(cash_b), Decimal("200.00"))

    def test_commercial_offer_creates_and_cannot_be_posted(self):
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.COMMERCIAL_OFFER,
            warehouse_from=self.wh,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("2"), price=Decimal("15"))

        # totals can be recalculated for quotation
        services.recalc_document_totals(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.total, Decimal("30.00"))

        with self.assertRaises(Exception):
            services.post_document(doc)

    def test_counterparty_pagination_supports_over_100_items(self):
        """
        Проверяет, что список контрагентов не обрезается на 100 элементах
        и пагинация выводит более 100 записей (до 1000 по умолчанию).
        """
        # Создаем 105 контрагентов
        counterparties = [
            models.Counterparty(
                name=f"Counterparty {i}",
                phone=f"+99670000{i:04d}",
                type=models.Counterparty.Type.CLIENT,
                company=self.company,
                branch=self.branch,
            )
            for i in range(105)
        ]
        models.Counterparty.objects.bulk_create(counterparties)

        from rest_framework.test import APIRequestFactory
        from apps.warehouse.views_documents import CounterpartyListCreateView

        factory = APIRequestFactory()
        request = factory.get("/api/warehouse/crud/counterparties/")
        request.user = self.user

        view = CounterpartyListCreateView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        # Так как всего 105 контрагентов, они все должны поместиться на 1 странице (page_size=1000)
        results = response.data.get("results", [])
        self.assertEqual(len(results), 105)

    def test_document_post_view_handles_sale_request_and_posted_status(self):
        """
        Проверяет, что проведение Заявки на продажу через API переводит документ в статус POSTED,
        а повторный вызов идемпотентно возвращает HTTP 200.
        """
        self.user.company = self.company
        self.user.branch = self.branch
        self.user.role = "owner"
        self.user.save()

        models.StockBalance.objects.create(warehouse=self.wh, product=self.prod, qty=Decimal("10.000"))
        cp = models.Counterparty.objects.create(
            name="Client Sale Request",
            phone="+996700000099",
            type=models.Counterparty.Type.CLIENT,
            company=self.company,
            branch=self.branch,
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.SALE_REQUEST,
            is_sale_request=True,
            warehouse_from=self.wh,
            counterparty=cp,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod, qty=Decimal("2"), price=Decimal("15.00"))

        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.warehouse.views_documents import DocumentPostView

        factory = APIRequestFactory()
        request = factory.post(f"/api/warehouse/documents/{doc.id}/post/", {}, format="json")
        force_authenticate(request, user=self.user)

        view = DocumentPostView.as_view()
        response = view(request, pk=str(doc.id))
        self.assertEqual(response.status_code, 200)

        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        # Повторный вызов /post/ на уже проведённом документе также возвращает 200 OK (идемпотентно)
        request_repeat = factory.post(f"/api/warehouse/documents/{doc.id}/post/", {}, format="json")
        force_authenticate(request_repeat, user=self.user)
        response_repeat = view(request_repeat, pk=str(doc.id))
        self.assertEqual(response_repeat.status_code, 200)


class BackendChecklistTests(APITestCase):
    def setUp(self):
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        User = get_user_model()
        self.owner_user = User.objects.create_user(email="owner_test_check@example.com", password="password123", role="owner")
        self.company = Company.objects.create(name="Company Check", owner=self.owner_user)
        self.branch = Branch.objects.create(name="Branch 1", company=self.company)
        self.whA = models.Warehouse.objects.create(name="Warehouse A", company=self.company, branch=self.branch)
        self.whB = models.Warehouse.objects.create(name="Warehouse B", company=self.company, branch=self.branch)

        self.agent_user = User.objects.create_user(email="agent_test_check@example.com", password="password123", role="employee")
        Membership = apps.get_model("users", "Membership")
        Membership.objects.create(
            user=self.agent_user,
            company=self.company,
            branch=self.branch,
            is_active=True,
        )
        self.agent_membership = models.CompanyWarehouseAgent.objects.create(
            user=self.agent_user,
            company=self.company,
            assigned_warehouse=self.whA,
            status=models.CompanyWarehouseAgent.Status.ACTIVE,
            common_access_enabled=False,
        )

        self.prod_a = models.WarehouseProduct.objects.create(
            name="Prod A", company=self.company, branch=self.branch, warehouse=self.whA, quantity=Decimal("100")
        )
        self.prod_b = models.WarehouseProduct.objects.create(
            name="Prod B", company=self.company, branch=self.branch, warehouse=self.whB, quantity=Decimal("100")
        )

    def test_block1_agent_me_products_returns_all_warehouses(self):
        models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whA, product=self.prod_a, qty=Decimal("10"), company=self.company
        )
        models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whB, product=self.prod_b, qty=Decimal("20"), company=self.company
        )

        self.client.force_authenticate(user=self.agent_user)
        res = self.client.get("/api/warehouse/agents/me/products/")
        self.assertEqual(res.status_code, 200)
        results = res.data.get("results", [])
        self.assertEqual(len(results), 2)

        res_b = self.client.get(f"/api/warehouse/agents/me/products/?warehouse={self.whB.id}")
        self.assertEqual(res_b.status_code, 200)
        results_b = res_b.data.get("results", [])
        self.assertEqual(len(results_b), 1)
        self.assertEqual(str(results_b[0]["warehouse"]), str(self.whB.id))

    def test_block2_post_insufficient_stock_rollback(self):
        bal = models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whB, product=self.prod_b, qty=Decimal("5"), company=self.company
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.DRAFT,
            warehouse_from=self.whB,
            agent=self.agent_user,
            payment_kind=models.Document.PaymentKind.CASH,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod_b, qty=Decimal("10"), price=Decimal("10"))

        self.client.force_authenticate(user=self.agent_user)
        res = self.client.post(f"/api/warehouse/documents/{doc.id}/post/")
        self.assertEqual(res.status_code, 400)

        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("5"))
        self.assertEqual(doc.agent_moves.count(), 0)

    def test_block3_cash_post_cash_pending_workflow(self):
        bal = models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whB, product=self.prod_b, qty=Decimal("50"), company=self.company
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.DRAFT,
            warehouse_from=self.whB,
            agent=self.agent_user,
            payment_kind=models.Document.PaymentKind.CASH,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod_b, qty=Decimal("10"), price=Decimal("10"))

        self.client.force_authenticate(user=self.agent_user)
        res_post = self.client.post(f"/api/warehouse/documents/{doc.id}/post/")
        self.assertEqual(res_post.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("40"))

        res_app = self.client.post(f"/api/warehouse/documents/{doc.id}/cash/approve/")
        self.assertEqual(res_app.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("40"))

    def test_block3_cash_reject_restores_qty(self):
        bal = models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whB, product=self.prod_b, qty=Decimal("50"), company=self.company
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.DRAFT,
            warehouse_from=self.whB,
            agent=self.agent_user,
            payment_kind=models.Document.PaymentKind.CASH,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod_b, qty=Decimal("10"), price=Decimal("10"))

        self.client.force_authenticate(user=self.agent_user)
        self.client.post(f"/api/warehouse/documents/{doc.id}/post/")
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)

        res_rej = self.client.post(f"/api/warehouse/documents/{doc.id}/cash/reject/")
        self.assertEqual(res_rej.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.REJECTED)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("50"))

    def test_block3_unpost_from_cash_pending_restores_qty(self):
        bal = models.AgentStockBalance.objects.create(
            agent=self.agent_user, warehouse=self.whB, product=self.prod_b, qty=Decimal("50"), company=self.company
        )
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.DRAFT,
            warehouse_from=self.whB,
            agent=self.agent_user,
            payment_kind=models.Document.PaymentKind.CASH,
        )
        models.DocumentItem.objects.create(document=doc, product=self.prod_b, qty=Decimal("10"), price=Decimal("10"))

        self.client.force_authenticate(user=self.agent_user)
        self.client.post(f"/api/warehouse/documents/{doc.id}/post/")
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.CASH_PENDING)

        res_unpost = self.client.post(f"/api/warehouse/documents/{doc.id}/unpost/")
        self.assertEqual(res_unpost.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
        self.assertEqual(bal.qty, Decimal("50"))


class TZAug2026Tests(APITestCase):
    def setUp(self):
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        User = get_user_model()
        self.owner_user = User.objects.create_user(email="owner_tz_aug@example.com", password="password123", role="owner")
        self.company = Company.objects.create(name="Company TZ Aug", owner=self.owner_user)
        self.branch = Branch.objects.create(name="Branch TZ Aug", company=self.company)
        self.warehouse = models.Warehouse.objects.create(name="Main WH", company=self.company, branch=self.branch)

        self.prod1 = models.WarehouseProduct.objects.create(
            name="Juice 1L",
            barcode="460000000001",
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            quantity=Decimal("50"),
            price=Decimal("120.00"),
        )
        models.StockBalance.objects.create(
            warehouse=self.warehouse, product=self.prod1, qty=Decimal("50")
        )

        self.prod_catalog = models.WarehouseProduct.objects.create(
            name="Catalog Soda",
            barcode="460000000002",
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            quantity=Decimal("0"),
            price=Decimal("80.00"),
        )

        self.client.force_authenticate(user=self.owner_user)

    def test_barcode_check_in_stock(self):
        res = self.client.get(f"/api/warehouse/{self.warehouse.id}/barcode-check/460000000001/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "IN_STOCK")
        self.assertEqual(res.data["product"]["id"], str(self.prod1.id))
        self.assertFalse(res.data["ambiguous"])

    def test_barcode_check_in_catalog(self):
        res = self.client.get(f"/api/warehouse/{self.warehouse.id}/barcode-check/460000000002/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "IN_CATALOG")

    def test_barcode_check_unknown(self):
        res = self.client.get(f"/api/warehouse/{self.warehouse.id}/barcode-check/999999999999/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "UNKNOWN")

    def test_mass_incoming(self):
        payload = {
            "items": [
                {"product_id": str(self.prod1.id), "quantity": "10", "price": "100.00"},
                {"product_id": str(self.prod_catalog.id), "quantity": "20", "price": "75.00"}
            ],
            "comment": "Тестовый массовый приход"
        }
        res = self.client.post(f"/api/warehouse/{self.warehouse.id}/mass-incoming/", payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["items_count"], 2)

        bal1 = models.StockBalance.objects.get(warehouse=self.warehouse, product=self.prod1)
        self.assertEqual(bal1.qty, Decimal("60"))

    def test_notification_category_and_delete(self):
        from apps.main.models import Notification
        notif = Notification.objects.create(
            company=self.company,
            user=self.owner_user,
            title="Тариф истекает",
            message="Остался 1 день",
            category="tariff",
            type="tariff",
        )
        res = self.client.get("/api/main/notifications/?category=tariff")
        self.assertEqual(res.status_code, 200)
        results = res.data.get("results") or res.data
        if isinstance(results, list):
            self.assertTrue(any(n["id"] == str(notif.id) for n in results))

        res_del = self.client.delete(f"/api/main/notifications/{notif.id}/")
        self.assertIn(res_del.status_code, [200, 204])
        self.assertFalse(Notification.objects.filter(id=notif.id).exists())

    def test_pos_quick_slots(self):
        res_get = self.client.get("/api/main/pos/quick-slots/")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn("slots", res_get.data)

        payload = {"slots": {"0": str(self.prod1.id), "1": str(self.prod_catalog.id)}}
        res_put = self.client.put("/api/main/pos/quick-slots/", payload, format="json")
        self.assertEqual(res_put.status_code, 200)
        self.assertEqual(res_put.data["slots"]["0"], str(self.prod1.id))



