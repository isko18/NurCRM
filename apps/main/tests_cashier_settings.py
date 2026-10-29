from decimal import Decimal
import uuid
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from django.core.cache import cache

from apps.users.models import Company, Branch, User
from apps.construction.models import Cashbox, CashShift
from apps.main.models import Cart, CartItem, Product


class CashierSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.owner = User.objects.create_user(
            email=f"owner_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            role="owner",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Cashier Settings Test Co",
            owner=self.owner,
            cashier_password="1234",
            max_discount_percent=Decimal("20.00"),
            debt_schedule_version="v1",
        )
        self.owner.company = self.company
        self.owner.save()

        self.cashier = User.objects.create_user(
            email=f"cashier_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            company=self.company,
            role="cashier",
        )

        self.branch = Branch.objects.create(name="Central", company=self.company)
        self.cashbox = Cashbox.objects.create(
            name="Касса 1",
            company=self.company,
            branch=self.branch,
            role=Cashbox.CashboxRole.POS_MAIN,
        )
        self.shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox,
            cashier=self.cashier,
            opening_cash=Decimal("100.00"),
            status=CashShift.Status.OPEN,
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Test Product",
            price=Decimal("100.00"),
            purchase_price=Decimal("50.00"),
        )

    def tearDown(self):
        cache.clear()

    def test_get_settings_owner_vs_cashier(self):
        # Owner sees delete_item_code
        self.client.force_authenticate(user=self.owner)
        res_owner = self.client.get("/main/pos/cashier-settings/")
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(res_owner.data.get("delete_item_code"), "1234")
        self.assertTrue(res_owner.data.get("delete_item_code_required"))
        self.assertEqual(res_owner.data.get("max_discount_percent"), "20.00")
        self.assertEqual(res_owner.data.get("debt_schedule_version"), "v1")
        self.assertFalse(res_owner.data.get("cashflow_requests_enabled"))

        # Cashier does NOT see delete_item_code
        self.client.force_authenticate(user=self.cashier)
        res_cashier = self.client.get("/main/pos/cashier-settings/")
        self.assertEqual(res_cashier.status_code, status.HTTP_200_OK)
        self.assertNotIn("delete_item_code", res_cashier.data)
        self.assertTrue(res_cashier.data.get("delete_item_code_required"))
        self.assertEqual(res_cashier.data.get("max_discount_percent"), "20.00")
        self.assertEqual(res_cashier.data.get("debt_schedule_version"), "v1")
        self.assertFalse(res_cashier.data.get("cashflow_requests_enabled"))

    def test_patch_settings_permissions_and_validation(self):
        # Cashier cannot PATCH
        self.client.force_authenticate(user=self.cashier)
        res_forbidden = self.client.patch("/main/pos/cashier-settings/", {"delete_item_code": "5678"})
        self.assertEqual(res_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        # Owner can PATCH
        self.client.force_authenticate(user=self.owner)
        res_invalid_code = self.client.patch("/main/pos/cashier-settings/", {"delete_item_code": "12"})
        self.assertEqual(res_invalid_code.status_code, status.HTTP_400_BAD_REQUEST)

        res_invalid_discount = self.client.patch("/main/pos/cashier-settings/", {"max_discount_percent": "150"})
        self.assertEqual(res_invalid_discount.status_code, status.HTTP_400_BAD_REQUEST)

        res_invalid_toggle = self.client.patch("/main/pos/cashier-settings/", {"cashflow_requests_enabled": "not_a_bool"}, format="json")
        self.assertEqual(res_invalid_toggle.status_code, status.HTTP_400_BAD_REQUEST)

        res_ok = self.client.patch("/main/pos/cashier-settings/", {
            "delete_item_code": "9876",
            "max_discount_percent": "15.5",
            "debt_schedule_version": "v2",
            "cashflow_requests_enabled": True,
        }, format="json")
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ok.data.get("delete_item_code"), "9876")
        self.assertEqual(res_ok.data.get("max_discount_percent"), "15.5")
        self.assertEqual(res_ok.data.get("debt_schedule_version"), "v2")
        self.assertTrue(res_ok.data.get("cashflow_requests_enabled"))

    def test_verify_delete_code_endpoint(self):
        self.client.force_authenticate(user=self.cashier)

        # Wrong code returns valid=False (status 200)
        res_wrong = self.client.post("/main/pos/cashier-settings/verify-delete-code/", {"code": "0000"})
        self.assertEqual(res_wrong.status_code, status.HTTP_200_OK)
        self.assertFalse(res_wrong.data.get("valid"))

        # Right code returns valid=True (status 200)
        res_right = self.client.post("/main/pos/cashier-settings/verify-delete-code/", {"code": "1234"})
        self.assertEqual(res_right.status_code, status.HTTP_200_OK)
        self.assertTrue(res_right.data.get("valid"))

    def test_cart_item_deletion_protection(self):
        cart = Cart.objects.create(
            company=self.company,
            user=self.cashier,
            shift=self.shift,
            status=Cart.Status.ACTIVE,
        )
        item = CartItem.objects.create(
            cart=cart,
            product=self.product,
            quantity=Decimal("2.000"),
            unit_price=Decimal("100.00"),
            line_discount=Decimal("0.00"),
        )
        cart.recalc()

        self.client.force_authenticate(user=self.cashier)

        # 1. DELETE without code or verification -> 403
        url = f"/main/pos/carts/{cart.id}/items/{item.id}/"
        res_denied = self.client.delete(url)
        self.assertEqual(res_denied.status_code, status.HTTP_403_FORBIDDEN)

        # 2. DELETE with X-Delete-Code header -> 200
        res_header = self.client.delete(url, HTTP_X_DELETE_CODE="1234")
        self.assertEqual(res_header.status_code, status.HTTP_200_OK)
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())

    def test_discount_limit_protection(self):
        cart = Cart.objects.create(
            company=self.company,
            user=self.cashier,
            shift=self.shift,
            status=Cart.Status.ACTIVE,
        )
        item = CartItem.objects.create(
            cart=cart,
            product=self.product,
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
            line_discount=Decimal("0.00"),
        )
        cart.recalc()

        self.client.force_authenticate(user=self.cashier)

        # Max discount is 20%. Attempting 25% discount (25.00) -> 400
        url = f"/main/pos/carts/{cart.id}/items/{item.id}/"
        res_exceed = self.client.patch(url, {"discount_total": "25.00"})
        self.assertEqual(res_exceed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", res_exceed.data)

        # 20.00 discount -> 200
        res_ok = self.client.patch(url, {"discount_total": "20.00"})
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
