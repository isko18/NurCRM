from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.construction.models import Cashbox, CashShift
from apps.main.models import Cart, CartItem, Product
from apps.main.pos_serializers import AddItemSerializer, CartItemPatchSerializer
from apps.main.pos_views import _line_discount_from_request
from apps.main.services import checkout_cart
from apps.users.models import Branch, Company


User = get_user_model()


class PosLineDiscountTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="pos-discount@test.com", password="pass")
        self.company = Company.objects.create(name="POS Discount Co", owner=self.owner)
        self.branch = Branch.objects.create(name="POS Branch", company=self.company)
        self.owner.company = self.company
        self.owner.branch = self.branch
        self.owner.save(update_fields=["company", "branch"])
        self.cashbox = Cashbox.objects.create(company=self.company, branch=self.branch, name="Касса")
        self.shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            cashier=self.owner,
            status=CashShift.Status.OPEN,
        )
        self.product = Product.objects.create(
            company=self.company,
            branch=self.branch,
            name="Товар со скидкой",
            price=Decimal("25.00"),
            purchase_price=Decimal("10.00"),
            quantity=Decimal("100.000"),
        )

    def test_percent_line_discount_is_calculated_from_line_total(self):
        discount = _line_discount_from_request(
            Decimal("25.00"),
            Decimal("5.000"),
            discount_percent=Decimal("8.00"),
        )

        self.assertEqual(discount, Decimal("10.00"))

    def test_serializers_accept_percent_discount_without_total_discount(self):
        add_ser = AddItemSerializer(
            data={
                "product_id": str(self.product.id),
                "quantity": "5",
                "unit_price": "25",
                "discount_percent": "8",
            }
        )
        patch_ser = CartItemPatchSerializer(
            data={"quantity": "5", "unit_price": "25", "discount_percent": "8"},
            partial=True,
        )

        self.assertTrue(add_ser.is_valid(), add_ser.errors)
        self.assertTrue(patch_ser.is_valid(), patch_ser.errors)

    def test_line_discount_reduces_cart_and_sale_total_not_unit_price(self):
        cart = Cart.objects.create(
            company=self.company,
            branch=self.branch,
            user=self.owner,
            shift=self.shift,
            status=Cart.Status.ACTIVE,
        )
        CartItem.objects.create(
            cart=cart,
            company=self.company,
            branch=self.branch,
            product=self.product,
            quantity=Decimal("5.000"),
            unit_price=Decimal("25.00"),
            line_discount=Decimal("10.00"),
        )

        cart.refresh_from_db()
        self.assertEqual(cart.subtotal, Decimal("125.00"))
        self.assertEqual(cart.discount_total, Decimal("10.00"))
        self.assertEqual(cart.total, Decimal("115.00"))

        sale = checkout_cart(cart)
        sale_item = sale.items.get()

        self.assertEqual(sale_item.unit_price, Decimal("25.00"))
        self.assertEqual(sale_item.quantity, Decimal("5.000"))
        self.assertEqual(sale_item.line_discount, Decimal("10.00"))
        self.assertEqual(sale_item.line_total, Decimal("115.00"))
        self.assertEqual(sale.subtotal, Decimal("125.00"))
        self.assertEqual(sale.discount_total, Decimal("10.00"))
        self.assertEqual(sale.total, Decimal("115.00"))
