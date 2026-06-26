from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.main.models import Product, ProductAlternateBarcode
from apps.main.pos_views import (
    _effective_qty_from_scale_data,
    _finalize_scale_data_for_product,
    _parse_scale_barcode,
    _parse_scale_barcode_loose,
    _should_use_main_stock_in_agent_sale,
)
from apps.main.views import ProductWarehouseBarcodeAPIView, ProductCreateManualAPIView
from apps.users.models import Roles, Company, Branch
from django.contrib.auth import get_user_model

User = get_user_model()


class PosOwnerAgentSaleTests(TestCase):
    def test_owner_uses_main_stock_even_when_agent_selected(self):
        owner = SimpleNamespace(role=Roles.OWNER, id="owner-id", company=None)
        selected_agent = SimpleNamespace(id="agent-id")

        self.assertTrue(
            _should_use_main_stock_in_agent_sale(user=owner, acting_agent=selected_agent)
        )


class PosScaleBarcodeTests(TestCase):
    # Итоговый префикс (25): в ШК зашита СТОИМОСТЬ в сомах.
    BARCODE = "2500001000441"
    # Весовой префикс (20): в ШК зашит ВЕС в граммах.
    WEIGHT_BARCODE = "2004626002149"

    def test_parse_scale_barcode_amount_in_som(self):
        data = _parse_scale_barcode(self.BARCODE)
        self.assertIsNotNone(data)
        self.assertEqual(data["prefix"], "25")
        self.assertEqual(data["plu"], 1)
        self.assertEqual(data["raw_code"], "00001")
        self.assertEqual(data["amount_raw"], "00044")
        self.assertEqual(data["amount"], Decimal("44"))
        self.assertEqual(data["check_digit"], "1")
        self.assertEqual(data["mode"], "amount_plain")
        self.assertNotIn("weight_kg", data)

    def test_parse_weight_barcode_grams_to_kg(self):
        # Весовой штрихкод ШТРИХ: поле = вес в граммах, без деления на цену.
        data = _parse_scale_barcode(self.WEIGHT_BARCODE)
        self.assertIsNotNone(data)
        self.assertEqual(data["prefix"], "20")
        self.assertEqual(data["plu"], 4626)
        self.assertEqual(data["mode"], "weight")
        self.assertEqual(data["weight_raw"], 214)
        self.assertEqual(data["weight_kg"], Decimal("0.214"))
        self.assertNotIn("amount", data)

    def test_weight_barcode_effective_qty_independent_of_price(self):
        # 0.214 кг берётся напрямую из ШК; цена за кг не влияет на вес.
        scale_data = _parse_scale_barcode(self.WEIGHT_BARCODE)
        product = SimpleNamespace(price=Decimal("100"))
        # Для весового режима finalize — no-op (не делит на цену).
        self.assertIsNone(_finalize_scale_data_for_product(product, scale_data))
        self.assertNotIn("quantity_kg", scale_data)
        self.assertEqual(
            _effective_qty_from_scale_data(scale_data, Decimal("9.000")),
            Decimal("0.214"),
        )

    def test_quantity_amount_44_price_44(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=Decimal("44"))
        self.assertIsNone(_finalize_scale_data_for_product(product, scale_data))
        self.assertEqual(scale_data["quantity_kg"], Decimal("1.000"))
        self.assertEqual(scale_data["mode"], "amount_plain")
        self.assertEqual(scale_data["amount"], Decimal("44"))
        self.assertEqual(scale_data["plu"], 1)

    def test_finalize_rejects_zero_price(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=Decimal("0"))
        err = _finalize_scale_data_for_product(product, scale_data)
        self.assertEqual(err, "Невозможно рассчитать вес: у товара не указана цена")
        self.assertNotIn("quantity_kg", scale_data)

    def test_finalize_rejects_missing_price(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=None)
        err = _finalize_scale_data_for_product(product, scale_data)
        self.assertEqual(err, "Невозможно рассчитать вес: у товара не указана цена")

    def test_effective_qty_prefers_quantity_kg_over_request_qty(self):
        scale_data = {"quantity_kg": Decimal("1.000"), "mode": "amount_plain"}
        self.assertEqual(
            _effective_qty_from_scale_data(scale_data, Decimal("9.000")),
            Decimal("1.000"),
        )

    def test_effective_qty_legacy_weight_kg(self):
        scale_data = {"weight_kg": 0.312}
        self.assertEqual(
            _effective_qty_from_scale_data(scale_data, Decimal("1.000")),
            Decimal("0.312"),
        )

    def test_parse_scale_barcode_rejects_non_weight_prefix(self):
        self.assertIsNone(_parse_scale_barcode("0100001000441"))

    def test_loose_parser_unchanged_weight_mode(self):
        loose = _parse_scale_barcode_loose(self.BARCODE)
        self.assertIsNotNone(loose)
        self.assertEqual(loose["plu"], 1)
        self.assertIn("weight_kg", loose)
        self.assertNotIn("mode", loose)


class ProductWarehouseBarcodeAPITestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner-wh@test.com", password="testpass123")
        self.company = Company.objects.create(name="Warehouse Market Co", owner=self.owner)
        self.branch = Branch.objects.create(name="WH Branch", company=self.company)
        self.owner.company = self.company
        self.owner.save(update_fields=["company"])
        self.product = Product.objects.create(
            company=self.company,
            branch=self.branch,
            name="Сабиз",
            code="001",
            article="ART-1",
            barcode="0693123456789",
            unit="кг",
            quantity=Decimal("100.000"),
            price=Decimal("85.00"),
            purchase_price=Decimal("70.00"),
        )
        ProductAlternateBarcode.objects.create(product=self.product, barcode="0693999999999")
        self.api_factory = APIRequestFactory()

    def _get(self, barcode):
        req = self.api_factory.get(f"/main/products/warehouse-barcode/{barcode}/")
        force_authenticate(req, user=self.owner)
        return ProductWarehouseBarcodeAPIView.as_view()(req, barcode=barcode)

    def test_lookup_by_primary_barcode(self):
        resp = self._get("0693123456789")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["product"]["id"], str(self.product.id))
        self.assertEqual(resp.data["matched_barcode"], "0693123456789")
        self.assertEqual(resp.data["product"]["barcode"], "0693123456789")

    def test_lookup_by_alternate_barcode(self):
        resp = self._get("0693999999999")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["product"]["id"], str(self.product.id))

    def test_lookup_preserves_leading_zeros(self):
        resp = self._get("0693123456789")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["matched_barcode"], "0693123456789")

    def test_not_found_returns_404(self):
        resp = self._get("0000000000000")
        self.assertEqual(resp.status_code, 404)

    def test_empty_barcode_returns_400(self):
        req = self.api_factory.get("/main/products/warehouse-barcode//")
        force_authenticate(req, user=self.owner)
        resp = ProductWarehouseBarcodeAPIView.as_view()(req, barcode="")
        self.assertEqual(resp.status_code, 400)


class ProductCreateManualWholesalePriceTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner-wholesale@test.com", password="testpass123")
        self.company = Company.objects.create(name="Wholesale Market Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save(update_fields=["company"])
        self.api_factory = APIRequestFactory()

    def test_create_manual_saves_wholesale_price(self):
        req = self.api_factory.post(
            "/main/products/create-manual/",
            {
                "name": "TEST wholesale",
                "barcode": "8056241343999",
                "article": "0343",
                "unit": "шт",
                "is_weight": False,
                "price": "12.24",
                "wholesale_price": "31",
                "discount_percent": "0",
                "purchase_price": "12",
                "markup_percent": "2",
                "quantity": 10,
                "stock": False,
                "packages_input": [],
                "promotion_rules_input": [],
            },
            format="json",
        )
        force_authenticate(req, user=self.owner)
        resp = ProductCreateManualAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", resp.content))
        self.assertEqual(Decimal(str(resp.data["wholesale_price"])), Decimal("31"))
        product = Product.objects.get(pk=resp.data["id"])
        self.assertEqual(product.wholesale_price, Decimal("31"))
