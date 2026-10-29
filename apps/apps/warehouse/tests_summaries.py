from decimal import Decimal
import datetime
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.apps import apps
from rest_framework import status
from rest_framework.test import APIClient

from apps.warehouse import models
from apps.warehouse.services_summaries import build_summary_snapshot

User = get_user_model()


class WarehouseSummariesTests(TestCase):
    """Тесты для агрегации товаров в сводках продаж."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="User"
        )
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="Test Company", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Main Branch")

        # Настройка ролей: сделаем владельца админом/оунером
        self.owner.role = "owner"
        self.owner.company = self.company
        self.owner.branch = self.branch
        self.owner.save()

        # Склад
        self.wh = models.Warehouse.objects.create(
            name="Центральный Склад",
            company=self.company,
            branch=self.branch,
            status=models.Warehouse.Status.active
        )

        # Товары
        self.product_a = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Май Карона Изобилия 5 л",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("500.00"),
            price=Decimal("924.00"),
            quantity=Decimal("100.000"),
        )

        # Характеристики товара
        self.char = models.WarehouseProductCharasteristics.objects.create(
            company=self.company,
            branch=self.branch,
            product=self.product_a,
            factual_weight_kg=Decimal("1.500") # 1.5 кг за единицу
        )

        self.product_b = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh,
            name="Сок Любимый 1 л",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("50.00"),
            price=Decimal("100.00"),
            quantity=Decimal("100.000")
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_summary_products_aggregation_with_different_prices(self):
        """
        Проверяет, что товары с разными ценами агрегируются в разные строки
        и итоги сводки сходятся.
        """
        today = datetime.date.today()

        # Документ 1: Товар А по цене 924.00 (кол-во 10)
        doc1 = models.Document.objects.create(
            warehouse_from=self.wh,
            doc_type=models.Document.DocType.SALE,
            date=datetime.datetime.now(),
            status=models.Document.Status.POSTED,
            total=Decimal("9240.00")
        )
        models.DocumentItem.objects.create(
            document=doc1,
            product=self.product_a,
            qty=Decimal("10"),
            price=Decimal("924.00"),
            line_total=Decimal("9240.00")
        )

        # Документ 2: Товар А по цене 860.00 (кол-во 5) + Товар B по цене 100.00 (кол-во 2)
        doc2 = models.Document.objects.create(
            warehouse_from=self.wh,
            doc_type=models.Document.DocType.SALE,
            date=datetime.datetime.now(),
            status=models.Document.Status.POSTED,
            total=Decimal("4500.00")
        )
        models.DocumentItem.objects.create(
            document=doc2,
            product=self.product_a,
            qty=Decimal("5"),
            price=Decimal("860.00"),
            line_total=Decimal("4300.00")
        )
        models.DocumentItem.objects.create(
            document=doc2,
            product=self.product_b,
            qty=Decimal("2"),
            price=Decimal("100.00"),
            line_total=Decimal("200.00")
        )

        # Создаем сводку
        summary = models.WarehouseSalesSummary.objects.create(
            company=self.company,
            branch=self.branch,
            date=today,
            name="Тестовая сводка"
        )
        summary.warehouses.add(self.wh)

        # Строим снапшот
        build_summary_snapshot(summary)

        # Проверяем документы
        self.assertEqual(summary.documents.count(), 2)

        # Проверяем продукты: должно быть 3 строки (Товар А по 924.00, Товар А по 860.00, Товар B по 100.00)
        products = list(summary.products.all())
        self.assertEqual(len(products), 3)

        # Проверяем конкретные агрегированные строки
        row_a_924 = summary.products.filter(name="Май Карона Изобилия 5 л", price=Decimal("924.00")).first()
        self.assertIsNotNone(row_a_924)
        self.assertEqual(row_a_924.quantity, Decimal("10.000"))
        self.assertEqual(row_a_924.amount, Decimal("9240.00"))
        # Вес = 10 * 1.5 = 15
        self.assertEqual(row_a_924.weight, Decimal("15.000"))

        row_a_860 = summary.products.filter(name="Май Карона Изобилия 5 л", price=Decimal("860.00")).first()
        self.assertIsNotNone(row_a_860)
        self.assertEqual(row_a_860.quantity, Decimal("5.000"))
        self.assertEqual(row_a_860.amount, Decimal("4300.00"))
        # Вес = 5 * 1.5 = 7.5
        self.assertEqual(row_a_860.weight, Decimal("7.500"))

        row_b_100 = summary.products.filter(name="Сок Любимый 1 л", price=Decimal("100.00")).first()
        self.assertIsNotNone(row_b_100)
        self.assertEqual(row_b_100.quantity, Decimal("2.000"))
        self.assertEqual(row_b_100.amount, Decimal("200.00"))
        self.assertEqual(row_b_100.weight, Decimal("0.000"))

        # Проверяем итоги:
        # total_quantity = 10 + 5 + 2 = 17
        # total_weight = 15 + 7.5 + 0 = 22.5
        # total_amount = 9240 + 4300 + 200 = 13740
        self.assertEqual(summary.total_quantity, Decimal("17.000"))
        self.assertEqual(summary.total_weight, Decimal("22.500"))
        self.assertEqual(summary.total_amount, Decimal("13740.00"))
        self.assertEqual(summary.products_count, 3)
        self.assertEqual(summary.documents_count, 2)

    def test_summary_regenerate_endpoint(self):
        """Проверяет эндпоинт регенерации сводки."""
        today = datetime.date.today()
        summary = models.WarehouseSalesSummary.objects.create(
            company=self.company,
            branch=self.branch,
            date=today,
            name="Тестовая сводка"
        )
        summary.warehouses.add(self.wh)

        url = reverse("warehouse-summary-regenerate", kwargs={"pk": str(summary.id)})
        if not url.endswith("/"):
            url += "/"
        response = self.client.post(url, secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        summary.refresh_from_db()
        self.assertEqual(summary.products_count, 0) # так как нет документов

    def test_summary_excludes_sale_requests_and_drafts(self):
        """
        Проверяет, что документы со статусом SALE_REQUEST (Заявка на продажу)
        и DRAFT (Черновик) не отображаются в сводке.
        """
        today = datetime.date.today()

        # Документ SALE_REQUEST
        doc_request = models.Document.objects.create(
            warehouse_from=self.wh,
            doc_type=models.Document.DocType.SALE,
            date=datetime.datetime.now(),
            status=models.Document.Status.SALE_REQUEST,
            total=Decimal("1000.00")
        )
        models.DocumentItem.objects.create(
            document=doc_request,
            product=self.product_a,
            qty=Decimal("1"),
            price=Decimal("1000.00"),
            line_total=Decimal("1000.00")
        )

        # Документ DRAFT
        doc_draft = models.Document.objects.create(
            warehouse_from=self.wh,
            doc_type=models.Document.DocType.SALE,
            date=datetime.datetime.now(),
            status=models.Document.Status.DRAFT,
            total=Decimal("500.00")
        )
        models.DocumentItem.objects.create(
            document=doc_draft,
            product=self.product_b,
            qty=Decimal("1"),
            price=Decimal("500.00"),
            line_total=Decimal("500.00")
        )

        # Документ POSTED (должен попасть)
        doc_posted = models.Document.objects.create(
            warehouse_from=self.wh,
            doc_type=models.Document.DocType.SALE,
            date=datetime.datetime.now(),
            status=models.Document.Status.POSTED,
            total=Decimal("2000.00")
        )
        models.DocumentItem.objects.create(
            document=doc_posted,
            product=self.product_a,
            qty=Decimal("2"),
            price=Decimal("1000.00"),
            line_total=Decimal("2000.00")
        )

        summary = models.WarehouseSalesSummary.objects.create(
            company=self.company,
            branch=self.branch,
            date=today,
            name="Сводка со статусами"
        )
        summary.warehouses.add(self.wh)

        build_summary_snapshot(summary)

        # В сводку должен попасть только POSTED документ (1 документ, а не 3)
        self.assertEqual(summary.documents.count(), 1)
        self.assertEqual(summary.documents.first().document_id, doc_posted.id)
        self.assertEqual(summary.total_amount, Decimal("2000.00"))

