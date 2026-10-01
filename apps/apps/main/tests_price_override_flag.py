from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.construction.models import Cashbox, CashShift
from apps.main.document import SaleReceiptAPIView
from apps.main.models import Cart, CartItem, Product, Sale, SaleItem
from apps.main.pos_views import CartItemUpdateDestroyAPIView, SaleReceiptDataAPIView
from apps.main.services import checkout_cart
from apps.users.models import Branch, Company, SubscriptionPlan

User = get_user_model()


class PriceOverrideFlagAPITests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_po_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Price Override Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashbox = Cashbox.objects.create(name="Main Cashbox", company=self.company, branch=self.branch)
        self.shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            cashier=self.owner,
            status=CashShift.Status.OPEN,
        )

        self.prod_lemonade = Product.objects.create(
            name="Лимонад Kt",
            company=self.company,
            price=Decimal("250.00"),
            purchase_price=Decimal("150.00"),
            quantity=Decimal("100.000"),
        )

        self.cart = Cart.objects.create(
            company=self.company,
            branch=self.branch,
            shift=self.shift,
            user=self.owner,
            status=Cart.Status.ACTIVE,
        )

        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            company=self.company,
            branch=self.branch,
            product=self.prod_lemonade,
            unit_price=Decimal("250.00"),
            quantity=Decimal("1.000"),
            line_discount=Decimal("0.00"),
        )
        self.cart.recalc()

    def test_cart_item_patch_unit_price_sets_price_manually_edited(self):
        # Initial is False
        self.assertFalse(self.cart_item.price_manually_edited)

        # Patch unit_price 250 -> 230
        req = self.factory.patch(
            f"/api/main/pos/carts/{self.cart.id}/items/{self.cart_item.id}/",
            {"unit_price": "230.00"},
            format="json",
        )
        force_authenticate(req, user=self.owner)
        view = CartItemUpdateDestroyAPIView.as_view()
        resp = view(req, cart_id=self.cart.id, item_id=self.cart_item.id)
        self.assertEqual(resp.status_code, 200)

        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.unit_price, Decimal("230.00"))
        self.assertTrue(self.cart_item.price_manually_edited)

        # Serializer check
        items_data = resp.data["items"]
        self.assertEqual(len(items_data), 1)
        self.assertTrue(items_data[0]["price_manually_edited"])

    def test_checkout_and_receipt_shows_price_manually_edited_and_reason(self):
        # 1. Edit cart item price
        self.cart_item.unit_price = Decimal("230.00")
        self.cart_item.price_manually_edited = True
        self.cart_item.save(update_fields=["unit_price", "price_manually_edited"])
        self.cart.recalc()

        # 2. Checkout
        sale = checkout_cart(
            self.cart,
            payment_method=Sale.PaymentMethod.CASH,
            cash_received=Decimal("230.00"),
        )
        sale_item = sale.items.first()
        self.assertIsNotNone(sale_item)
        self.assertEqual(sale_item.unit_price, Decimal("230.00"))
        self.assertTrue(sale_item.price_manually_edited)

        # 3. GET JSON receipt
        req_receipt = self.factory.get(f"/api/main/sales/json/{sale.id}/receipt/")
        force_authenticate(req_receipt, user=self.owner)
        view_receipt = SaleReceiptAPIView.as_view()
        resp_receipt = view_receipt(req_receipt, pk=sale.id)
        self.assertEqual(resp_receipt.status_code, 200)

        rc_items = resp_receipt.data["items"]
        self.assertEqual(len(rc_items), 1)
        item0 = rc_items[0]
        self.assertEqual(item0["unit_price"], "230")
        self.assertEqual(item0["line_discount"], "0")
        self.assertEqual(item0["line_total"], "230")
        self.assertTrue(item0["price_manually_edited"])
        self.assertEqual(item0["price_override_reason"], "manual")

        # 4. GET POS receipt
        req_pos = self.factory.get(f"/api/main/pos/sales/{sale.id}/receipt/")
        force_authenticate(req_pos, user=self.owner)
        view_pos = SaleReceiptDataAPIView.as_view()
        resp_pos = view_pos(req_pos, pk=sale.id)
        self.assertEqual(resp_pos.status_code, 200)

        pos_items = resp_pos.data["items"]
        self.assertEqual(len(pos_items), 1)
        self.assertTrue(pos_items[0]["price_manually_edited"])
        self.assertEqual(pos_items[0]["price_override_reason"], "manual")

    def test_discount_only_shows_discount_override_reason(self):
        # Non-edited price with discount
        self.cart_item.unit_price = Decimal("250.00")
        self.cart_item.line_discount = Decimal("20.00")
        self.cart_item.price_manually_edited = False
        self.cart_item.save(update_fields=["unit_price", "line_discount", "price_manually_edited"])
        self.cart.recalc()

        sale = checkout_cart(
            self.cart,
            payment_method=Sale.PaymentMethod.CASH,
            cash_received=Decimal("230.00"),
        )
        req_receipt = self.factory.get(f"/api/main/sales/json/{sale.id}/receipt/")
        force_authenticate(req_receipt, user=self.owner)
        resp_receipt = SaleReceiptAPIView.as_view()(req_receipt, pk=sale.id)
        self.assertEqual(resp_receipt.status_code, 200)

        item0 = resp_receipt.data["items"][0]
        self.assertFalse(item0["price_manually_edited"])
        self.assertEqual(item0["price_override_reason"], "discount")
        self.assertEqual(item0["line_discount"], "20")
