from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from apps.main.pos_views import (
    _effective_qty_from_scale_data,
    _finalize_scale_data_for_product,
    _parse_scale_barcode,
    _parse_scale_barcode_loose,
    _should_use_main_stock_in_agent_sale,
)
from apps.users.models import Roles


class PosOwnerAgentSaleTests(TestCase):
    def test_owner_uses_main_stock_even_when_agent_selected(self):
        owner = SimpleNamespace(role=Roles.OWNER, id="owner-id", company=None)
        selected_agent = SimpleNamespace(id="agent-id")

        self.assertTrue(
            _should_use_main_stock_in_agent_sale(user=owner, acting_agent=selected_agent)
        )


class PosScaleBarcodeTests(TestCase):
    BARCODE = "2000001306000"

    def test_parse_scale_barcode_tm_amount_format(self):
        data = _parse_scale_barcode(self.BARCODE)
        self.assertIsNotNone(data)
        self.assertEqual(data["prefix"], "20")
        self.assertEqual(data["plu"], 1)
        self.assertEqual(data["raw_code"], "00001")
        self.assertEqual(data["amount_raw"], "30600")
        self.assertEqual(data["amount"], Decimal("306.00"))
        self.assertEqual(data["check_digit"], "0")
        self.assertEqual(data["mode"], "amount")
        self.assertNotIn("weight_kg", data)

    def test_quantity_from_amount_price_70(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=Decimal("70.00"))
        self.assertIsNone(_finalize_scale_data_for_product(product, scale_data))
        self.assertEqual(scale_data["quantity_kg"], Decimal("4.371"))

    def test_quantity_from_amount_price_equals_label_amount(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=Decimal("306.00"))
        self.assertIsNone(_finalize_scale_data_for_product(product, scale_data))
        self.assertEqual(scale_data["quantity_kg"], Decimal("1.000"))

    def test_finalize_rejects_zero_price(self):
        scale_data = _parse_scale_barcode(self.BARCODE)
        product = SimpleNamespace(price=Decimal("0"))
        err = _finalize_scale_data_for_product(product, scale_data)
        self.assertIn("цена", err.lower())
        self.assertNotIn("quantity_kg", scale_data)

    def test_effective_qty_prefers_quantity_kg_over_request_qty(self):
        scale_data = {"quantity_kg": Decimal("4.371"), "mode": "amount"}
        self.assertEqual(
            _effective_qty_from_scale_data(scale_data, Decimal("9.000")),
            Decimal("4.371"),
        )

    def test_effective_qty_legacy_weight_kg(self):
        scale_data = {"weight_kg": 0.312}
        self.assertEqual(
            _effective_qty_from_scale_data(scale_data, Decimal("1.000")),
            Decimal("0.312"),
        )

    def test_parse_scale_barcode_rejects_non_weight_prefix(self):
        self.assertIsNone(_parse_scale_barcode("0100001306000"))

    def test_loose_parser_unchanged_weight_mode(self):
        loose = _parse_scale_barcode_loose("2000001306000")
        self.assertIsNotNone(loose)
        self.assertEqual(loose["plu"], 1)
        self.assertIn("weight_kg", loose)
        self.assertNotIn("mode", loose)
