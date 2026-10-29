"""
GET /api/main/products/price-check/ — «Проверка цен» калькуляции
(calculator-after-stress-test/01-price-check-endpoint.md).
Контрольный пример — копия describe("проверка цен") из фронтового pricing.test.ts.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.main.models import Product, ProductAlternateBarcode
from apps.users.models import Company

User = get_user_model()
URL = "/api/main/products/price-check/"


class PriceCheckTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="pc-owner@test.com", password="x", role="owner")
        self.company = Company.objects.create(name="PC Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        rows = [
            ("Убыток", 100, 90, 2),
            ("В ноль", 50, 50, 1),
            ("Низкая маржа", 90, 100, 10),
            ("Без закупки", 0, 70, 5),
            ("Минус", 10, 20, -3),
            ("Хорошо", 50, 100, 4),
        ]
        self.p = {}
        for name, cost, price, qty in rows:
            self.p[name] = Product.objects.create(
                company=self.company, name=name,
                purchase_price=Decimal(cost), price=Decimal(price), quantity=Decimal(qty),
            )
        # Услуга не участвует ни в итогах, ни в строках.
        Product.objects.create(
            company=self.company, name="Услуга", kind=Product.Kind.SERVICE,
            purchase_price=Decimal("1"), price=Decimal("1000"), quantity=Decimal("100"),
        )
        Product.objects.filter(pk=self.p["Хорошо"].pk).update(barcode="4870000000017")
        ProductAlternateBarcode.objects.create(product=self.p["Убыток"], barcode="2000000000015")

    def _get(self, **params):
        resp = self.api.get(URL, params, secure=True)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        return resp.data

    def _names(self, data):
        return [r["name"] for r in data["results"]]

    def test_control_example(self):
        data = self._get(threshold="15")
        s = data["summary"]
        self.assertEqual(s["future_profit"], "280.00")
        self.assertEqual(s["average_margin"], "17.18")
        self.assertEqual(s["stock_at_price"], "1980.00")
        self.assertEqual(s["products_total"], 6)
        self.assertEqual(data["counts"], {"all": 6, "loss": 2, "lowMargin": 1, "noCost": 1, "negativeStock": 1})

        flags = {r["name"]: r["flags"] for r in data["results"]}
        self.assertEqual(flags["Убыток"], ["loss"])
        self.assertEqual(flags["В ноль"], ["noMarkup"])
        self.assertEqual(flags["Низкая маржа"], ["lowMargin"])
        self.assertEqual(flags["Без закупки"], ["noCost"])
        self.assertEqual(flags["Минус"], ["negativeStock"])
        self.assertEqual(flags["Хорошо"], [])

        row = {r["name"]: r for r in data["results"]}
        self.assertEqual(row["Низкая маржа"]["margin"], "10.00")
        self.assertEqual(row["Низкая маржа"]["markup"], "11.11")
        self.assertEqual(row["Минус"]["stock_profit"], "0.00")
        self.assertIsNone(row["Без закупки"]["margin"])
        self.assertIsNone(row["Без закупки"]["stock_profit"])
        self.assertEqual(row["Минус"]["quantity"], "-3.000")

    def test_stock_at_cost(self):
        # 2×100 + 1×50 + 10×90 + 5×0 + 0 (минус) + 4×50
        self.assertEqual(self._get()["summary"]["stock_at_cost"], "1350.00")

    def test_filters_and_search(self):
        self.assertEqual(sorted(self._get(filter="loss")["results"][i]["name"] for i in range(2)), ["В ноль", "Убыток"])
        self.assertEqual(self._names(self._get(filter="noCost")), ["Без закупки"])
        self.assertEqual(self._names(self._get(filter="negativeStock")), ["Минус"])
        data = self._get(search="хорош")
        self.assertEqual(self._names(data), ["Хорошо"])
        self.assertEqual(data["counts"]["all"], 1)
        # итоги — по всему складу, без search
        self.assertEqual(data["summary"]["future_profit"], "280.00")
        self.assertEqual(self._names(self._get(search="4870000000017")), ["Хорошо"])
        self.assertEqual(self._names(self._get(search="2000000000015")), ["Убыток"])

    def test_threshold_changes_low_margin(self):
        self.assertEqual(self._get(threshold="5")["counts"]["lowMargin"], 0)
        # 60 %: «Низкая маржа» (10 %), «Минус» и «Хорошо» (по 50 %)
        self.assertEqual(self._get(threshold="60")["counts"]["lowMargin"], 3)

    def test_ordering_and_pagination(self):
        data = self._get(ordering="-margin", page_size="2")
        # маржа 50 % у «Минус» (10→20) и «Хорошо» (50→100): при равенстве — по названию
        self.assertEqual(self._names(data), ["Минус", "Хорошо"])
        self.assertEqual(data["count"], 6)
        self.assertIsNotNone(data["next"])
        data = self._get(ordering="margin")
        self.assertEqual(self._names(data)[-1], "Без закупки")  # null — в конце

    def test_validation(self):
        for params in ({"filter": "x"}, {"threshold": "101"}, {"threshold": "abc"}, {"ordering": "price"}):
            resp = self.api.get(URL, params, secure=True)
            self.assertEqual(resp.status_code, 400, params)

    def test_other_company_and_archived_hidden(self):
        other = User.objects.create_user(email="pc-other@test.com", password="x", role="owner")
        oc = Company.objects.create(name="Other", owner=other)
        Product.objects.create(company=oc, name="Чужой", purchase_price=1, price=2, quantity=1)
        Product.objects.filter(pk=self.p["Хорошо"].pk).update(status=Product.Status.ARCHIVED)
        data = self._get()
        self.assertNotIn("Чужой", self._names(data))
        self.assertNotIn("Хорошо", self._names(data))
        self.assertEqual(data["summary"]["products_total"], 5)

    def test_requires_auth(self):
        self.assertEqual(APIClient().get(URL, secure=True).status_code, 401)


class CatalogVersionTests(PriceCheckTests):
    """02-catalog-change-marker.md: ETag/304 на price-check и GET catalog-version/."""

    VERSION_URL = "/api/main/products/catalog-version/"

    def _version(self):
        resp = self.api.get(self.VERSION_URL, secure=True)
        self.assertEqual(resp.status_code, 200)
        return resp.data["version"]

    def test_etag_304_when_unchanged(self):
        r1 = self.api.get(URL, {"filter": "loss"}, secure=True)
        etag = r1["ETag"]
        self.assertTrue(etag.startswith('"pc-'))
        r2 = self.api.get(URL, {"filter": "loss"}, secure=True, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(r2.content, b"")
        # другие параметры — другой ETag
        r3 = self.api.get(URL, {"filter": "noCost"}, secure=True, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r3.status_code, 200)

    def test_price_change_invalidates_etag(self):
        etag = self.api.get(URL, secure=True)["ETag"]
        p = self.p["Хорошо"]
        p.price = Decimal("120")
        p.save()
        r = self.api.get(URL, secure=True, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r["ETag"], etag)

    def test_version_changes_on_stock_update_and_delete(self):
        v1 = self._version()
        self.assertEqual(self._version(), v1)
        # остаток через queryset.update() — updated_at не меняется, версия меняется
        Product.objects.filter(pk=self.p["Хорошо"].pk).update(quantity=Decimal("3"))
        v2 = self._version()
        self.assertNotEqual(v2, v1)
        Product.objects.filter(pk=self.p["Минус"].pk).update(purchase_price=Decimal("11"))
        v3 = self._version()
        self.assertNotEqual(v3, v2)
        self.p["В ноль"].delete()
        resp = self.api.get(self.VERSION_URL, secure=True)
        self.assertNotEqual(resp.data["version"], v3)
        self.assertEqual(resp.data["products_total"], 6)  # 5 товаров + услуга

    def test_stock_moves_between_products_change_version(self):
        v1 = self._version()
        # +1 у одного и −1 у другого: простая сумма остатка не меняется, версия — меняется
        Product.objects.filter(pk=self.p["Хорошо"].pk).update(quantity=Decimal("5"))
        Product.objects.filter(pk=self.p["Убыток"].pk).update(quantity=Decimal("1"))
        self.assertNotEqual(self._version(), v1)


class DuplicateBarcodeTests(TestCase):
    """calculator-after-stress-test/03: дубли штрихкода."""

    def setUp(self):
        from apps.users.models import Branch

        self.owner = User.objects.create_user(email="dup-owner@test.com", password="x", role="owner")
        self.company = Company.objects.create(name="Dup Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.branch = Branch.objects.create(company=self.company, name="Филиал Ош")
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.mars = Product.objects.create(company=self.company, name="Батончик Mars 50г", barcode="4011100091108",
                                           quantity=Decimal("48"))
        # копия в филиале (как после перемещения между филиалами) — допустимо
        self.mars_branch = Product.objects.create(company=self.company, branch=self.branch, name="Батончик Mars 50г",
                                                  barcode="4011100091108", quantity=Decimal("0"),
                                                  status=Product.Status.ACCEPTED)

    def test_same_scope_duplicate_rejected_with_name_and_code(self):
        from django.core.exceptions import ValidationError as DjangoVE

        with self.assertRaises(DjangoVE) as ctx:
            Product.objects.create(company=self.company, name="Другой", barcode="4011100091108")
        text = str(ctx.exception)
        self.assertIn("Батончик Mars 50г", text)
        self.assertIn(f"код {self.mars.code}", text)

    def test_api_patch_to_existing_barcode_is_400(self):
        other = Product.objects.create(company=self.company, name="Сникерс", barcode="4000000000001")
        resp = self.api.patch(f"/api/main/products/{other.pk}/", {"barcode": "4011100091108"}, format="json", secure=True)
        self.assertEqual(resp.status_code, 400, getattr(resp, "data", None))
        self.assertIn("Батончик Mars 50г", str(resp.data))

    def test_alternate_barcode_of_other_product_is_400(self):
        other = Product.objects.create(company=self.company, name="Сникерс", barcode="4000000000001")
        resp = self.api.patch(
            f"/api/main/products/{other.pk}/",
            {"alternate_barcodes": [{"barcode": "4011100091108"}]}, format="json", secure=True,
        )
        self.assertEqual(resp.status_code, 400, getattr(resp, "data", None))
        self.assertIn("Батончик Mars 50г", str(resp.data))

    def test_duplicates_list(self):
        for url in ("/api/main/products/duplicate-barcodes/", "/api/main/products/barcode-duplicates/"):
            resp = self.api.get(url, secure=True)
            self.assertEqual(resp.status_code, 200)
            groups = {g["barcode"]: g for g in resp.data}
            g = groups["4011100091108"]
            self.assertFalse(g["same_scope"])
            self.assertEqual({p["id"] for p in g["products"]}, {str(self.mars.pk), str(self.mars_branch.pk)})
            row = {p["id"]: p for p in g["products"]}[str(self.mars_branch.pk)]
            self.assertEqual(row["branch_name"], "Филиал Ош")
            self.assertEqual(row["code"], self.mars_branch.code)
            self.assertEqual(row["status"], "accepted")
        resp = self.api.get("/api/main/products/duplicate-barcodes/?same_scope_only=true", secure=True)
        self.assertEqual(resp.data, [])

    def test_same_scope_legacy_duplicate_listed(self):
        legacy = Product.objects.create(company=self.company, name="Mars дубль", barcode="4011100099999")
        # старые данные: доп. штрихкод товара совпадает с основным у другого товара того же склада
        ProductAlternateBarcode.objects.bulk_create([
            ProductAlternateBarcode(product=legacy, company=self.company, barcode="4011100091108")
        ])
        resp = self.api.get("/api/main/products/duplicate-barcodes/?same_scope_only=true", secure=True)
        self.assertEqual(len(resp.data), 1)
        g = resp.data[0]
        self.assertTrue(g["same_scope"])
        alt = {p["id"]: p for p in g["products"]}[str(legacy.pk)]
        self.assertTrue(alt["is_alternate"])


class DataContractTests(TestCase):
    """calculator-after-stress-test/04: is_promo = stock; *_total в карточках продаж."""

    def setUp(self):
        self.owner = User.objects.create_user(email="dc-owner@test.com", password="x", role="owner")
        self.company = Company.objects.create(name="DC Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.p = Product.objects.create(company=self.company, name="Акционный", stock=True, price=10)

    def test_is_promo_alias(self):
        resp = self.api.get("/api/main/products/list/", secure=True)
        row = resp.data["results"][0]
        self.assertTrue(row["stock"])
        self.assertTrue(row["is_promo"])
        resp = self.api.patch(f"/api/main/products/{self.p.pk}/", {"is_promo": False}, format="json", secure=True)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        self.p.refresh_from_db()
        self.assertFalse(self.p.stock)
        self.assertFalse(resp.data["is_promo"])

    def test_sales_cards_total_aliases(self):
        resp = self.api.get("/api/main/analytics/market/", {"tab": "sales"}, secure=True)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        cards = resp.data["cards"]
        self.assertIn("margin_percent_total", cards)
        self.assertEqual(cards["margin_percent_total"], cards["margin_percent"])
        self.assertEqual(cards.get("gross_profit_total"), cards.get("gross_profit"))
