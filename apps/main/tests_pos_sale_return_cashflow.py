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
        # Движение ящик не уменьшает: возвращённый чек ушёл из paid, его наличные
        # уже не попадают в drawer_expected_cash. Флаг True снимал бы сумму дважды
        # (было −500 вместо 500 — этот тест и падал).
        self.assertFalse(return_flow.affects_shift_drawer)
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
        # Частичный возврат уже уменьшил строки оплат чека до остатка
        # (_rescale_sale_payments_after_partial_return) — вычитать ещё и это
        # движение значило бы снять 500 дважды.
        self.assertFalse(return_flow.affects_shift_drawer)

    def test_partial_cash_return_keeps_remainder_in_drawer(self):
        """Заплатили 1000 наличными, вернули 500 → в ящике должно остаться +500."""
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        item = sale.items.first()

        before = self.shift.calc_live_totals()["drawer_expected_cash"]
        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"items": [{"sale_item_id": str(item.id), "quantity": 1}],
             "idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        self.assertEqual(SaleReturnAPIView.as_view()(req, pk=sale.id).status_code, 200)

        after = self.shift.calc_live_totals()["drawer_expected_cash"]
        self.assertEqual(before - after, Decimal("500.00"))

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
        # Раньше движения различали по affects_shift_drawer; теперь его нет ни у
        # одного возврата, поэтому разделяем по сумме.
        transfer_flow, cash_flow = flows[0], flows[1]

        self.assertEqual(transfer_flow.amount, Decimal("400.00"))
        self.assertEqual(cash_flow.amount, Decimal("600.00"))
        self.assertFalse(transfer_flow.affects_shift_drawer)
        self.assertFalse(cash_flow.affects_shift_drawer)

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
        self.assertFalse(return_flow.affects_shift_drawer)

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


class SaleReturnRealtimeBroadcastTests(POSSaleReturnCashflowTests):
    """
    R9: возврат чека должен слать в WS `market.cashflow.created` (лента кассы)
    и `market.shift.updated` (экран смены кассира).
    """

    def _capture_group_sends(self):
        """Подменяет channel layer и собирает отправленные события."""
        from unittest.mock import patch, MagicMock

        sent = []

        layer = MagicMock()

        # group_send должен быть корутиной: код шлёт через async_to_sync,
        # обычная функция роняет отправку после первого события.
        async def _group_send(group, message):
            sent.append((group, message))

        layer.group_send = _group_send
        cm = patch("channels.layers.get_channel_layer", return_value=layer)
        return cm, sent

    def _return_sale(self, sale):
        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        return SaleReturnAPIView.as_view()(req, pk=sale.id)

    def test_return_broadcasts_cashflow_and_shift_events(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")

        cm, sent = self._capture_group_sends()
        with cm:
            self.assertEqual(self._return_sale(sale).status_code, 200)

        events = [m.get("event") for _, m in sent]
        self.assertIn("market.cashflow.created", events)
        # Возврат меняет drawer_expected_cash даже без affects_shift_drawer —
        # экран смены должен обновиться.
        self.assertIn("market.shift.updated", events)

        groups = {g for g, _ in sent}
        self.assertEqual(groups, {f"notif_company_{self.company.id}"})
        for _, m in sent:
            self.assertEqual(m["type"], "market.notification")

    def test_noncash_return_also_updates_shift_screen(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="transfer")

        cm, sent = self._capture_group_sends()
        with cm:
            self.assertEqual(self._return_sale(sale).status_code, 200)

        events = [m.get("event") for _, m in sent]
        self.assertIn("market.cashflow.created", events)
        self.assertIn("market.shift.updated", events)


class SaleItemReturnableQtyTests(POSSaleReturnCashflowTests):
    """§16: фронту нужен остаток к возврату по строке, чтобы ограничить «Макс. возврат»."""

    def test_returnable_qty_reflects_remaining_quantity(self):
        from apps.main.pos_serializers import SaleItemReadSerializer

        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        item = sale.items.first()
        self.assertEqual(SaleItemReadSerializer(item).data["returnable_qty"], Decimal("2.000"))

        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"items": [{"sale_item_id": str(item.id), "quantity": 1}],
             "idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        self.assertEqual(SaleReturnAPIView.as_view()(req, pk=sale.id).status_code, 200)

        item.refresh_from_db()
        # Частичный возврат уменьшил строку — вернуть можно только остаток.
        self.assertEqual(SaleItemReadSerializer(item).data["returnable_qty"], Decimal("1.000"))

    def test_partial_return_status_is_partially_returned(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        item = sale.items.first()
        req = self.factory.post(
            f"/main/pos/sales/{sale.id}/return/",
            {"items": [{"sale_item_id": str(item.id), "quantity": 1}],
             "idempotency_key": str(uuid.uuid4())},
            format="json",
        )
        force_authenticate(req, user=self.cashier)
        SaleReturnAPIView.as_view()(req, pk=sale.id)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "partially_returned")


class RefundMethodOverrideTests(POSSaleReturnCashflowTests):
    """
    §9.1: способ, которым деньги фактически отдали, может отличаться от способа
    исходной оплаты. Без этого ящик смены расходится в обе стороны.
    """

    def _return(self, sale, **payload):
        body = {"idempotency_key": str(uuid.uuid4())}
        body.update(payload)
        req = self.factory.post(f"/main/pos/sales/{sale.id}/return/", body, format="json")
        force_authenticate(req, user=self.cashier)
        return SaleReturnAPIView.as_view()(req, pk=sale.id)

    def _drawer(self):
        return CashShift.objects.get(pk=self.shift.pk).calc_live_totals()["drawer_expected_cash"]

    # ── инцидент: чек безналом, деньги отдали наличными ──
    def test_noncash_sale_refunded_in_cash_reduces_drawer(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("970.00"), payment_method="transfer")
        before = self._drawer()

        self.assertEqual(self._return(sale, refund_method="cash").status_code, 200)

        # Наличные ушли из ящика, хотя по чеку их там не было.
        self.assertEqual(before - self._drawer(), Decimal("1940.00"))
        flow = CashFlow.objects.get(
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN, source_id=str(sale.id),
        )
        self.assertTrue(flow.affects_shift_drawer)

    def test_noncash_sale_refunded_as_usual_leaves_drawer_intact(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("970.00"), payment_method="transfer")
        before = self._drawer()

        self.assertEqual(self._return(sale).status_code, 200)

        self.assertEqual(self._drawer(), before)
        flow = CashFlow.objects.get(
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN, source_id=str(sale.id),
        )
        self.assertFalse(flow.affects_shift_drawer)

    # ── обратное направление: чек наличными, вернули переводом ──
    def test_cash_sale_refunded_by_transfer_keeps_cash_in_drawer(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        before = self._drawer()          # 500 размен + 1000 наличных

        self.assertEqual(self._return(sale, refund_method="transfer").status_code, 200)

        # Чек ушёл из paid и унёс свои наличные из расчёта, но физически они
        # остались — компенсирующий приход возвращает их обратно.
        self.assertEqual(self._drawer(), before)
        adj = CashFlow.objects.filter(
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
            source_id=str(sale.id), type=CashFlow.Type.INCOME,
        ).first()
        self.assertIsNotNone(adj)
        self.assertTrue(adj.affects_shift_drawer)
        self.assertEqual(adj.amount, Decimal("1000.00"))

    def test_cash_sale_refunded_in_cash_is_unchanged(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="cash")
        before = self._drawer()

        self.assertEqual(self._return(sale, refund_method="cash").status_code, 200)

        # Деньги и так наличные — поведение прежнее, ящик падает на сумму чека.
        self.assertEqual(before - self._drawer(), Decimal("1000.00"))
        self.assertFalse(
            CashFlow.objects.filter(
                source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
                source_id=str(sale.id), type=CashFlow.Type.INCOME,
            ).exists()
        )

    # ── частичный возврат: сумма только по возвращённым позициям ──
    def test_partial_noncash_refund_in_cash_uses_returned_amount_only(self):
        sale = self._create_paid_sale(quantity=2, price=Decimal("500.00"), payment_method="transfer")
        item = sale.items.first()
        before = self._drawer()

        resp = self._return(
            sale, items=[{"sale_item_id": str(item.id), "quantity": 1}], refund_method="cash",
        )
        self.assertEqual(resp.status_code, 200)

        # Из ящика ушла стоимость одной позиции, а не всего чека.
        self.assertEqual(before - self._drawer(), Decimal("500.00"))

    # ── "original" эквивалентен отсутствию поля ──
    def test_refund_method_original_matches_default(self):
        sale = self._create_paid_sale(quantity=1, price=Decimal("300.00"), payment_method="transfer")
        before = self._drawer()
        self.assertEqual(self._return(sale, refund_method="original").status_code, 200)
        self.assertEqual(self._drawer(), before)

    # ── без открытой смены ящика нет, корректировок быть не должно ──
    def test_no_shift_means_no_drawer_adjustment(self):
        self.shift.status = CashShift.Status.CLOSED
        self.shift.save(update_fields=["status"])
        sale = self._create_paid_sale(quantity=1, price=Decimal("300.00"), payment_method="transfer")

        self.assertEqual(self._return(sale, refund_method="cash").status_code, 200)
        for f in CashFlow.objects.filter(
            source_kind=CashFlow.SourceKind.POS_SALE_RETURN, source_id=str(sale.id)
        ):
            self.assertFalse(f.affects_shift_drawer)
