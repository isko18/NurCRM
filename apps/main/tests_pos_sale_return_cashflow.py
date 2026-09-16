from decimal import Decimal
import uuid
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from apps.users.models import Company, Branch, User, Roles
from apps.construction.models import Cashbox, CashShift, CashFlow
from apps.construction.auto_cashflow import create_auto_cashflow, handle_cashflow_reject
from apps.main.models import (
    Sale,
    SaleItem,
    SalePayment,
    Product,
    Client,
    ClientDeal,
    DealInstallment,
    SaleReturn,
)
from apps.main.pos_views import SaleReturnAPIView


class POSSaleReturnCashflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner_pos@test.kg",
            password="testpassword",
            is_staff=True,
            is_superuser=True,
        )
        self.company = Company.objects.create(name="POS Test Company", owner=self.owner, is_active=True)
        self.owner.company = self.company
        self.owner.role = "owner"
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashier = User.objects.create_user(
            email="cashier_pos_test@test.kg",
            password="testpassword",
            company=self.company,
            role="cashier",
        )
        self.cashier.branches.add(self.branch)
        self.cashbox = Cashbox.objects.create(
            name="Касса 1",
            company=self.company,
            branch=self.branch,
            role=Cashbox.CashboxRole.POS_MAIN,
        )
        self.shift = CashShift.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            cashier=self.cashier,
            status=CashShift.Status.OPEN,
            opening_cash=Decimal("500.00"),
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Товар А",
            price=Decimal("500.00"),
            quantity=Decimal("20.00"),
        )
        self.client = Client.objects.create(
            company=self.company,
            branch=self.branch,
            full_name="Клиент Тест",
            phone="+996555111222",
        )
        self.factory = APIRequestFactory()

    def _create_paid_sale(self, quantity=2, price=Decimal("500.00"), payment_method="cash", payments=None):
        total = Decimal(str(quantity)) * price
        sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            shift=self.shift,
            user=self.cashier,
            client=self.client,
            payment_method=payment_method,
            status=Sale.Status.PAID,
            subtotal=total,
            total=total,
        )
        SaleItem.objects.create(
            company=self.company,
            branch=self.branch,
            sale=sale,
            product=self.product,
            unit_price=price,
            quantity=Decimal(str(quantity)),
            name_snapshot=self.product.name,
        )
        # Deduct initial stock
        self.product.quantity -= Decimal(str(quantity))
        self.product.save(update_fields=["quantity"])

        if payments:
            for p in payments:
                SalePayment.objects.create(
                    company=self.company,
                    sale=sale,
                    method=p["method"],
                    amount=p["amount"],
                )
        elif payment_method != "debt":
            SalePayment.objects.create(
                company=self.company,
                sale=sale,
                method=payment_method,
                amount=total,
            )

        # Original income flow
        create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.cashier,
            shift=self.shift,
            type=CashFlow.Type.INCOME,
            amount=total,
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id=str(sale.id),
            affects_shift_drawer=(payment_method == "cash"),
        )
        return sale

    def test_full_cash_return_creates_compensating_cashflow_and_affects_drawer(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        self.assertEqual(self.product.quantity, Decimal("18.00"))

        # Shift expected cash before return: 500 + 1000 = 1500
        totals = self.shift.calc_live_totals()
        self.assertEqual(totals["drawer_expected_cash"], Decimal("1500.00"))

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)

        self.assertEqual(res.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CANCELED)

        # Stock restored
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("20.00"))

        # Compensating expense created
        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.type, CashFlow.Type.EXPENSE)
        self.assertEqual(return_flow.amount, Decimal("1000.00"))
        self.assertTrue(return_flow.affects_shift_drawer)
        self.assertEqual(return_flow.shift_id, self.shift.id)

        # Shift expected cash after return: 1500 - 1000 = 500
        totals = self.shift.calc_live_totals()
        self.assertEqual(totals["drawer_expected_cash"], Decimal("500.00"))

    def test_full_card_return_does_not_affect_shift_drawer(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="transfer")

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.type, CashFlow.Type.EXPENSE)
        self.assertEqual(return_flow.amount, Decimal("1000.00"))
        self.assertFalse(return_flow.affects_shift_drawer)

        # Drawer cash remains 500
        totals = self.shift.calc_live_totals()
        self.assertEqual(totals["drawer_expected_cash"], Decimal("500.00"))

    def test_partial_return_sets_partially_returned_and_creates_proportional_cashflow(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        item = sale.items.first()

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {
                "items": [{"sale_item_id": str(item.id), "quantity": 1}],
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PARTIALLY_RETURNED)
        self.assertEqual(sale.total, Decimal("500.00"))

        # Restocked 1 item
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("19.00"))

        # Compensating cashflow of 500
        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.amount, Decimal("500.00"))
        self.assertTrue(return_flow.affects_shift_drawer)

    def test_split_payment_return_creates_separate_expenses(self):
        payments = [
            {"method": "cash", "amount": Decimal("600.00")},
            {"method": "transfer", "amount": Decimal("400.00")},
        ]
        sale = self._create_paid_sale(
            quantity=2, price=Decimal("500.00"), payment_method="mixed", payments=payments
        )

        idemp = str(uuid.uuid4())
        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": idemp},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        flows = list(
            CashFlow.objects.filter(
                company=self.company,
                source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
                source_id=str(sale.id),
            ).order_by("amount")
        )
        self.assertEqual(len(flows), 2)
        transfer_flow = [f for f in flows if not f.affects_shift_drawer][0]
        cash_flow = [f for f in flows if f.affects_shift_drawer][0]

        self.assertEqual(transfer_flow.amount, Decimal("400.00"))
        self.assertFalse(transfer_flow.affects_shift_drawer)
        self.assertEqual(cash_flow.amount, Decimal("600.00"))
        self.assertTrue(cash_flow.affects_shift_drawer)

    def test_debt_sale_with_prepayment_returns_cash_refund(self):
        total = Decimal("2000.00")
        prepay = Decimal("500.00")
        sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            shift=self.shift,
            user=self.cashier,
            client=self.client,
            payment_method="debt",
            status=Sale.Status.DEBT,
            subtotal=total,
            total=total,
            cash_received=prepay,
        )
        SaleItem.objects.create(
            company=self.company,
            branch=self.branch,
            sale=sale,
            product=self.product,
            unit_price=Decimal("1000.00"),
            quantity=Decimal("2.00"),
            name_snapshot="Товар 2000",
        )
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client,
            sale=sale,
            title="Долг с предоплатой",
            kind=ClientDeal.Kind.DEBT,
            amount=total,
            prepayment=prepay,
            debt_days=30,
        )
        self.assertEqual(deal.remaining_debt, Decimal("1500.00"))

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        deal.refresh_from_db()
        self.assertEqual(deal.remaining_debt, Decimal("0.00"))

        # Cash refund flow created for prepayment amount
        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.amount, Decimal("500.00"))
        self.assertTrue(return_flow.affects_shift_drawer)

    def test_pure_debt_sale_no_cashflow(self):
        total = Decimal("1000.00")
        sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            shift=self.shift,
            user=self.cashier,
            client=self.client,
            payment_method="debt",
            status=Sale.Status.DEBT,
            subtotal=total,
            total=total,
            cash_received=Decimal("0.00"),
        )
        SaleItem.objects.create(
            company=self.company,
            branch=self.branch,
            sale=sale,
            product=self.product,
            unit_price=Decimal("500.00"),
            quantity=Decimal("2.00"),
            name_snapshot="Товар",
        )
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client,
            sale=sale,
            title="Чистый долг",
            kind=ClientDeal.Kind.DEBT,
            amount=total,
            prepayment=Decimal("0.00"),
            debt_days=30,
        )

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        deal.refresh_from_db()
        self.assertEqual(deal.remaining_debt, Decimal("0.00"))
        # No cashflow created
        self.assertFalse(
            CashFlow.objects.filter(
                company=self.company,
                source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
                source_id=str(sale.id),
            ).exists()
        )

    def test_is_defect_product_not_restocked_but_money_returned(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        self.assertEqual(self.product.quantity, Decimal("18.00"))

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"is_defect": True, "idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        res = SaleReturnAPIView.as_view()(req, pk=sale.id)
        self.assertEqual(res.status_code, 200)

        # Product quantity NOT restocked
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("18.00"))

        # But money is returned
        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.amount, Decimal("1000.00"))

    def test_idempotency_duplicate_key_returns_same_response(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        idemp = str(uuid.uuid4())

        req1 = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": idemp},
            format="json",
        )
        force_authenticate(req1, user=self.cashier)
        res1 = SaleReturnAPIView.as_view()(req1, pk=sale.id)
        self.assertEqual(res1.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("20.00"))

        cf_count = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).count()
        self.assertEqual(cf_count, 1)

        # Retry with SAME idempotency_key
        req2 = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": idemp},
            format="json",
        )
        force_authenticate(req2, user=self.cashier)
        res2 = SaleReturnAPIView.as_view()(req2, pk=sale.id)
        self.assertEqual(res2.status_code, 200)

        # Stock not double-restocked
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("20.00"))

        # Cashflow not duplicated
        cf_count_after = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).count()
        self.assertEqual(cf_count_after, 1)

    def test_reject_pos_sale_return_raises_conflict(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        SaleReturnAPIView.as_view()(req, pk=sale.id)

        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()

        return_flow.status = CashFlow.Status.REJECTED
        return_flow.save()

        with self.assertRaises(ValidationError) as ctx:
            handle_cashflow_reject(return_flow, user=self.cashier)
        self.assertEqual(ctx.exception.get_codes(), {"detail": "reject_cascade_failed"})

    def test_reject_pos_sale_when_already_returned_raises_conflict(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        orig_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id=str(sale.id),
        ).first()

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        SaleReturnAPIView.as_view()(req, pk=sale.id)

        orig_flow.status = CashFlow.Status.REJECTED
        orig_flow.save()

        with self.assertRaises(ValidationError) as ctx:
            handle_cashflow_reject(orig_flow, user=self.cashier)
        self.assertEqual(ctx.exception.get_codes(), {"detail": "reject_cascade_failed"})
