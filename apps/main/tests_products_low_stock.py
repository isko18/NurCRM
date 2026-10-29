"""«Сводка» → «Заканчивается на складе»: ordering=quantity, услуги, preset=low_stock."""
from decimal import Decimal

from apps.main.models import Product
from apps.main.tests_kassa_api import KassaBase


class LowStockListTests(KassaBase):
    def mk(self, name, qty, kind="product", minimum="0"):
        return Product.objects.create(
            company=self.company, name=name, price=Decimal("10"), quantity=Decimal(str(qty)),
            kind=kind, minimum_quantity=Decimal(minimum),
        )

    def names(self, **params):
        r = self.api.get("/api/main/products/list/", params)
        self.assertEqual(r.status_code, 200, r.data)
        return [x["name"] for x in r.data["results"]]

    def test_ordering_by_quantity_with_services_cut_in_one_request(self):
        self.product.delete()
        for i in range(30):
            self.mk(f"Товар {i}", 100 + i)
        self.mk("Мало 1", 1)
        self.mk("Мало 3", 3)
        self.mk("Мало 5", 5)
        self.mk("Мало 5.5", "5.5")
        self.mk("Услуга", 0, kind="service")
        names = self.names(stock_type="total", stock_condition="lt", stock_value=6,
                           kind=["product", "bundle"], ordering="quantity", page_size=3)
        self.assertEqual(names, ["Мало 1", "Мало 3", "Мало 5"])
        # дробный остаток 5.5 отсекается порогом ≤5; услуг нет
        self.assertEqual(self.names(preset="low_stock", ordering="quantity", page_size=10),
                         ["Мало 1", "Мало 3", "Мало 5"])

    def test_favorites_do_not_jump_ahead_when_sorting_by_quantity(self):
        from apps.main.models import ProductFavorite

        self.product.delete()
        many = self.mk("Много", 50)
        self.mk("Мало", 1)
        ProductFavorite.objects.create(company=self.company, product=many)
        self.assertEqual(self.names(ordering="quantity")[:2], ["Мало", "Много"])

    def test_low_stock_uses_own_minimum_and_default_threshold(self):
        self.product.delete()
        self.mk("Свой минимум 20, остаток 15", 15, minimum="20")
        self.mk("Свой минимум 20, остаток 20", 20, minimum="20")
        self.mk("Свой минимум 20, остаток 21", 21, minimum="20")
        self.mk("Без минимума, остаток 4", 4)
        self.mk("Без минимума, остаток 40", 40)
        self.mk("Услуга", 0, kind="service", minimum="5")
        self.assertEqual(
            set(self.names(preset="low_stock")),
            {"Свой минимум 20, остаток 15", "Свой минимум 20, остаток 20", "Без минимума, остаток 4"},
        )
        self.assertEqual(self.names(preset="low_stock", low_stock_threshold=50, kind="product").count("Без минимума, остаток 40"), 1)

    def test_default_order_still_puts_favorites_first(self):
        from apps.main.models import ProductFavorite

        self.product.delete()
        old_fav = self.mk("Старый избранный", 1)
        self.mk("Новый", 2)
        ProductFavorite.objects.create(company=self.company, product=old_fav)
        self.assertEqual(self.names(), ["Старый избранный", "Новый"])
