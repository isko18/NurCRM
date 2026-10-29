from datetime import date
from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.construction.auto_cashflow import (
    resolve_auto_cashflow_status,
    resolve_auto_cashbox,
    create_auto_cashflow,
    serialize_auto_cashflows,
    handle_cashflow_reject,
)
from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.main.models import Branch, Client, ClientDeal, DealInstallment, DealPayment, Product, Sale
from apps.users.models import Company, SubscriptionPlan

User = get_user_model()


class AutoCashflowUnitTests(TestCase):
    def setUp(self):
        email = f"cashier_{uuid.uuid4().hex[:8]}@test.com"
        self.user = User.objects.create_user(email=email, password="pass")
        self.company = Company.objects.create(name=f"Test Company {uuid.uuid4().hex[:6]}", owner=self.user)
        self.user.company = self.company
        self.user.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashbox = Cashbox.objects.create(name="Cashbox 1", company=self.company, branch=self.branch)

    def tearDown(self):
        try:
            self.company.delete()
            self.user.delete()
        except Exception:
            pass

    def test_status_resolution_start_plan(self):
        plan_start = SubscriptionPlan.objects.create(name="Старт", price=Decimal("0.00"))
        self.company.subscription_plan = plan_start
        self.company.save()
        self.assertEqual(resolve_auto_cashflow_status(self.company), CashFlow.Status.APPROVED)

    def test_status_resolution_other_plan(self):
        plan_pro = SubscriptionPlan.objects.create(name="Бизнес", price=Decimal("500.00"))
        self.company.subscription_plan = plan_pro
        self.company.save()
        self.assertEqual(resolve_auto_cashflow_status(self.company), CashFlow.Status.PENDING)

    def test_status_resolution_no_plan(self):
        self.company.subscription_plan = None
        self.company.save()
        self.assertEqual(resolve_auto_cashflow_status(self.company), CashFlow.Status.PENDING)

    def test_cashbox_resolution_explicit(self):
        res = resolve_auto_cashbox(self.company, cashbox_id=self.cashbox.id)
        self.assertEqual(res, self.cashbox)

    def test_cashbox_resolution_open_shift(self):
        shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            cashier=self.user,
            status=CashShift.Status.OPEN,
        )
        res = resolve_auto_cashbox(self.company, user=self.user)
        self.assertEqual(res, self.cashbox)

    def test_cashbox_resolution_missing_fails(self):
        with self.assertRaises(DRFValidationError) as ctx:
            resolve_auto_cashbox(self.company, user=self.user, require_cashbox=True)
        self.assertIn("cashbox_id", ctx.exception.detail)

    def test_create_auto_cashflow_and_idempotency(self):
        cf1 = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("150.00"),
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id="sale-123",
            name="Продажа (наличные)",
        )
        self.assertIsNotNone(cf1)
        self.assertEqual(cf1.amount, Decimal("150.00"))
        self.assertEqual(cf1.source_kind, "pos_sale")
        self.assertEqual(cf1.source_id, "sale-123")
        self.assertEqual(cf1.status, CashFlow.Status.PENDING)

        # Repeated call must return existing and not create duplicate
        cf2 = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("150.00"),
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id="sale-123",
            name="Продажа (наличные)",
        )
        self.assertEqual(cf1.id, cf2.id)
        self.assertEqual(CashFlow.objects.filter(company=self.company, source_id="sale-123").count(), 1)

    def test_serialize_auto_cashflows(self):
        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("200.50"),
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id="sale-456",
            name="Продажа",
        )
        serialized = serialize_auto_cashflows([cf])
        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["amount"], "200.50")
        self.assertEqual(serialized[0]["type"], "income")
        self.assertEqual(serialized[0]["source_kind"], "pos_sale")
        self.assertEqual(serialized[0]["source_id"], "sale-456")
        self.assertEqual(serialized[0]["cashbox"], str(self.cashbox.id))

    def test_reject_cascade_pos_sale(self):
        sale = Sale.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            total=Decimal("300.00"),
            payment_method=Sale.PaymentMethod.CASH,
            status=Sale.Status.PAID,
        )
        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("300.00"),
            source_kind=CashFlow.SourceKind.POS_SALE,
            source_id=str(sale.id),
        )
        self.assertTrue(Sale.objects.filter(id=sale.id).exists())

        # Perform reject cascade
        cf.status = CashFlow.Status.REJECTED
        cf.save()
        handle_cashflow_reject(cf, user=self.user)

        self.assertFalse(Sale.objects.filter(id=sale.id).exists())

    def test_reject_cascade_debt_deal(self):
        client = Client.objects.create(full_name="Test Client", company=self.company)
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=client,
            title="Deal 1",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal("1000.00"),
        )
        inst = deal.installments.first()
        if not inst:
            inst = DealInstallment.objects.create(
                company=self.company,
                branch=self.branch,
                deal=deal,
                number=1,
                amount=Decimal("500.00"),
                paid_amount=Decimal("500.00"),
                due_date=date.today(),
                balance_after=Decimal("500.00"),
            )
        else:
            inst.paid_amount = Decimal("500.00")
            inst.save()

        payment = DealPayment.objects.create(
            company=self.company,
            branch=self.branch,
            deal=deal,
            installment=inst,
            kind=DealPayment.Kind.PAY,
            amount=Decimal("500.00"),
            paid_date=date.today(),
            idempotency_key=uuid.uuid4(),
            created_by=self.user,
        )

        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.INCOME,
            amount=Decimal("500.00"),
            source_kind=CashFlow.SourceKind.DEBT_REPAYMENT,
            source_id=str(deal.id),
        )

        cf.status = CashFlow.Status.REJECTED
        cf.save()
        handle_cashflow_reject(cf, user=self.user)

        inst.refresh_from_db()
        self.assertEqual(inst.paid_amount, Decimal("0.00"))
        self.assertFalse(DealPayment.objects.filter(id=payment.id).exists())

    def test_reject_cascade_warehouse_purchase(self):
        prod = Product.objects.create(
            company=self.company,
            branch=self.branch,
            name="Purchased Item",
            purchase_price=Decimal("50.00"),
            quantity=Decimal("10.00"),
            price=Decimal("100.00"),
        )
        cf = create_auto_cashflow(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            user=self.user,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("500.00"),
            source_kind=CashFlow.SourceKind.WAREHOUSE_PURCHASE,
            source_id=str(prod.id),
        )
        self.assertTrue(Product.objects.filter(id=prod.id).exists())

        cf.status = CashFlow.Status.REJECTED
        cf.save()
        handle_cashflow_reject(cf, user=self.user)

        self.assertFalse(Product.objects.filter(id=prod.id).exists())
