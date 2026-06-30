from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.building import services


class BuildingServicesHelpersTest(SimpleTestCase):
    def test_default_currency_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(services.default_currency(), "KGS")

    def test_cash_amount_for_debt_is_zero(self):
        amount = services.cash_amount_for_source(
            company_id="00000000-0000-0000-0000-000000000001",
            payment_mode="debt",
            total=Decimal("1000.00"),
            source_type="work_entry",
            source_id="00000000-0000-0000-0000-000000000002",
        )
        self.assertEqual(amount, Decimal("0.00"))

    def test_cash_amount_for_mixed_subtracts_barter(self):
        with patch.object(services, "_barter_total", return_value=Decimal("300.00")):
            amount = services.cash_amount_for_source(
                company_id="00000000-0000-0000-0000-000000000001",
                payment_mode="mixed",
                total=Decimal("1000.00"),
                source_type="procurement",
                source_id="00000000-0000-0000-0000-000000000002",
            )
        self.assertEqual(amount, Decimal("700.00"))

    def test_treaty_payment_mode_maps_valid_values(self):
        self.assertEqual(services._treaty_payment_mode("barter"), "barter")
        self.assertEqual(services._treaty_payment_mode("unknown"), "cash")

    def test_decrement_stock_item_raises_on_insufficient_qty(self):
        stock_item = MagicMock()
        stock_item.quantity = Decimal("1.000")
        stock_item.name = "Цемент"
        with patch.object(services.BuildingWarehouseStockItem.objects, "select_for_update") as sfu:
            sfu.return_value.get.return_value = stock_item
            with self.assertRaises(Exception):
                services.decrement_stock_item(
                    stock_item_id="00000000-0000-0000-0000-000000000001",
                    warehouse_id="00000000-0000-0000-0000-000000000002",
                    qty=Decimal("5"),
                )
