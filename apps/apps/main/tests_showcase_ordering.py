from decimal import Decimal
import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from apps.main.models import Product, ProductCategory, ProductBrand
from apps.users.models import Company


from django.contrib.auth import get_user_model

User = get_user_model()


class PublicCompanyShowcaseOrderingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="showcase_owner@test.com",
            password="testpassword123",
        )
        self.company = Company.objects.create(
            name="Тестовый Магазин",
            slug="test-shop",
            owner=self.owner,
        )
        self.owner.company = self.company
        self.owner.save()
        self.category_phones = ProductCategory.objects.create(
            company=self.company,
            name="Телефоны",
        )
        self.category_laptops = ProductCategory.objects.create(
            company=self.company,
            name="Ноутбуки",
        )

        # Создаем товары с разными ценами, скидками и названиями
        # 1. Product A: price 1000, discount 0% -> final_price = 1000
        self.p_a = Product.objects.create(
            company=self.company,
            name="Апельсин телефон",
            price=Decimal("1000.00"),
            discount_percent=Decimal("0.00"),
            category=self.category_phones,
            article="ART-1",
        )
        # 2. Product B: price 2000, discount 50% -> final_price = 1000
        self.p_b = Product.objects.create(
            company=self.company,
            name="банан телефон",  # lowercase 'б' for case-insensitivity test
            price=Decimal("2000.00"),
            discount_percent=Decimal("50.00"),
            category=self.category_phones,
            article="ART-2",
        )
        # 3. Product C: price 500, discount 0% -> final_price = 500
        self.p_c = Product.objects.create(
            company=self.company,
            name="Вишня ноутбук",
            price=Decimal("500.00"),
            discount_percent=Decimal("0.00"),
            category=self.category_laptops,
            article="ART-3",
        )
        # 4. Product D: price 3000, discount 10% -> final_price = 2700
        self.p_d = Product.objects.create(
            company=self.company,
            name="Груша ноутбук",
            price=Decimal("3000.00"),
            discount_percent=Decimal("10.00"),
            category=self.category_laptops,
            article="ART-4",
        )
        # 5. Product E: price 100, discount 0% -> final_price = 100
        self.p_e = Product.objects.create(
            company=self.company,
            name="Дыня аксессуар",
            price=Decimal("100.00"),
            discount_percent=Decimal("0.00"),
            article="ART-5",
        )

        self.url = f"/api/main/public/companies/{self.company.slug}/showcase/"

    def test_default_ordering(self):
        """Без ordering возвращается порядок по умолчанию (-created_at, id)."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results", response.data)
        ids = [item["id"] for item in results]
        self.assertEqual(len(ids), 5)
        self.assertEqual(ids[0], str(self.p_e.id))

    def test_ordering_final_price_asc(self):
        """Сортировка по final_price (дешевле сначала): 100 (E) -> 500 (C) -> 1000 (A/B) -> 2700 (D)"""
        response = self.client.get(self.url, {"ordering": "final_price"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        self.assertEqual(ids[0], str(self.p_e.id))  # 100
        self.assertEqual(ids[1], str(self.p_c.id))  # 500
        self.assertIn(ids[2], [str(self.p_a.id), str(self.p_b.id)])  # 1000
        self.assertIn(ids[3], [str(self.p_a.id), str(self.p_b.id)])  # 1000
        self.assertEqual(ids[4], str(self.p_d.id))  # 2700

    def test_ordering_final_price_desc(self):
        """Сортировка по -final_price (дороже сначала): 2700 (D) -> 1000 (A/B) -> 500 (C) -> 100 (E)"""
        response = self.client.get(self.url, {"ordering": "-final_price"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        self.assertEqual(ids[0], str(self.p_d.id))  # 2700
        self.assertIn(ids[1], [str(self.p_a.id), str(self.p_b.id)])  # 1000
        self.assertIn(ids[2], [str(self.p_a.id), str(self.p_b.id)])  # 1000
        self.assertEqual(ids[3], str(self.p_c.id))  # 500
        self.assertEqual(ids[4], str(self.p_e.id))  # 100

    def test_ordering_name_asc(self):
        """Сортировка по name (А–Я, регистронезависимо): Апельсин -> банан -> Вишня -> Груша -> Дыня"""
        response = self.client.get(self.url, {"ordering": "name"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        expected_ids = [
            str(self.p_a.id),  # Апельсин
            str(self.p_b.id),  # банан
            str(self.p_c.id),  # Вишня
            str(self.p_d.id),  # Груша
            str(self.p_e.id),  # Дыня
        ]
        self.assertEqual(ids, expected_ids)

    def test_ordering_name_desc(self):
        """Сортировка по -name (Я–А, регистронезависимо): Дыня -> Груша -> Вишня -> банан -> Апельсин"""
        response = self.client.get(self.url, {"ordering": "-name"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        expected_ids = [
            str(self.p_e.id),  # Дыня
            str(self.p_d.id),  # Груша
            str(self.p_c.id),  # Вишня
            str(self.p_b.id),  # банан
            str(self.p_a.id),  # Апельсин
        ]
        self.assertEqual(ids, expected_ids)

    def test_ordering_discount_percent_asc(self):
        """Сортировка по discount_percent (меньше сначала): 0% / None -> 10% (D) -> 50% (B)"""
        response = self.client.get(self.url, {"ordering": "discount_percent"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        self.assertEqual(ids[3], str(self.p_d.id))  # 10%
        self.assertEqual(ids[4], str(self.p_b.id))  # 50%

    def test_ordering_discount_percent_desc(self):
        """Сортировка по -discount_percent (больше сначала): 50% (B) -> 10% (D) -> 0% / None"""
        response = self.client.get(self.url, {"ordering": "-discount_percent"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        ids = [item["id"] for item in results]
        self.assertEqual(ids[0], str(self.p_b.id))  # 50%
        self.assertEqual(ids[1], str(self.p_d.id))  # 10%

    def test_ordering_with_category_filter(self):
        """Комбинация category + ordering: только телефоны, отсортированные по final_price asc"""
        response = self.client.get(
            self.url,
            {"category": str(self.category_phones.id), "ordering": "final_price"},
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 2)
        ids = {item["id"] for item in results}
        self.assertEqual(ids, {str(self.p_a.id), str(self.p_b.id)})

    def test_ordering_with_search_filter(self):
        """Комбинация search + ordering: поиск 'ноутбук' + ordering=final_price"""
        response = self.client.get(
            self.url,
            {"search": "ноутбук", "ordering": "final_price"},
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], str(self.p_c.id))
        self.assertEqual(results[1]["id"], str(self.p_d.id))

    def test_pagination_preserves_ordering(self):
        """Пагинация page_size=2: page 1 и page 2 глобально упорядочены без пропусков/дублей."""
        res_p1 = self.client.get(self.url, {"ordering": "final_price", "page": 1, "page_size": 2})
        self.assertEqual(res_p1.status_code, 200)
        self.assertEqual(len(res_p1.data["results"]), 2)
        self.assertEqual(res_p1.data["count"], 5)

        res_p2 = self.client.get(self.url, {"ordering": "final_price", "page": 2, "page_size": 2})
        self.assertEqual(res_p2.status_code, 200)
        self.assertEqual(len(res_p2.data["results"]), 2)

        res_p3 = self.client.get(self.url, {"ordering": "final_price", "page": 3, "page_size": 2})
        self.assertEqual(res_p3.status_code, 200)
        self.assertEqual(len(res_p3.data["results"]), 1)

        all_ids = (
            [x["id"] for x in res_p1.data["results"]]
            + [x["id"] for x in res_p2.data["results"]]
            + [x["id"] for x in res_p3.data["results"]]
        )
        self.assertEqual(len(set(all_ids)), 5)
        self.assertEqual(all_ids[0], str(self.p_e.id))
        self.assertEqual(all_ids[1], str(self.p_c.id))
        self.assertEqual(all_ids[4], str(self.p_d.id))

    def test_invalid_ordering_returns_400(self):
        """Невалидный ordering параметр возвращает 400 Bad Request с подробным detail."""
        response = self.client.get(self.url, {"ordering": "invalid_column"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)
        self.assertIn("invalid_column", str(response.data["detail"]))

    def test_serializer_fields_present(self):
        """Проверка наличия всех ключевых полей в сериализаторе витрины (name, title, final_price, is_new и др.)."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        first_item = response.data["results"][0]
        for field in ["id", "name", "title", "price", "final_price", "discount_percent", "is_new"]:
            self.assertIn(field, first_item)
