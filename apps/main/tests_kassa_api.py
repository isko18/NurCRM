"""Доработки для кассы NurMarket (BACKEND_API.md)."""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.integrations.models import ApiKey, WebhookEndpoint
from apps.main.models import (
    Cart,
    CartItem,
    Client,
    ClientBonusTransaction,
    ClientDeal,
    Product,
    Sale,
    SaleDocCounter,
    SalePayment,
    SaleReturn,
)
from apps.users.models import Company, CompanyAddon, Feature, SubscriptionPlan, User


def _ok_delivery():
    resp = mock.MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: False
    return resp


class KassaBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.plan = SubscriptionPlan.objects.create(name="Стандарт", price=Decimal("1.00"))
        self.company = Company.objects.create(
            name="Kassa Co", owner=self.owner, is_active=True, subscription_plan=self.plan
        )
        self.owner.company = self.company
        self.owner.save()
        self.cashier = User.objects.create_user(
            email=f"c{uuid.uuid4().hex[:6]}@t.kg", password="x", company=self.company, role="salesperson"
        )
        self.cashbox = Cashbox.objects.create(
            name="Касса", company=self.company, role=Cashbox.CashboxRole.POS_MAIN
        )
        self.shift = CashShift.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            cashier=self.owner,
            status=CashShift.Status.OPEN,
            opening_cash=Decimal("1000.00"),
        )
        self.product = Product.objects.create(
            company=self.company, name="Хлеб", price=Decimal("100.00"), quantity=Decimal("50")
        )
        self.client_obj = Client.objects.create(company=self.company, full_name="Айбек", phone="+996555000222")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def quick(self, key=None, **body):
        payload = {
            "items": [{"product": str(self.product.id), "qty": "2", "price": "100.00"}],
            "payment": {"method": "cash", "received": "500.00"},
        }
        payload.update(body)
        return self.api.post(
            "/api/main/pos/checkout/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4())
        )


class QuickCheckoutTests(KassaBase):
    def test_creates_paid_sale_with_number_and_is_idempotent(self):
        key = str(uuid.uuid4())
        r1 = self.quick(key=key)
        self.assertEqual(r1.status_code, 201, r1.data)
        self.assertEqual(r1.data["status"], "paid")
        self.assertEqual(r1.data["total"], "200.00")
        self.assertEqual(r1.data["change"], "300.00")
        self.assertEqual(r1.data["number"], 1)

        r2 = self.quick(key=key)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.data["replayed"])
        self.assertEqual(r2.data["id"], r1.data["id"])
        self.assertEqual(Sale.objects.filter(company=self.company).count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("48"))

    def test_numbers_are_sequential_and_continue_after_existing(self):
        Sale.objects.create(company=self.company, user=self.owner, cashbox=self.cashbox, status=Sale.Status.PAID, doc_number=41)
        n1 = self.quick().data["number"]
        n2 = self.quick().data["number"]
        self.assertEqual((n1, n2), (42, 43))
        self.assertEqual(SaleDocCounter.objects.get(company=self.company).last_number, 43)
        r = self.api.get("/api/main/pos/sales/?number=43")
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual([row["number"] for row in rows], [43])

    def test_requires_idempotency_key(self):
        r = self.api.post(
            "/api/main/pos/checkout/",
            {"items": [{"product": str(self.product.id), "qty": "1"}], "payment": {"method": "cash", "received": "100"}},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_custom_item_and_mixed_split(self):
        r = self.quick(
            items=[
                {"product": str(self.product.id), "qty": "3"},
                {"custom": True, "name": "Доставка", "price": "100.00", "qty": "1"},
            ],
            payment={"method": "mixed", "cash_amount": "300.00", "card_amount": "100.00"},
        )
        self.assertEqual(r.status_code, 201, r.data)
        sale = Sale.objects.get(pk=r.data["id"])
        self.assertEqual(sale.payment_method, Sale.PaymentMethod.MIXED)
        lines = {p.method: p.amount for p in SalePayment.objects.filter(sale=sale)}
        self.assertEqual(lines, {"cash": Decimal("300.00"), "transfer": Decimal("100.00")})
        detail = self.api.get(f"/api/main/pos/sales/{sale.id}/").data
        self.assertEqual((detail["cash_amount"], detail["card_amount"]), ("300.00", "100.00"))
        self.assertEqual(self.shift.calc_live_totals(refresh=True)["expected_cash"], Decimal("1300.00"))

    def test_cashier_discount_over_limit_is_400(self):
        self.company.max_discount_percent = Decimal("10")
        self.company.save()
        shift = CashShift.objects.create(
            company=self.company, cashbox=self.cashbox, cashier=self.cashier, status=CashShift.Status.OPEN
        )
        api = APIClient()
        api.force_authenticate(self.cashier)
        r = api.post(
            "/api/main/pos/checkout/",
            {
                "shift": str(shift.id),
                "items": [{"product": str(self.product.id), "qty": "1"}],
                "order_discount_total": "50.00",
                "payment": {"method": "cash", "received": "100"},
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="k1",
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "discount_limit")
        self.assertIn("нужно разрешение владельца", r.data["detail"])
        self.assertFalse(Sale.objects.exists())

    def test_bonus_redeemed_separate_from_discount(self):
        self.client_obj.bonus_balance = Decimal("150.00")
        self.client_obj.save()
        r = self.quick(client=str(self.client_obj.id), bonus_redeemed="50.00",
                       payment={"method": "cash", "received": "150.00"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["total"], "150.00")
        self.assertEqual(r.data["discount_total"], "0.00")
        self.assertEqual(r.data["bonus_redeemed"], "50.00")
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.bonus_balance, Decimal("100.00"))
        self.assertEqual(ClientBonusTransaction.objects.get().reason, "redeem")


class CartDiscountLimitTests(KassaBase):
    def test_order_discount_amount_over_limit_is_400_not_500(self):
        self.company.max_discount_percent = Decimal("10")
        self.company.save()
        cart = Cart.objects.create(company=self.company, user=self.cashier, shift=self.shift, status=Cart.Status.ACTIVE)
        CartItem(company=self.company, cart=cart, product=self.product, quantity=Decimal("2"),
                 unit_price=Decimal("100.00")).save(skip_full_clean=True)
        api = APIClient()
        api.force_authenticate(self.cashier)
        r = api.patch(f"/api/main/pos/carts/{cart.id}/", {"order_discount_total": "30.00"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "Скидка больше разрешённой (10%), нужно разрешение владельца")
        self.assertEqual(r.data["code"], "discount_limit")
        r = api.patch(f"/api/main/pos/carts/{cart.id}/", {"order_discount_total": "20.00"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def _cashier_cart(self, limit="10"):
        self.company.max_discount_percent = Decimal(limit)
        self.company.save()
        cart = Cart.objects.create(company=self.company, user=self.cashier, shift=self.shift, status=Cart.Status.ACTIVE)
        api = APIClient()
        api.force_authenticate(self.cashier)
        return cart, api

    def assertDiscountLimit(self, r):
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["code"], "discount_limit")
        self.assertEqual(Decimal(r.data["max_discount_percent"]), Decimal("10"))

    def test_order_discount_percent_over_limit(self):
        cart, api = self._cashier_cart()
        r = api.patch(f"/api/main/pos/carts/{cart.id}/", {"order_discount_percent": "50"}, format="json")
        self.assertDiscountLimit(r)

    def test_add_item_with_discount_over_limit_is_rejected(self):
        cart, api = self._cashier_cart()
        url = f"/api/main/pos/sales/{cart.id}/add-item/"
        r = api.post(url, {"product_id": str(self.product.id), "quantity": "2", "discount_total": "50.00"}, format="json")
        self.assertDiscountLimit(r)
        self.assertFalse(CartItem.objects.filter(cart=cart).exists())
        r = api.post(url, {"product_id": str(self.product.id), "quantity": "1", "discount_percent": "50"}, format="json")
        self.assertDiscountLimit(r)
        r = api.post(url, {"product_id": str(self.product.id), "quantity": "2", "discount_total": "20.00"}, format="json")
        self.assertIn(r.status_code, (200, 201), r.data)

    def test_item_patch_discount_over_limit(self):
        cart, api = self._cashier_cart()
        item = CartItem(company=self.company, cart=cart, product=self.product, quantity=Decimal("1"),
                        unit_price=Decimal("100.00"))
        item.save(skip_full_clean=True)
        url = f"/api/main/pos/carts/{cart.id}/items/{item.id}/"
        self.assertDiscountLimit(api.patch(url, {"discount_total": "50.00"}, format="json"))
        self.assertDiscountLimit(api.patch(url, {"discount_percent": "50"}, format="json"))
        item.refresh_from_db()
        self.assertEqual(item.line_discount, Decimal("0.00"))
        r = api.patch(url, {"discount_total": "10.00"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def test_checkout_line_discount_over_limit(self):
        cart, api = self._cashier_cart()
        shift = CashShift.objects.create(
            company=self.company, cashbox=self.cashbox, cashier=self.cashier, status=CashShift.Status.OPEN
        )
        r = api.post(
            "/api/main/pos/checkout/",
            {
                "shift": str(shift.id),
                "items": [{"product": str(self.product.id), "qty": "1", "discount": "50.00"}],
                "payment": {"method": "cash", "received": "100"},
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-line",
        )
        self.assertDiscountLimit(r)
        self.assertFalse(Sale.objects.exists())

    def test_owner_is_not_limited(self):
        cart, _ = self._cashier_cart()
        cart.user = self.owner
        cart.save()
        api = APIClient()
        api.force_authenticate(self.owner)
        r = api.post(f"/api/main/pos/sales/{cart.id}/add-item/",
                     {"product_id": str(self.product.id), "quantity": "1", "discount_total": "50.00"}, format="json")
        self.assertIn(r.status_code, (200, 201), r.data)


class CheckoutMixedSplitTests(KassaBase):
    def test_classic_checkout_with_cash_and_card_amount(self):
        cart = Cart.objects.create(company=self.company, user=self.owner, shift=self.shift, status=Cart.Status.ACTIVE)
        CartItem(company=self.company, cart=cart, product=self.product, quantity=Decimal("5"),
                 unit_price=Decimal("100.00")).save(skip_full_clean=True)
        r = self.api.post(
            f"/api/main/pos/sales/{cart.id}/checkout/",
            {"payment_method": "mixed", "cash_amount": "300.00", "card_amount": "200.00", "card_method": "mbank"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        sale = Sale.objects.get(pk=r.data["sale_id"])
        self.assertIsNotNone(sale.doc_number)
        self.assertEqual(
            {p.method: p.amount for p in sale.payments.all()},
            {"cash": Decimal("300.00"), "mbank": Decimal("200.00")},
        )


class DrawerInflowTests(KassaBase):
    def test_inflow_and_outflow_move_expected_cash_905_915_905(self):
        """BE2-05: внесение 10 → expected_cash 905 → 915, изъятие 10 → снова 905."""
        self.shift.opening_cash = Decimal("905.00")
        self.shift.save()

        def expected():
            r = self.api.get(f"/api/construction/shifts/{self.shift.id}/")
            self.assertEqual(r.status_code, 200, r.data)
            return Decimal(str(r.data["expected_cash"]))

        self.assertEqual(expected(), Decimal("905.00"))
        for kind, typ, want in (("shift_drawer_inflow", "income", "915.00"), ("shift_drawer_outflow", "expense", "905.00")):
            r = self.api.post("/api/construction/cashflows/", {
                "cashbox": str(self.cashbox.id), "shift": str(self.shift.id), "type": typ, "name": kind,
                "amount": "10.00", "source_kind": kind, "source_id": kind, "payment_method": "cash",
            }, format="json")
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(expected(), Decimal(want))

    def test_inflow_counts_in_expected_cash_and_is_deduplicated(self):
        body = {
            "cashbox": str(self.cashbox.id), "shift": str(self.shift.id), "type": "income", "name": "Внесение",
            "amount": "500.00", "source_kind": "shift_drawer_inflow", "source_id": "op-1", "payment_method": "cash",
        }
        r1 = self.api.post("/api/construction/cashflows/", body, format="json")
        self.assertEqual(r1.status_code, 201, r1.data)
        r2 = self.api.post("/api/construction/cashflows/", body, format="json")
        self.assertEqual(r2.data["id"], r1.data["id"])
        totals = self.shift.calc_live_totals(refresh=True)
        self.assertEqual(totals["expected_cash"], Decimal("1500.00"))
        self.assertEqual(totals["income_total"], Decimal("500.00"))

        r = self.api.get("/api/construction/cashflows/?source_id=op-1&source_kind=shift_drawer_inflow")
        self.assertEqual(r.data["count"], 1)
        r = self.api.get("/api/construction/cashflows/?source_id=nope")
        self.assertEqual(r.data["count"], 0)

    def test_inflow_must_be_income(self):
        r = self.api.post("/api/construction/cashflows/", {
            "cashbox": str(self.cashbox.id), "type": "expense", "name": "x", "amount": "5",
            "source_kind": "shift_drawer_inflow",
        }, format="json")
        self.assertEqual(r.status_code, 400)


class ShiftReportTests(KassaBase):
    def test_report_has_deposits_withdrawals_returns(self):
        self.quick(payment={"method": "mixed", "cash_amount": "150.00", "card_amount": "50.00"})
        sale_id = self.quick().data["id"]
        for kind, typ, amt in (("shift_drawer_inflow", "income", "500"), ("shift_drawer_outflow", "expense", "200")):
            self.api.post("/api/construction/cashflows/", {
                "cashbox": str(self.cashbox.id), "shift": str(self.shift.id), "type": typ, "name": kind,
                "amount": amt, "source_kind": kind,
            }, format="json")
        r = self.api.post(f"/api/main/pos/sales/{sale_id}/return/", {"reason": "брак"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

        rep = self.api.get(f"/api/construction/shifts/{self.shift.id}/report/").data
        self.assertEqual(rep["deposits"], "500.00")
        self.assertEqual(rep["withdrawals"], "200.00")
        self.assertEqual(rep["by_payment"]["mixed_cash"], "150.00")
        self.assertEqual(rep["by_payment"]["mixed_card"], "50.00")
        self.assertEqual(rep["returns_count"], 1)
        self.assertEqual(rep["returns_total"], "200.00")

        returns = self.api.get("/api/main/pos/returns/").data
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]["reason"], "брак")
        self.assertEqual(returns[0]["items"][0]["qty"], "2.000")
        self.assertEqual(returns[0]["sale_number"], Sale.objects.get(pk=sale_id).doc_number)


class DebtTests(KassaBase):
    def _debt_sale(self, total, prepay="0"):
        r = self.quick(
            client=str(self.client_obj.id),
            items=[{"product": str(self.product.id), "qty": str(Decimal(total) / 100)}],
            payment={"method": "debt", "received": prepay},
        )
        self.assertEqual(r.status_code, 201, r.data)
        return Sale.objects.get(pk=r.data["id"])

    def test_debtors_and_pay_debt_oldest_first(self):
        s1 = self._debt_sale("300", prepay="100")
        s2 = self._debt_sale("500")
        other = Client.objects.create(company=self.company, full_name="Без долга", phone="1")

        r = self.api.get("/api/main/clients/debtors/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)
        row = r.data[0]
        self.assertEqual((row["client"], row["debt_total"], row["sales_count"]),
                         (str(self.client_obj.id), "700.00", 2))
        self.assertNotIn(str(other.id), [x["client"] for x in r.data])

        if not ClientDeal.objects.filter(sale=s1).first().installments.exists():
            self.skipTest("долговые сделки без графика взносов — pay-any не применим")
        r = self.api.post(f"/api/main/clients/{self.client_obj.id}/pay-debt/",
                          {"amount": "250.00", "method": "cash", "shift": str(self.shift.id)},
                          format="json", HTTP_IDEMPOTENCY_KEY="pay-1")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["paid"], "250.00")
        self.assertEqual(r.data["left"], "450.00")
        self.assertEqual(r.data["applied"][0]["sale"], str(s1.id))
        r2 = self.api.post(f"/api/main/clients/{self.client_obj.id}/pay-debt/",
                           {"amount": "250.00", "method": "cash"}, format="json", HTTP_IDEMPOTENCY_KEY="pay-1")
        self.assertEqual(r2.data["left"], "450.00")


class ProductFilterTests(KassaBase):
    def test_quantity_lte_and_is_service(self):
        Product.objects.create(company=self.company, name="Мало", price=1, quantity=Decimal("3"))
        Product.objects.create(company=self.company, name="Стрижка", price=1, quantity=Decimal("0"), kind="service")
        r = self.api.get("/api/main/products/list/?quantity_lte=5&is_service=false")
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual([p["name"] for p in rows], ["Мало"])


class ClientFieldsTests(KassaBase):
    def test_telegram_chat_id_and_bonus_endpoints(self):
        r = self.api.patch(f"/api/main/clients/{self.client_obj.id}/", {"telegram_chat_id": "123456789"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.telegram_chat_id, "123456789")

        r = self.api.post(f"/api/main/clients/{self.client_obj.id}/bonus/", {"delta": "100", "reason": "earn"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.api.post(f"/api/main/clients/{self.client_obj.id}/bonus/", {"delta": "-150", "reason": "redeem"}, format="json")
        self.assertEqual(r.status_code, 400)
        hist = self.api.get(f"/api/main/clients/{self.client_obj.id}/bonus/history/").data
        self.assertEqual(hist["bonus_balance"], "100.00")
        self.assertEqual(len(hist["results"]), 1)


class CompanyTests(KassaBase):
    def test_market_sphere_features_addons(self):
        self.plan.features.add(Feature.objects.create(name="debts"))
        CompanyAddon.objects.create(company=self.company, code="loyalty")
        r = self.api.patch("/api/users/company/", {"market_sphere": "clothing"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["market_sphere"], "clothing")
        self.assertEqual(r.data["features"], ["debts", "loyalty"])
        addons = self.api.get("/api/users/company/addons/").data
        self.assertEqual(addons, [{"code": "loyalty", "active": True, "until": None}])

        api = APIClient()
        api.force_authenticate(self.cashier)
        self.assertEqual(api.patch("/api/users/company/", {"market_sphere": "grocery"}, format="json").status_code, 403)

    def test_market_spheres_list_and_features_detail(self):
        """BE2-02: несколько видов магазина и функции со сроком."""
        import datetime

        self.plan.features.add(Feature.objects.create(name="variants"))
        CompanyAddon.objects.create(company=self.company, code="certificates", until=datetime.date(2027, 1, 1))
        r = self.api.patch("/api/users/company/", {"market_spheres": ["clothing", "services"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["market_sphere"], r.data["market_spheres"]), ("clothing", ["clothing", "services"]))
        detail = {f["code"]: f["until"] for f in r.data["features_detail"]}
        self.assertEqual(detail["certificates"][:10], "2027-01-01")
        self.assertIn("variants", detail)

        r = self.api.patch("/api/users/company/", {"market_sphere": "services"}, format="json")
        self.assertEqual(r.data["market_spheres"], ["services"])
        r = self.api.patch("/api/users/company/", {"market_spheres": ["cafe"]}, format="json")
        self.assertEqual(r.status_code, 400)


class ApiKeyTests(KassaBase):
    def test_read_only_key(self):
        r = self.api.post("/api/users/api-keys/", {"name": "Telegram-бот", "scopes": ["read"]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        raw = r.data["key"]
        self.assertTrue(raw.startswith("nk_live_"))
        self.assertNotIn("key", self.api.get("/api/users/api-keys/").data[0])

        bot = APIClient()
        bot.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}")
        self.assertEqual(bot.get("/api/main/pos/sales/").status_code, 200)
        self.assertEqual(bot.post("/api/main/clients/", {"full_name": "x", "phone": "1"}, format="json").status_code, 403)
        self.assertEqual(bot.get("/api/users/api-keys/").status_code, 403)

        self.assertEqual(self.api.delete(f"/api/users/api-keys/{r.data['id']}/").status_code, 204)
        self.assertEqual(bot.get("/api/main/pos/sales/").status_code, 401)

    def test_cashier_cannot_manage_keys(self):
        api = APIClient()
        api.force_authenticate(self.cashier)
        self.assertEqual(api.post("/api/users/api-keys/", {"name": "x"}, format="json").status_code, 403)


class WebhookTests(KassaBase):
    @mock.patch("apps.integrations.events.validate_public_url")
    def test_sale_paid_and_shift_closed_are_delivered_signed(self, _v):
        ep = WebhookEndpoint.objects.create(
            company=self.company, url="https://bot.example.kg/hook", events=["sale.paid", "shift.closed"],
            secret="s" * 32,
        )
        with mock.patch("apps.integrations.tasks._opener.open", return_value=_ok_delivery()) as op, \
                mock.patch("apps.integrations.events.validate_public_url"), \
                self.captureOnCommitCallbacks(execute=True):
            self.quick()
            self.api.post(f"/api/construction/shifts/{self.shift.id}/close/", {"closing_cash": "1200"}, format="json")
        events = [json.loads(c.args[0].data)["event"] for c in op.call_args_list]
        self.assertEqual(events, ["sale.paid", "shift.closed"])
        req = op.call_args_list[0].args[0]
        self.assertEqual(
            req.headers["X-signature"],
            hmac.new(ep.secret.encode(), req.data, hashlib.sha256).hexdigest(),
        )
        ep.refresh_from_db()
        self.assertEqual(ep.last_status, 200)

    def test_rejects_internal_url(self):
        r = self.api.post("/api/users/webhooks/", {"url": "http://127.0.0.1/x", "events": ["sale.paid"]}, format="json")
        self.assertEqual(r.status_code, 400)


class ReviewFixesTests(KassaBase):
    def test_shift_opened_webhook(self):
        WebhookEndpoint.objects.create(company=self.company, url="https://bot.example.kg/h", events=["shift.opened"],
                                       secret="s" * 32)
        with mock.patch("apps.integrations.tasks._opener.open", return_value=_ok_delivery()) as op, \
                mock.patch("apps.integrations.events.validate_public_url"), \
                self.captureOnCommitCallbacks(execute=True):
            CashShift.objects.create(company=self.company, cashbox=self.cashbox, cashier=self.cashier,
                                     status=CashShift.Status.OPEN)
        body = json.loads(op.call_args.args[0].data)
        self.assertEqual(body["event"], "shift.opened")
        self.assertEqual(body["data"]["cashier"], str(self.cashier.id))

    def test_debt_return_gets_items_and_shift(self):
        sale_id = self.quick(client=str(self.client_obj.id), payment={"method": "debt", "received": "0"}).data["id"]
        r = self.api.post(f"/api/main/pos/sales/{sale_id}/return/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        ret = self.api.get("/api/main/pos/returns/").data[0]
        self.assertEqual(ret["shift"], str(self.shift.id))
        self.assertEqual(ret["items"][0]["qty"], "2.000")

    def test_backfill_old_returns(self):
        from django.core.management import call_command

        sale_id = self.quick().data["id"]
        self.api.post(f"/api/main/pos/sales/{sale_id}/return/", {}, format="json")
        SaleReturn.objects.update(returned_items=None, shift=None)
        call_command("backfill_sale_returns", stdout=mock.MagicMock())
        r = SaleReturn.objects.get()
        self.assertEqual(r.shift_id, self.shift.id)
        self.assertEqual(r.returned_items[0]["qty"], "2.000")


class PromotionVsManualDiscountTests(KassaBase):
    """BE2-06/07: акция и ручная скидка кассира на одной строке."""

    def setUp(self):
        super().setUp()
        from apps.main.models import ProductPromotionTier

        self.promo = Product.objects.create(
            company=self.company, name="Адыгене 1л", price=Decimal("50.00"), quantity=Decimal("50"), stock=True
        )
        self.tier = ProductPromotionTier.objects.create(
            product=self.promo, min_amount=Decimal("32.00"), discount_percent=Decimal("15")
        )

    def _checkout(self, price, discount):
        return self.quick(items=[{"product": str(self.promo.id), "qty": "1", "price": price, "discount": discount}],
                          payment={"method": "cash", "received": "100.00"})

    def test_manual_discount_applies_when_promotion_does_not_match(self):
        r = self._checkout("20.00", "5.00")  # 20 < 32 — акция не подошла
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["total"], "15.00")
        line = r.data["items"][0]
        self.assertEqual((line["discount"], line["discount_source"], line["total"]), ("5.00", "manual", "15.00"))
        self.assertFalse(line["manual_discount_ignored"])

    def test_promotion_wins_and_ignored_manual_discount_is_reported(self):
        r = self._checkout("50.00", "5.00")  # 50 ≥ 32 — акция −15 %
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["total"], "42.50")
        line = r.data["items"][0]
        self.assertEqual((line["discount"], line["discount_source"]), ("7.50", "promotion"))
        self.assertEqual(line["promotion_id"], str(self.tier.id))
        self.assertTrue(line["manual_discount_ignored"])
        item = Sale.objects.get(id=r.data["id"]).items.get()
        self.assertEqual((item.discount_source, item.manual_discount), ("promotion", Decimal("5.00")))

    def test_manual_discount_survives_quantity_change_in_cart(self):
        cart = Cart.objects.create(company=self.company, user=self.owner, shift=self.shift, status=Cart.Status.ACTIVE)
        item = CartItem(company=self.company, cart=cart, product=self.promo, quantity=Decimal("1"),
                        unit_price=Decimal("20.00"), line_discount=Decimal("5.00"), manual_discount=Decimal("5.00"))
        item.save(skip_full_clean=True)
        cart.recalc()
        item.refresh_from_db()
        self.assertEqual((item.line_discount, item.discount_source), (Decimal("5.00"), "manual"))

        CartItem.objects.filter(pk=item.pk).update(quantity=Decimal("2"))  # 40 ≥ 32 — акция
        cart.recalc()
        item.refresh_from_db()
        self.assertEqual((item.line_discount, item.discount_source), (Decimal("6.00"), "promotion"))

        CartItem.objects.filter(pk=item.pk).update(quantity=Decimal("1"))  # снова ниже порога
        cart.recalc()
        item.refresh_from_db()
        self.assertEqual((item.line_discount, item.discount_source), (Decimal("5.00"), "manual"))

    def test_cart_response_shows_discount_source(self):
        cart = Cart.objects.create(company=self.company, user=self.owner, shift=self.shift, status=Cart.Status.ACTIVE)
        r = self.api.post(f"/api/main/pos/sales/{cart.id}/add-item/",
                          {"product_id": str(self.promo.id), "quantity": "1", "discount_total": "5.00"}, format="json")
        self.assertIn(r.status_code, (200, 201), r.data)
        line = CartItem.objects.get(cart=cart)
        self.assertEqual((line.line_discount, line.discount_source, line.manual_discount),
                         (Decimal("7.50"), "promotion", Decimal("5.00")))
        data = self.api.get(f"/api/main/pos/carts/{cart.id}/").data
        row = data["items"][0]
        self.assertEqual((row["discount_source"], row["manual_discount_ignored"]), ("promotion", True))


class ReturnLinesTests(KassaBase):
    """BE2-01: строки возврата с ценой, скидкой, причиной и сменой."""

    def test_partial_return_lines_and_shift_filter(self):
        r = self.quick(items=[{"product": str(self.product.id), "qty": "2", "price": "100.00", "discount": "20.00"}],
                       shift=str(self.shift.id))
        self.assertEqual(r.status_code, 201, r.data)
        sale = Sale.objects.get(id=r.data["id"])
        item = sale.items.get()
        r = self.api.post(f"/api/main/pos/sales/{sale.id}/return/",
                          {"items": [{"sale_item_id": str(item.id), "quantity": 1}], "reason": "не подошёл",
                           "refund_method": "cash"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

        rows = self.api.get(f"/api/main/pos/returns/?shift={self.shift.id}").data
        self.assertEqual(len(rows), 1)
        ret = rows[0]
        self.assertEqual(ret["shift"], str(self.shift.id))
        self.assertEqual((ret["total"], ret["refund_method"]), ("90.00", "cash"))
        line = ret["items"][0]
        self.assertEqual(
            (line["product"], line["qty"], line["price"], line["discount"], line["total"], line["reason"], line["restock"]),
            (str(self.product.id), "1.000", "100.00", "10.00", "90.00", "не подошёл", True),
        )
        other = CashShift.objects.create(company=self.company, cashbox=self.cashbox, cashier=self.cashier,
                                         status=CashShift.Status.OPEN)
        self.assertEqual(self.api.get(f"/api/main/pos/returns/?shift={other.id}").data, [])

    def test_defect_return_is_not_restocked(self):
        sale_id = self.quick(shift=str(self.shift.id)).data["id"]
        r = self.api.post(f"/api/main/pos/sales/{sale_id}/return/", {"is_defect": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        line = self.api.get("/api/main/pos/returns/").data[0]["items"][0]
        self.assertEqual((line["reason"], line["restock"], line["total"]), ("defect", False, "200.00"))


class HealthAndErrorFormatTests(KassaBase):
    """BE2-09 и п. 2.1/2.4: health без авторизации, JSON-ошибки с code."""

    def test_health_ok_without_auth(self):
        r = APIClient().get("/api/health/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["status"], body["db"]), ("ok", "ok"))
        self.assertIn("time", body)
        self.assertIn("version", body)

    def test_health_db_down_is_503_json_with_retry_after(self):
        with mock.patch("core.views_health.connection") as conn:
            conn.cursor.side_effect = Exception("db is down")
            r = APIClient().get("/api/health/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r["Retry-After"], "30")
        self.assertEqual(r.json()["db"], "down")

    def test_errors_carry_code(self):
        r = APIClient().get("/api/main/pos/returns/")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.data["code"], "not_authenticated")
        r = self.api.get(f"/api/main/pos/sales/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data["code"], "not_found")

    def test_field_errors_keep_their_shape(self):
        r = self.api.get("/api/main/pos/returns/?date_from=bad")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data), {"date_from"})

    def test_django_validation_error_is_400_not_500(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from core.exceptions import api_exception_handler

        resp = api_exception_handler(DjangoValidationError("Остаток меньше нуля."), {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((resp.data["detail"], resp.data["code"]), ("Остаток меньше нуля.", "invalid"))


class IdempotencyKeyTests(KassaBase):
    """BE2-11: повтор операции с тем же Idempotency-Key не создаёт дубль."""

    def test_return_twice_with_same_key_creates_one_return(self):
        sale_id = self.quick(shift=str(self.shift.id)).data["id"]
        url = f"/api/main/pos/sales/{sale_id}/return/"
        r1 = self.api.post(url, {"reason": "брак"}, format="json", HTTP_IDEMPOTENCY_KEY="ret-1")
        self.assertEqual(r1.status_code, 200, r1.data)
        r2 = self.api.post(url, {"reason": "брак"}, format="json", HTTP_IDEMPOTENCY_KEY="ret-1")
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(r2.json(), r1.json())
        self.assertEqual(SaleReturn.objects.count(), 1)

    def test_same_key_other_body_is_conflict(self):
        body = {"cashbox": str(self.cashbox.id), "shift": str(self.shift.id), "type": "income", "name": "Внесение",
                "amount": "10.00", "source_kind": "shift_drawer_inflow", "payment_method": "cash"}
        r = self.api.post("/api/construction/cashflows/", body, format="json", HTTP_IDEMPOTENCY_KEY="cf-1")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.api.post("/api/construction/cashflows/", body, format="json", HTTP_IDEMPOTENCY_KEY="cf-1")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(CashFlow.objects.filter(source_kind="shift_drawer_inflow").count(), 1)
        r = self.api.post("/api/construction/cashflows/", {**body, "amount": "20.00"}, format="json",
                          HTTP_IDEMPOTENCY_KEY="cf-1")
        self.assertEqual((r.status_code, r.data["code"]), (409, "idempotency_conflict"))

    def test_client_create_and_shift_open_close(self):
        body = {"full_name": "Айгуль", "phone": "+996700000001"}
        ids = {self.api.post("/api/main/clients/", body, format="json", HTTP_IDEMPOTENCY_KEY="cl-1").data["id"]
               for _ in range(2)}
        self.assertEqual(len(ids), 1)

        self.shift.status = CashShift.Status.CLOSED
        self.shift.save()
        open_body = {"cashbox": str(self.cashbox.id), "opening_cash": "100.00"}
        r1 = self.api.post("/api/construction/shifts/open/", open_body, format="json", HTTP_IDEMPOTENCY_KEY="sh-1")
        self.assertEqual(r1.status_code, 201, r1.data)
        r2 = self.api.post("/api/construction/shifts/open/", open_body, format="json", HTTP_IDEMPOTENCY_KEY="sh-1")
        self.assertEqual((r2.status_code, r2.data["id"]), (201, r1.data["id"]))

        close_url = f"/api/construction/shifts/{r1.data['id']}/close/"
        c1 = self.api.post(close_url, {"closing_cash": "100.00"}, format="json", HTTP_IDEMPOTENCY_KEY="sh-close")
        self.assertIn(c1.status_code, (200, 201), c1.data)
        c2 = self.api.post(close_url, {"closing_cash": "100.00"}, format="json", HTTP_IDEMPOTENCY_KEY="sh-close")
        self.assertEqual(c2.status_code, c1.status_code)
        self.assertEqual(c2["Idempotent-Replayed"], "true")

    def test_failed_request_can_be_retried_with_same_key(self):
        r = self.api.post("/api/main/clients/", {"phone": "x"}, format="json", HTTP_IDEMPOTENCY_KEY="cl-bad")
        self.assertEqual(r.status_code, 400)
        r = self.api.post("/api/main/clients/", {"full_name": "Бек", "phone": "+996700000002"}, format="json",
                          HTTP_IDEMPOTENCY_KEY="cl-bad")
        self.assertEqual(r.status_code, 201, r.data)


class OfflineSaleTests(KassaBase):
    """BE2-10: продажа без связи попадает в свой день и свою (даже закрытую) смену."""

    def test_offline_sale_into_closed_shift_two_days_later(self):
        from datetime import timedelta
        from django.utils import timezone

        sold_at = timezone.now() - timedelta(days=2)
        CashShift.objects.filter(pk=self.shift.pk).update(opened_at=sold_at - timedelta(hours=1))
        self.shift.refresh_from_db()
        self.shift.close(closing_cash=Decimal("1000.00"))
        self.assertEqual(self.shift.sales_count, 0)

        body = {"offline": True, "offline_created_at": sold_at.isoformat(), "shift": str(self.shift.id)}
        r = self.quick(key="off-1", **body)
        self.assertEqual(r.status_code, 201, r.data)
        sale = Sale.objects.get(id=r.data["id"])
        self.assertTrue(sale.is_offline)
        self.assertEqual(sale.shift_id, self.shift.id)
        self.assertEqual((sale.created_at, sale.paid_at), (sold_at, sold_at))
        self.assertIsNotNone(sale.received_at)
        flow = CashFlow.objects.get(source_id=str(sale.id))
        self.assertEqual((flow.created_at, flow.shift_id), (sold_at, self.shift.id))

        self.shift.refresh_from_db()
        self.assertEqual((self.shift.sales_count, self.shift.sales_total), (1, Decimal("200.00")))

        again = self.quick(key="off-1", **body)
        self.assertEqual((again.status_code, again.data["replayed"]), (200, True))
        self.assertEqual(Sale.objects.count(), 1)

    def test_offline_time_is_validated(self):
        from datetime import timedelta
        from django.utils import timezone

        r = self.quick(offline=True)
        self.assertEqual(r.status_code, 400)
        r = self.quick(offline=True, offline_created_at=(timezone.now() + timedelta(hours=1)).isoformat())
        self.assertEqual(r.status_code, 400)
        r = self.quick(offline=True, offline_created_at=(timezone.now() - timedelta(days=8)).isoformat())
        self.assertEqual(r.status_code, 400)
