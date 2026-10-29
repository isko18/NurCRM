from decimal import Decimal
import uuid
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import Company, Branch, User
from apps.construction.models import Cashbox, CashShift, CashFlow
from apps.main.models import (
    Cart,
    CartItem,
    Sale,
    Product,
    Client,
    ClientDeal,
    DealInstallment,
)
from apps.main.pos_views import SaleCheckoutAPIView, SaleReturnAPIView
from apps.construction.views import build_shift_report


class DebtPrepaymentMethodTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner_test_debt@test.kg",
            password="testpassword",
            is_staff=True,
            is_superuser=True,
        )
        self.company = Company.objects.create(name="Debt Prepay Test Co", owner=self.owner, is_active=True)
        self.owner.company = self.company
        self.owner.role = "owner"
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashier = User.objects.create_user(
            email="cashier_debt@test.kg",
            password="testpassword",
            company=self.company,
            role="cashier",
        )
        self.cashier.branches.add(self.branch)
        self.cashbox = Cashbox.objects.create(
            name="Касса 1",
            company=self.company,
            branch=self.branch,
            role=Cashbox.CashboxRole.POS_BRANCH,
            is_active=True,
        )
        self.shift = CashShift.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            cashier=self.cashier,
            status=CashShift.Status.OPEN,
            opening_cash=Decimal("5000.00"),
        )
        self.client = Client.objects.create(
            company=self.company,
            full_name="Иван Должников",
            phone="+996555111222",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Дорогой товар",
            price=Decimal("10000.00"),
            quantity=Decimal("100.00"),
        )
        self.factory = APIRequestFactory()

    def _create_cart(self, price=Decimal("10000.00")):
        cart = Cart.objects.create(
            company=self.company,
            branch=self.branch,
            user=self.cashier,
            shift=self.shift,
            status=Cart.Status.ACTIVE,
        )
        CartItem.objects.create(
            company=self.company,
            cart=cart,
            product=self.product,
            unit_price=price,
            quantity=Decimal("1.000"),
        )
        cart.recalc()
        return cart

    def test_case_1_without_prepayment_method_defaults_to_cash(self):
        """1: без prepayment_method -> pos_prepayment 2000, affects_shift_drawer=True, drawer_expected_cash +2000"""
        cart = self._create_cart()
        before_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]

        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        self.assertEqual(sale.status, Sale.Status.DEBT)
        self.assertEqual(sale.cash_amount, Decimal("2000.00"))
        self.assertEqual(sale.card_amount, Decimal("0.00"))
        self.assertEqual(sale.paid_now, Decimal("2000.00"))
        self.assertEqual(sale.debt_initial, Decimal("8000.00"))

        # Payload checks
        self.assertEqual(res.data["paid_cash"], "2000.00")
        self.assertEqual(res.data["paid_card"], "0.00")

        # Cashflow checks
        cf = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(cf)
        self.assertEqual(cf.amount, Decimal("2000.00"))
        self.assertTrue(cf.affects_shift_drawer)
        self.assertEqual(cf.payment_method, "cash")

        after_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]
        self.assertEqual(after_drawer - before_drawer, Decimal("2000.00"))

    def test_case_2_prepayment_method_cash(self):
        """2: prepayment_method: 'cash' -> то же, что №1"""
        cart = self._create_cart()
        before_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]

        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "cash",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        self.assertEqual(sale.cash_amount, Decimal("2000.00"))
        self.assertEqual(sale.card_amount, Decimal("0.00"))

        cf = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(cf)
        self.assertEqual(cf.amount, Decimal("2000.00"))
        self.assertTrue(cf.affects_shift_drawer)
        self.assertEqual(cf.payment_method, "cash")

        after_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]
        self.assertEqual(after_drawer - before_drawer, Decimal("2000.00"))

    def test_case_3_prepayment_method_mbank(self):
        """3: prepayment_method: 'mbank' -> pos_prepayment 2000, payment_method=mbank, affects_shift_drawer=False, drawer_expected_cash без изменений, noncash_sales_total +2000"""
        cart = self._create_cart()
        before_totals = self.shift.calc_live_totals(refresh=True)
        before_drawer = before_totals["drawer_expected_cash"]
        before_noncash = before_totals["noncash_sales_total"]

        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "mbank",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        self.assertEqual(sale.cash_amount, Decimal("0.00"))
        self.assertEqual(sale.card_amount, Decimal("2000.00"))
        self.assertEqual(sale.paid_now, Decimal("2000.00"))
        self.assertEqual(sale.debt_initial, Decimal("8000.00"))

        # Check SalePayment lines
        pm_lines = list(sale.payments.all().order_by("method"))
        self.assertEqual(len(pm_lines), 2)
        debt_line = next(p for p in pm_lines if p.method == "debt")
        mbank_line = next(p for p in pm_lines if p.method == "mbank")
        self.assertEqual(debt_line.amount, Decimal("8000.00"))
        self.assertEqual(mbank_line.amount, Decimal("2000.00"))

        # Payload checks
        self.assertEqual(res.data["paid_cash"], "0.00")
        self.assertEqual(res.data["paid_card"], "2000.00")

        # Cashflow checks
        cf = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(cf)
        self.assertEqual(cf.amount, Decimal("2000.00"))
        self.assertFalse(cf.affects_shift_drawer)
        self.assertEqual(cf.payment_method, "mbank")

        after_totals = self.shift.calc_live_totals(refresh=True)
        # drawer_expected_cash is unchanged!
        self.assertEqual(after_totals["drawer_expected_cash"], before_drawer)
        # noncash_sales_total increased by 2000
        self.assertEqual(after_totals["noncash_sales_total"] - before_noncash, Decimal("2000.00"))
        self.assertEqual(after_totals["debt_prepayments_noncash"], Decimal("2000.00"))

        # Check shift report
        report = build_shift_report(self.shift)
        self.assertEqual(report["debt_prepayments_noncash"], "2000.00")
        self.assertEqual(report["noncash_sales"], "2000.00")

    def test_case_4_unknown_method_fallbacks_to_transfer(self):
        """4: prepayment_method: 'foo' -> трактуется как transfer, не 400"""
        cart = self._create_cart()
        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "foo",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        self.assertEqual(sale.card_amount, Decimal("2000.00"))

        # Transfer line in payments
        transfer_line = sale.payments.filter(method="transfer").first()
        self.assertIsNotNone(transfer_line)
        self.assertEqual(transfer_line.amount, Decimal("2000.00"))

        cf = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(cf)
        self.assertEqual(cf.payment_method, "transfer")
        self.assertFalse(cf.affects_shift_drawer)

    def test_case_5_mbank_with_deal_creation(self):
        """5: №3 + сделка -> deal.prepayment = 2000, остаток 8000, график не затронут"""
        cart = self._create_cart()
        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "mbank",
            "schedule_version": "v2",
            "debt_schedule": {
                "count": 2,
                "interval_days": 15,
                "first_due_date": "2026-10-15",
                "installments": [
                    {"order": 1, "amount": "4000.00", "due_date": "2026-10-15"},
                    {"order": 2, "amount": "4000.00", "due_date": "2026-10-30"},
                ],
            },
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        deal = ClientDeal.objects.filter(sale=sale).first()
        self.assertIsNotNone(deal)
        self.assertEqual(deal.amount, Decimal("10000.00"))
        self.assertEqual(deal.prepayment, Decimal("2000.00"))
        self.assertEqual(deal.remaining_debt, Decimal("8000.00"))

    def test_case_6_return_of_mbank_prepayment_sale(self):
        """6: Возврат чека №3 целиком -> откат долга 8000 + expense 2000 payment_method=mbank, affects_shift_drawer=False"""
        cart = self._create_cart()
        checkout_data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "mbank",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", checkout_data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        deal = ClientDeal.objects.filter(sale=sale).first()
        self.assertIsNotNone(deal)

        before_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]

        # Return full sale
        ret_req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(ret_req, user=self.cashier)
        ret_res = SaleReturnAPIView.as_view()(ret_req, pk=sale.id)
        self.assertEqual(ret_res.status_code, 200)

        # Deal is cancelled / remaining debt is 0
        deal.refresh_from_db()
        self.assertEqual(deal.remaining_debt, Decimal("0.00"))

        # Cashflow for return is 2000, mbank, affects_shift_drawer=False
        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.amount, Decimal("2000.00"))
        self.assertEqual(return_flow.payment_method, "mbank")
        self.assertFalse(return_flow.affects_shift_drawer)

        # Drawer expected cash is still unchanged!
        after_drawer = self.shift.calc_live_totals(refresh=True)["drawer_expected_cash"]
        self.assertEqual(after_drawer, before_drawer)

    def test_case_7_return_of_cash_prepayment_sale(self):
        """7: Возврат чека №1 целиком -> откат долга + expense 2000 нал"""
        cart = self._create_cart()
        checkout_data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", checkout_data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        deal = ClientDeal.objects.filter(sale=sale).first()
        self.assertIsNotNone(deal)

        ret_req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(ret_req, user=self.cashier)
        ret_res = SaleReturnAPIView.as_view()(ret_req, pk=sale.id)
        self.assertEqual(ret_res.status_code, 200)

        deal.refresh_from_db()
        self.assertEqual(deal.remaining_debt, Decimal("0.00"))

        return_flow = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id),
        ).first()
        self.assertIsNotNone(return_flow)
        self.assertEqual(return_flow.amount, Decimal("2000.00"))
        self.assertEqual(return_flow.payment_method, "cash")

    def test_case_8_idempotency_retry(self):
        """8: Повтор №3 с тем же idempotency_key -> 200, без дубля pos_prepayment"""
        cart = self._create_cart()
        idemp = str(uuid.uuid4())
        data = {
            "print_receipt": False,
            "client_id": str(self.client.id),
            "payment_method": "debt",
            "cash_received": "2000.00",
            "prepayment_method": "mbank",
            "idempotency_key": idemp,
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req1 = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req1, user=self.cashier)
        res1 = SaleCheckoutAPIView.as_view()(req1, pk=cart.id)
        self.assertEqual(res1.status_code, 201)

        cf_count = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=res1.data["sale_id"],
        ).count()
        self.assertEqual(cf_count, 1)

        # Retry
        req2 = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req2, user=self.cashier)
        res2 = SaleCheckoutAPIView.as_view()(req2, pk=cart.id)
        self.assertEqual(res2.status_code, 201)

        cf_count_after = CashFlow.objects.filter(
            company=self.company,
            source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
            source_id=res1.data["sale_id"],
        ).count()
        self.assertEqual(cf_count_after, 1)

    def test_case_9_cash_sale_ignores_prepayment_method(self):
        """9: payment_method: 'cash' + prepayment_method: 'mbank' -> prepayment_method игнорируется"""
        cart = self._create_cart()
        data = {
            "print_receipt": False,
            "payment_method": "cash",
            "cash_received": "10000.00",
            "prepayment_method": "mbank",
            "branch_id": str(self.branch.id),
            "shift_id": str(self.shift.id),
        }
        req = self.factory.post(f"/main/pos/sales/{cart.id}/checkout/", data, format="json")
        force_authenticate(req, user=self.cashier)
        res = SaleCheckoutAPIView.as_view()(req, pk=cart.id)
        self.assertEqual(res.status_code, 201)

        sale = Sale.objects.get(id=res.data["sale_id"])
        self.assertEqual(sale.payment_method, "cash")
        self.assertEqual(sale.cash_amount, Decimal("10000.00"))
        self.assertEqual(sale.card_amount, Decimal("0.00"))
        # No pos_prepayment created
        self.assertFalse(
            CashFlow.objects.filter(
                company=self.company,
                source_kind=CashFlow.SourceKind.POS_PREPAYMENT,
                source_id=str(sale.id),
            ).exists()
        )
