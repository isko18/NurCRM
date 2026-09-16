from datetime import date
from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.construction.auto_cashflow import (
    resolve_cashbox,
    create_auto_cashflow,
    serialize_auto_cashflows,
    handle_cashflow_reject,
)
from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.main.models import Branch, Client, ClientDeal, DealInstallment, DealPayment, Product, Sale, SalePayment
from apps.users.models import Company

User = get_user_model()


class ShiftReconciliationTests(TestCase):
    def setUp(self):
        email = f"cashier_{uuid.uuid4().hex[:8]}@test.com"
        self.user = User.objects.create_user(email=email, password="pass")
        self.company = Company.objects.create(name=f"Test Company {uuid.uuid4().hex[:6]}", owner=self.user)
        self.user.company = self.company
        self.user.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        self.pos_cashbox = Cashbox.objects.create(
            name="Касса филиала",
            role=Cashbox.CashboxRole.POS_BRANCH,
            company=self.company,
            branch=self.branch,
        )
        self.expense_cashbox = Cashbox.objects.create(
            name="Переменные расходы",
            role=Cashbox.CashboxRole.EXPENSE_VARIABLE,
            company=self.company,
        )

    def tearDown(self):
        try:
            self.company.delete()
            self.user.delete()
        except Exception:
            pass

    def test_golden_scenario_reconciliation(self):
        """
        Golden scenario:
        - Opening cash = 50.00
        - Cash sales total = 33,768.00
        - Warehouse purchases (non-drawer) = 54,625.00
        - Drawer expected cash = 50 + 33768 = 33,818.00
        - Ledger expected cash = 50 + 33768 - 54625 = -20,807.00
        - Closing cash = 8,888.00
        - cash_diff = 8,888.00 - 33,818.00 = -24,930.00
        """
        shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            cashier=self.user,
            opening_cash=Decimal("50.00"),
            status=CashShift.Status.OPEN,
        )

        # 1. Cash sale
        sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            total=Decimal("33768.00"),
            payment_method=Sale.PaymentMethod.CASH,
            status=Sale.Status.PAID,
        )
        create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("33768.00"),
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id=str(sale.id),
            affects_shift_drawer=False,  # POS sale affects shift via Sale model
        )

        # 2. Warehouse purchase (expense_variable cashbox, shift=None, affects_shift_drawer=False)
        prod = Product.objects.create(
            company=self.company,
            branch=self.branch,
            name="Bulk Stock",
            purchase_price=Decimal("54625.00"),
            quantity=Decimal("1.00"),
            price=Decimal("70000.00"),
        )
        create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.expense_cashbox,
            shift=None,
            user=self.user,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("54625.00"),
            source_kind=CashFlow.SourceKind.WAREHOUSE_PURCHASE,
            source_id=str(prod.id),
            affects_shift_drawer=False,
        )

        # Live totals check
        totals = shift.calc_live_totals()
        self.assertEqual(shift.opening_cash, Decimal("50.00"))
        self.assertEqual(totals["cash_sales_total"], Decimal("33768.00"))
        self.assertEqual(totals["drawer_expected_cash"], Decimal("33818.00"))
        self.assertEqual(totals["expected_cash"], Decimal("33818.00"))

        # Close shift with 8888.00
        shift.close(Decimal("8888.00"))
        shift.refresh_from_db()

        self.assertEqual(shift.status, CashShift.Status.CLOSED)
        self.assertEqual(shift.opening_cash, Decimal("50.00"))
        self.assertEqual(shift.closing_cash, Decimal("8888.00"))
        self.assertEqual(shift.cash_sales_total, Decimal("33768.00"))
        self.assertEqual(shift.drawer_expected_cash, Decimal("33818.00"))
        self.assertEqual(shift.expected_cash, Decimal("33818.00"))
        self.assertEqual(shift.cash_diff, Decimal("-24930.00"))

    def test_drawer_outflow_reduces_drawer_expected_cash(self):
        shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            cashier=self.user,
            opening_cash=Decimal("1000.00"),
            status=CashShift.Status.OPEN,
        )

        # Cash sale 500
        Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            total=Decimal("500.00"),
            payment_method=Sale.PaymentMethod.CASH,
            status=Sale.Status.PAID,
        )

        # Cashflow drawer outflow 200 (approved)
        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("200.00"),
            source_kind=CashFlow.SourceKind.SHIFT_DRAWER_OUTFLOW,
            source_id="manual-outflow-1",
            affects_shift_drawer=True,
        )
        cf.status = CashFlow.Status.APPROVED
        cf.save()

        totals = shift.calc_live_totals()
        self.assertEqual(totals["expected_cash"], Decimal("1300.00"))
        self.assertEqual(totals["drawer_expected_cash"], Decimal("1300.00"))

        shift.close(Decimal("1300.00"))
        shift.refresh_from_db()
        self.assertEqual(shift.cash_diff, Decimal("0.00"))

    def test_split_payment_breakdown(self):
        shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            cashier=self.user,
            opening_cash=Decimal("0.00"),
            status=CashShift.Status.OPEN,
        )

        client = Client.objects.create(full_name="Alice", company=self.company)

        # 1. Cash sale: 100
        Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            total=Decimal("100.00"),
            payment_method=Sale.PaymentMethod.CASH,
            status=Sale.Status.PAID,
        )

        # 2. Mbank sale: 200
        Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            total=Decimal("200.00"),
            payment_method=Sale.PaymentMethod.MBANK,
            status=Sale.Status.PAID,
        )

        # 3. Split sale: Cash 150 + Bakai 150 = 300
        split_sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            client=client,
            total=Decimal("300.00"),
            payment_method=Sale.PaymentMethod.CASH,
            status=Sale.Status.PAID,
        )
        SalePayment.objects.create(sale=split_sale, method=Sale.PaymentMethod.CASH, amount=Decimal("150.00"))
        SalePayment.objects.create(sale=split_sale, method=Sale.PaymentMethod.BAKAI, amount=Decimal("150.00"))

        # 4. Debt sale: 400
        Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_cashbox,
            shift=shift,
            user=self.user,
            client=client,
            total=Decimal("400.00"),
            payment_method=Sale.PaymentMethod.DEBT,
            status=Sale.Status.DEBT,
        )

        breakdown = shift.calc_payment_breakdown()
        methods = {b["method"]: b for b in breakdown}

        self.assertIn("cash", methods)
        self.assertEqual(methods["cash"]["amount"], "100.00")
        self.assertEqual(methods["cash"]["count"], 1)

        self.assertIn("mbank", methods)
        self.assertEqual(methods["mbank"]["amount"], "200.00")
        self.assertEqual(methods["mbank"]["count"], 1)

        self.assertIn("split", methods)
        self.assertEqual(methods["split"]["amount"], "300.00")
        self.assertEqual(methods["split"]["count"], 1)

        self.assertIn("debt", methods)
        self.assertEqual(methods["debt"]["amount"], "400.00")
        self.assertEqual(methods["debt"]["count"], 1)

    def test_routing_by_source_kind(self):
        # POS sale should route to pos_branch or pos_main
        cb_sale = resolve_cashbox(
            company=self.company,
            context={"branch_id": str(self.branch.id)},
            source_kind="pos_sale",
        )
        self.assertEqual(cb_sale, self.pos_cashbox)

        # Warehouse purchase should route to expense_variable
        cb_wh = resolve_cashbox(
            company=self.company,
            source_kind="warehouse_purchase",
        )
        self.assertEqual(cb_wh, self.expense_cashbox)

        # Supplier debt payment should route to expense_variable
        cb_sup = resolve_cashbox(
            company=self.company,
            source_kind="supplier_debt_payment",
        )
        self.assertEqual(cb_sup, self.expense_cashbox)

    def test_supplier_debt_payment_reject_cascade(self):
        client = Client.objects.create(
            full_name="Supplier LLC",
            company=self.company,
            type=Client.StatusClient.SUPPLIERS,
        )
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=client,
            title="Procurement Deal",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal("2000.00"),
            debt_months=1,
        )
        inst = deal.installments.first()
        if not inst:
            inst = DealInstallment.objects.create(
                company=self.company,
                branch=self.branch,
                deal=deal,
                number=1,
                amount=Decimal("2000.00"),
                paid_amount=Decimal("1000.00"),
                due_date=date.today(),
                balance_after=Decimal("1000.00"),
            )
        else:
            inst.paid_amount = Decimal("1000.00")
            inst.save()

        payment = DealPayment.objects.create(
            company=self.company,
            branch=self.branch,
            deal=deal,
            installment=inst,
            kind=DealPayment.Kind.PAY,
            amount=Decimal("1000.00"),
            paid_date=date.today(),
            idempotency_key=uuid.uuid4(),
            created_by=self.user,
        )

        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.expense_cashbox,
            user=self.user,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("1000.00"),
            source_kind=CashFlow.SourceKind.SUPPLIER_DEBT_PAYMENT,
            source_id=str(deal.id),
            affects_shift_drawer=False,
        )
        self.assertIsNotNone(cf)

        # Reject cascade
        cf.status = CashFlow.Status.REJECTED
        cf.save()
        handle_cashflow_reject(cf, user=self.user)

        inst.refresh_from_db()
        self.assertEqual(inst.paid_amount, Decimal("0.00"))
        self.assertFalse(DealPayment.objects.filter(id=payment.id).exists())
