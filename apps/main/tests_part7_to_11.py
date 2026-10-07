from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import Company
from apps.main.models import (
    Product,
    ProductVariant,
    Cart,
    CartItem,
    Sale,
    SaleItem,
)
from apps.construction.models import CashShift, Cashbox
from apps.main.services import checkout_cart
from apps.main.telegram_bot.models import (
    TelegramBotSettings,
    TelegramBotScenario,
    TelegramBotAudit,
)

User = get_user_model()


class Part7To11FeatureTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="owner_p711@test.com",
            password="password123",
            role="owner",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="P711 Test Company",
            slug="p711-test-company",
            owner=self.owner,
        )
        self.owner.company = self.company
        self.owner.owned_company = self.company
        self.owner.save()

        self.client.force_authenticate(user=self.owner)

        self.cashbox = Cashbox.objects.create(
            company=self.company,
            name="Касса 1",
        )
        self.shift = CashShift.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            cashier=self.owner,
            opened_at=timezone.now(),
        )

        self.bot_settings = TelegramBotSettings.objects.create(
            company=self.company,
            token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            bot_username="test_p711_bot",
            mode=TelegramBotSettings.Mode.SERVER,
        )

    # 1. TelegramBotScenario CRUD, Audit and Dry-run Test
    def test_scenario_crud_and_audit(self):
        # 1.1 Create scenario
        create_url = "/api/main/telegram-bot/scenarios/"
        payload = {
            "kind": "command",
            "command": "delivery",
            "title": "Условия доставки",
            "reply_text": "Доставка по Бишкеку бесплатно от 1000 сом.",
            "buttons": [
                {"text": "Сайт", "url": "https://example.com"}
            ],
            "audience": "customers",
            "priority": 10,
        }
        res = self.client.post(create_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        scenario_id = res.data["id"]

        # Check that audit log was written
        audit = TelegramBotAudit.objects.filter(company=self.company, action="scenario_create").first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.object_title, "Условия доставки")

        # 1.2 Update scenario
        detail_url = f"/api/main/telegram-bot/scenarios/{scenario_id}/"
        res = self.client.patch(detail_url, {"reply_text": "Доставка по Бишкеку бесплатно от 2000 сом."}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["reply_text"], "Доставка по Бишкеку бесплатно от 2000 сом.")

        # Check update audit
        audit_update = TelegramBotAudit.objects.filter(company=self.company, action="scenario_update").first()
        self.assertIsNotNone(audit_update)

        # 1.3 Dry-run test scenario
        test_url = "/api/main/telegram-bot/scenarios/test/"
        res = self.client.post(test_url, {"text": "/delivery"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data["matched"])
        self.assertEqual(res.data["matched"]["id"], scenario_id)
        self.assertIn("2000 сом", res.data["reply_text"])

        # 1.4 List audit
        audit_url = "/api/main/telegram-bot/audit/"
        res = self.client.get(audit_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 2)

        # 1.5 Delete scenario
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        audit_del = TelegramBotAudit.objects.filter(company=self.company, action="scenario_delete").first()
        self.assertIsNotNone(audit_del)

    # 2. Sync menu API
    @patch("apps.main.telegram_bot.services.telegram_api.set_my_commands")
    def test_scenario_sync_menu(self, mock_set_commands):
        mock_set_commands.return_value = True
        TelegramBotScenario.objects.create(
            company=self.company,
            kind="command",
            command="help",
            title="Помощь",
            reply_text="Инструкция",
            show_in_menu=True,
            is_active=True,
        )
        sync_url = "/api/main/telegram-bot/scenarios/sync-menu/"
        res = self.client.post(sync_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["ok"])
        self.assertTrue(mock_set_commands.called)

    # 3. Loss sales analytics API
    def test_loss_sales_analytics(self):
        p = Product.objects.create(
            company=self.company,
            name="Товар в убыток",
            price=Decimal("100.00"),
            purchase_price=Decimal("150.00"),
            quantity=Decimal("10"),
        )
        sale = Sale.objects.create(
            company=self.company,
            shift=self.shift,
            user=self.owner,
            total=Decimal("100.00"),
            status=Sale.Status.PAID,
            paid_at=timezone.now(),
            created_at=timezone.now(),
        )
        SaleItem.objects.create(
            company=self.company,
            sale=sale,
            product=p,
            name_snapshot="Товар в убыток",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            purchase_price_snapshot=Decimal("150.00"),
        )
        url = "/api/main/analytics/loss-sales/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("sales", res.data)
        self.assertEqual(res.data["sales_count"], 1)
        self.assertEqual(res.data["total_loss"], "50.00")

    # 4. Wholesale POS checkout
    def test_wholesale_pos_checkout(self):
        prod = Product.objects.create(
            company=self.company,
            name="Оптовый товар",
            price=Decimal("500.00"),
            wholesale_price=Decimal("400.00"),
            quantity=Decimal("50"),
        )
        cart = Cart.objects.create(
            company=self.company,
            shift=self.shift,
            is_wholesale=True,
            subtotal=Decimal("4000.00"),
            total=Decimal("4000.00"),
        )
        CartItem.objects.create(
            company=self.company,
            cart=cart,
            product=prod,
            quantity=Decimal("10"),
            unit_price=Decimal("400.00"),
            is_wholesale=True,
        )

        sale = checkout_cart(cart, payments=[{"method": "cash", "amount": Decimal("4000.00")}])
        sale_item = sale.items.first()
        self.assertTrue(sale_item.is_wholesale)
        self.assertEqual(sale_item.unit_price, Decimal("400.00"))
        prod.refresh_from_db()
        self.assertEqual(prod.quantity, Decimal("40"))

    # 5. Variant stock synchronization and required variant on checkout (Part 10 Section 3.5)
    def test_variant_stock_sync_and_validation(self):
        prod = Product.objects.create(
            company=self.company,
            name="Худи",
            price=Decimal("2000.00"),
            quantity=Decimal("0"),
        )
        # Add variants
        v_s = ProductVariant.objects.create(
            company=self.company,
            product=prod,
            size="S",
            color="Черный",
            quantity=Decimal("5"),
            is_active=True,
        )
        v_m = ProductVariant.objects.create(
            company=self.company,
            product=prod,
            size="M",
            color="Черный",
            quantity=Decimal("8"),
            is_active=True,
        )
        # Product quantity should automatically sync to sum of active variants (5 + 8 = 13)
        prod.refresh_from_db()
        self.assertEqual(prod.quantity, Decimal("13"))

        # Update variant quantity
        v_s.quantity = Decimal("10")
        v_s.save()
        prod.refresh_from_db()
        self.assertEqual(prod.quantity, Decimal("18"))

        # Checkout without variant must fail with ValueError / ValidationError
        cart = Cart.objects.create(
            company=self.company,
            shift=self.shift,
            subtotal=Decimal("2000.00"),
            total=Decimal("2000.00"),
        )
        CartItem.objects.create(
            company=self.company,
            cart=cart,
            product=prod,
            quantity=Decimal("1"),
            unit_price=Decimal("2000.00"),
            variant=None,  # No variant selected
        )
        with self.assertRaises(ValueError) as ctx:
            checkout_cart(cart)
        self.assertIn("Выберите размер/цвет", str(ctx.exception))

        # Now set the variant and checkout successfully
        item = cart.items.first()
        item.variant = v_s
        item.save()
        sale = checkout_cart(cart, payments=[{"method": "cash", "amount": Decimal("2000.00")}])
        self.assertIsNotNone(sale)

        # Variant stock decreased by 1 (10 -> 9)
        v_s.refresh_from_db()
        self.assertEqual(v_s.quantity, Decimal("9"))
        # Product stock decreased by 1 (18 -> 17)
        prod.refresh_from_db()
        self.assertEqual(prod.quantity, Decimal("17"))
