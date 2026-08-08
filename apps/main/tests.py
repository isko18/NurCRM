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
    _scale_barcode_variants,
    _should_use_main_stock_in_agent_sale,
)
from apps.main.views import ProductWarehouseBarcodeAPIView, ProductCreateManualAPIView
from apps.users.models import (
    Roles,
    Company,
    Branch,
    SCALE_BARCODE_AMOUNT_UNIT_SOM,
    SCALE_BARCODE_AMOUNT_UNIT_TIYIN,
    SCALE_BARCODE_LAYOUT_CODE,
    SCALE_BARCODE_LAYOUT_PLU,
    SCALE_BARCODE_MODE_AMOUNT,
    SCALE_BARCODE_MODE_AUTO,
)
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

    def test_parse_scale_barcode_amount_in_tiyin(self):
        # По умолчанию сумма в ШК считается тыйынами: «00044» → 0.44 сом.
        data = _parse_scale_barcode(self.BARCODE)
        self.assertIsNotNone(data)
        self.assertEqual(data["prefix"], "25")
        self.assertEqual(data["plu"], 1)
        self.assertEqual(data["raw_code"], "00001")
        self.assertEqual(data["amount_raw"], "00044")
        self.assertEqual(data["amount"], Decimal("0.44"))
        self.assertEqual(data["check_digit"], "1")
        self.assertEqual(data["mode"], "amount_plain")
        self.assertNotIn("weight_kg", data)

    def test_parse_scale_barcode_amount_in_som(self):
        # Переключатель «Сом»: поле читается как целые сомы, «00044» → 44 сом.
        data = _parse_scale_barcode(
            self.BARCODE,
            SCALE_BARCODE_MODE_AUTO,
            SCALE_BARCODE_LAYOUT_PLU,
            SCALE_BARCODE_AMOUNT_UNIT_SOM,
        )
        self.assertEqual(data["amount"], Decimal("44"))
        self.assertEqual(data["amount_unit"], SCALE_BARCODE_AMOUNT_UNIT_SOM)
        self.assertEqual(data["mode"], "amount_plain")

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
        scale_data = _parse_scale_barcode(
            self.BARCODE,
            SCALE_BARCODE_MODE_AUTO,
            SCALE_BARCODE_LAYOUT_PLU,
            SCALE_BARCODE_AMOUNT_UNIT_SOM,
        )
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


class PosScaleBarcodeVariantsTests(TestCase):
    """Запасной разбор весового ШК, когда scale_barcode_layout настроен не так,
    как печатают весы: 2 00453 00054 9 → PLU 453 (plu) либо 4530 (code)."""

    BARCODE = "2000453000549"

    def test_code_layout_falls_back_to_plu_layout(self):
        variants = _scale_barcode_variants(
            self.BARCODE, SCALE_BARCODE_MODE_AUTO, SCALE_BARCODE_LAYOUT_CODE
        )
        self.assertEqual([v["plu"] for v in variants], [4530, 453])
        self.assertEqual(variants[0]["weight_kg"], Decimal("0.054"))
        self.assertEqual(variants[1]["weight_kg"], Decimal("0.054"))

    def test_plu_layout_falls_back_to_code_layout(self):
        variants = _scale_barcode_variants(
            self.BARCODE, SCALE_BARCODE_MODE_AUTO, SCALE_BARCODE_LAYOUT_PLU
        )
        self.assertEqual([v["plu"] for v in variants], [453, 4530])

    def test_amount_barcode_fallback_stays_amount(self):
        # Префикс 25 → в поле зашита сумма. Запасная раскладка даёт другой PLU,
        # но поле по-прежнему читается как сумма (а не как вес).
        variants = _scale_barcode_variants(
            "2500001000441", SCALE_BARCODE_MODE_AUTO, SCALE_BARCODE_LAYOUT_PLU
        )
        self.assertEqual([v["plu"] for v in variants], [1, 10])
        self.assertEqual([v["mode"] for v in variants], ["amount_plain", "amount_plain"])

    def test_prefix_20_amount_in_som_reads_36(self):
        """Этикетка «Банан вес»: 2 00001 00036 6 — префикс 20, но в поле зашита
        СУММА целыми сомами (36), а не вес. Читается переключателями
        scale_barcode_mode=amount + scale_barcode_amount_unit=som."""
        variants = _scale_barcode_variants(
            "2000001000366",
            SCALE_BARCODE_MODE_AMOUNT,
            SCALE_BARCODE_LAYOUT_PLU,
            SCALE_BARCODE_AMOUNT_UNIT_SOM,
        )
        self.assertEqual([v["plu"] for v in variants], [1, 10])
        self.assertEqual([v["amount"] for v in variants], [Decimal("36"), Decimal("36")])
        self.assertEqual(variants[0]["mode"], "amount_plain")

        # 36 сом при цене 190 сом/кг → 0.189 кг (на этикетке 0.190).
        product = SimpleNamespace(price=Decimal("190"))
        self.assertIsNone(_finalize_scale_data_for_product(product, variants[0]))
        self.assertEqual(
            _effective_qty_from_scale_data(variants[0], Decimal("1.000")),
            Decimal("0.189"),
        )

    def test_prefix_20_default_settings_still_weight(self):
        # Без переключателей поведение прежнее: префикс 20 = вес в граммах.
        variants = _scale_barcode_variants(
            "2000001000366", SCALE_BARCODE_MODE_AUTO, SCALE_BARCODE_LAYOUT_PLU,
            SCALE_BARCODE_AMOUNT_UNIT_TIYIN,
        )
        self.assertEqual(variants[0]["mode"], "weight")
        self.assertEqual(variants[0]["weight_kg"], Decimal("0.036"))

    def test_non_scale_barcode_has_no_variants(self):
        self.assertEqual(
            _scale_barcode_variants("0100001000441", SCALE_BARCODE_MODE_AUTO,
                                    SCALE_BARCODE_LAYOUT_PLU),
            [],
        )


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


class ServiceKindTestCase(TestCase):
    def setUp(self):
        from apps.construction.models import Cashbox, CashShift
        from apps.main.models import Cart, CartItem
        from apps.main.services import checkout_cart, apply_product_list_filters

        self.owner = User.objects.create_user(email="service-owner@test.com", password="testpass123")
        self.company = Company.objects.create(name="Service Test Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save(update_fields=["company"])
        self.cashbox = Cashbox.objects.create(name="Main Cashbox", company=self.company)
        self.shift = CashShift.objects.create(cashbox=self.cashbox, cashier=self.owner, status=CashShift.Status.OPEN)
        self.api_factory = APIRequestFactory()

    def test_create_service_manual_sets_quantity_zero(self):
        req = self.api_factory.post(
            "/main/products/create-manual/",
            {
                "name": "Стрижка модельная",
                "kind": "service",
                "barcode": "2000000000015",
                "price": "800",
                "quantity": 0,
            },
            format="json",
        )
        force_authenticate(req, user=self.owner)
        resp = ProductCreateManualAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", resp.content))
        self.assertEqual(resp.data["kind"], "service")
        self.assertEqual(Decimal(str(resp.data["quantity"])), Decimal("0"))

    def test_checkout_service_does_not_check_or_decrement_stock(self):
        from apps.main.models import Cart, CartItem, Sale
        from apps.main.services import checkout_cart

        service_prod = Product.objects.create(
            company=self.company,
            name="Консультация",
            kind=Product.Kind.SERVICE,
            price=Decimal("1500"),
            quantity=Decimal("0"),
        )
        cart = Cart.objects.create(
            company=self.company,
            user=self.owner,
            shift=self.shift,
            cashbox=self.cashbox,
            status=Cart.Status.ACTIVE,
        )
        CartItem.objects.create(
            cart=cart,
            company=self.company,
            product=service_prod,
            quantity=Decimal("5"),
            unit_price=Decimal("1500"),
        )

        sale = checkout_cart(cart)
        self.assertEqual(sale.status, Sale.Status.NEW)
        service_prod.refresh_from_db()
        # Quantity remains 0, stock not checked or decremented
        self.assertEqual(service_prod.quantity, Decimal("0"))

    def test_preset_filters_exclude_services(self):
        from apps.main.services.product_list_filters import apply_product_list_filters

        service_prod = Product.objects.create(
            company=self.company,
            name="Услуга 1",
            kind=Product.Kind.SERVICE,
            price=Decimal("500"),
            quantity=Decimal("0"),
        )
        physical_prod = Product.objects.create(
            company=self.company,
            name="Товар 1",
            kind=Product.Kind.PRODUCT,
            price=Decimal("100"),
            quantity=Decimal("0"),
        )

        qs = Product.objects.filter(company=self.company)
        filtered_qs = apply_product_list_filters(qs, {"preset": "out_of_stock"})

        res_ids = list(filtered_qs.values_list("id", flat=True))
        self.assertIn(physical_prod.id, res_ids)
        self.assertNotIn(service_prod.id, res_ids)

