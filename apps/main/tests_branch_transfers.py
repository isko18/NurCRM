from decimal import Decimal
from datetime import timedelta
import threading

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.main.models import (
    BranchTransfer,
    BranchTransferItem,
    Product,
    ProductCategory,
)
from apps.construction.models import CashFlow
from apps.users.models import User, Company, Branch


class BranchTransferBaseTest(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Создаем владельца (owner)
        self.owner = User.objects.create_user(
            email="owner_user@test.com",
            password="testpassword",
            role="owner",
            can_view_branch=True,
            first_name="Nur",
            last_name="Test",
        )

        # Создаем компанию
        self.company = Company.objects.create(
            name="ОсОО Ромашка",
            owner=self.owner,
            inn="01234567890123",
            okpo="12345678",
            address="г. Бишкек, ул. Киевская, 1",
            phone="+996555123456",
        )
        self.owner.company = self.company
        self.owner.save()

        # Создаем филиалы
        self.branch_osh = Branch.objects.create(
            company=self.company,
            name="Филиал Ош",
            is_active=True,
        )
        self.branch_talas = Branch.objects.create(
            company=self.company,
            name="Филиал Талас",
            is_active=True,
        )
        self.inactive_branch = Branch.objects.create(
            company=self.company,
            name="Неактивный филиал",
            is_active=False,
        )

        # Чужая компания и филиал
        self.other_owner = User.objects.create_user(
            email="other_owner_user@test.com",
            password="testpassword",
            role="owner",
        )
        self.other_company = Company.objects.create(name="Другая компания", owner=self.other_owner)
        self.other_owner.company = self.other_company
        self.other_owner.save()
        self.other_branch = Branch.objects.create(
            company=self.other_company,
            name="Чужой филиал",
            is_active=True,
        )

        # Категория товаров
        self.category = ProductCategory.objects.create(
            company=self.company,
            name="Молочные продукты",
        )

        # Товар на главном складе (branch=None)
        self.main_product = Product.objects.create(
            company=self.company,
            branch=None,
            name="Молоко 1л",
            article="MLK-1",
            barcode="4870001234567",
            unit="шт",
            is_weight=False,
            quantity=Decimal("100.000"),
            purchase_price=Decimal("85.00"),
            price=Decimal("100.00"),
            category=self.category,
        )

        # Весовой товар на главном складе
        self.weight_product = Product.objects.create(
            company=self.company,
            branch=None,
            name="Сахар весовой",
            article="SUGAR-W",
            barcode="4870009876543",
            unit="кг",
            is_weight=True,
            quantity=Decimal("50.500"),
            purchase_price=Decimal("60.00"),
            price=Decimal("80.00"),
        )

        self.client.force_authenticate(user=self.owner)

    # 1. Главный → филиал: остатки сместились, номер ПЕР-000001, 201 с items и seller
    def test_01_transfer_main_to_branch(self):
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "date": timezone.localdate().isoformat(),
            "comment": "Пополнение витрины",
            "items": [
                {"product": str(self.main_product.id), "quantity": 12}
            ],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        data = res.data

        self.assertEqual(data["number"], "ПЕР-000001")
        self.assertEqual(data["status"], "completed")
        self.assertIsNone(data["from_branch"])
        self.assertEqual(data["to_branch"]["id"], str(self.branch_osh.id))
        self.assertEqual(data["to_branch"]["name"], "Филиал Ош")
        self.assertEqual(data["seller"]["name"], "ОсОО Ромашка")
        self.assertEqual(data["seller"]["inn"], "01234567890123")

        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["name"], "Молоко 1л")
        self.assertEqual(item["article"], "MLK-1")
        self.assertEqual(item["barcode"], "4870001234567")
        self.assertEqual(item["quantity"], "12.000")
        self.assertEqual(item["price"], "85.00")
        self.assertEqual(item["amount"], "1020.00")
        self.assertEqual(data["total_quantity"], "12.000")
        self.assertEqual(data["total_amount"], "1020.00")

        # Проверяем остатки
        self.main_product.refresh_from_db()
        self.assertEqual(self.main_product.quantity, Decimal("88.000"))

        osh_product = Product.objects.get(
            company=self.company,
            branch=self.branch_osh,
            barcode="4870001234567",
        )
        self.assertEqual(osh_product.quantity, Decimal("12.000"))
        self.assertEqual(osh_product.purchase_price, Decimal("85.00"))
        self.assertEqual(osh_product.price, Decimal("100.00"))

    # 2. Филиал → филиал и филиал → главный
    def test_02_transfer_branch_to_branch_and_branch_to_main(self):
        # Создаем товар в филиале Ош
        osh_prod = Product.objects.create(
            company=self.company,
            branch=self.branch_osh,
            name="Сок Яблочный",
            article="JUC-1",
            barcode="4870002222222",
            unit="шт",
            quantity=Decimal("30.000"),
            purchase_price=Decimal("50.00"),
            price=Decimal("70.00"),
        )

        # Филиал Ош -> Филиал Талас
        payload = {
            "from_branch": str(self.branch_osh.id),
            "to_branch": str(self.branch_talas.id),
            "items": [{"product": str(osh_prod.id), "quantity": 10}],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        osh_prod.refresh_from_db()
        self.assertEqual(osh_prod.quantity, Decimal("20.000"))

        talas_prod = Product.objects.get(
            company=self.company,
            branch=self.branch_talas,
            barcode="4870002222222",
        )
        self.assertEqual(talas_prod.quantity, Decimal("10.000"))

        # Филиал Талас -> Главный склад (to_branch=None)
        payload_to_main = {
            "from_branch": str(self.branch_talas.id),
            "to_branch": None,
            "items": [{"product": str(talas_prod.id), "quantity": 5}],
        }
        res2 = self.client.post("/main/branch-transfers/", payload_to_main, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED, res2.data)
        talas_prod.refresh_from_db()
        self.assertEqual(talas_prod.quantity, Decimal("5.000"))

        main_counterpart = Product.objects.get(
            company=self.company,
            branch=None,
            barcode="4870002222222",
        )
        self.assertEqual(main_counterpart.quantity, Decimal("5.000"))

    # 3. from == to (в т.ч. оба null) → 400
    def test_03_from_equals_to_returns_400(self):
        # Оба null
        res = self.client.post(
            "/main/branch-transfers/",
            {"from_branch": None, "to_branch": None, "items": [{"product": str(self.main_product.id), "quantity": 1}]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Оба один филиал
        res2 = self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": str(self.branch_osh.id),
                "to_branch": str(self.branch_osh.id),
                "items": [{"product": str(self.main_product.id), "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    # 4. quantity > остатка → 400, остатки не изменились, номер не сгорел
    def test_04_insufficient_quantity(self):
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "items": [{"product": str(self.main_product.id), "quantity": 150}],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("items", res.data)
        # Проверяем структуру ошибки: список по позициям
        self.assertTrue(isinstance(res.data["items"], list))
        self.assertIn("quantity", res.data["items"][0])
        self.assertIn("Недостаточно на складе", res.data["items"][0]["quantity"][0])

        self.main_product.refresh_from_db()
        self.assertEqual(self.main_product.quantity, Decimal("100.000"))
        # Документ не создался
        self.assertEqual(BranchTransfer.objects.count(), 0)

    # 5. Дубликат product в items → 400
    def test_05_duplicate_product_in_items(self):
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "items": [
                {"product": str(self.main_product.id), "quantity": 5},
                {"product": str(self.main_product.id), "quantity": 5},
            ],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("items", res.data)

    # 6. Неактивный получатель → 400; чужой филиал/товар → 400
    def test_06_inactive_or_foreign_branch(self):
        # Неактивный получатель
        res1 = self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": None,
                "to_branch": str(self.inactive_branch.id),
                "items": [{"product": str(self.main_product.id), "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)

        # Вывоз из неактивного разрешен (по ТЗ)
        inactive_prod = Product.objects.create(
            company=self.company,
            branch=self.inactive_branch,
            name="Товар из закрывающегося филиала",
            quantity=Decimal("10.000"),
            price=Decimal("10.00"),
        )
        res_evacuate = self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": str(self.inactive_branch.id),
                "to_branch": None,
                "items": [{"product": str(inactive_prod.id), "quantity": 5}],
            },
            format="json",
        )
        self.assertEqual(res_evacuate.status_code, status.HTTP_201_CREATED)

        # Чужой филиал
        res2 = self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": None,
                "to_branch": str(self.other_branch.id),
                "items": [{"product": str(self.main_product.id), "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    # 8. Отмена: остатки вернулись; повторная отмена → 400; отмена когда продали → 400
    def test_08_cancel_transfer(self):
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "items": [{"product": str(self.main_product.id), "quantity": 10}],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        transfer_id = res.data["id"]

        self.main_product.refresh_from_db()
        self.assertEqual(self.main_product.quantity, Decimal("90.000"))

        osh_product = Product.objects.get(
            company=self.company,
            branch=self.branch_osh,
            barcode=self.main_product.barcode,
        )
        self.assertEqual(osh_product.quantity, Decimal("10.000"))

        # Отмена
        cancel_res = self.client.post(
            f"/main/branch-transfers/{transfer_id}/cancel/",
            {"reason": "Ошибочное перемещение"},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel_res.data["status"], "cancelled")
        self.assertEqual(cancel_res.data["cancel_reason"], "Ошибочное перемещение")

        # Остатки вернулись
        self.main_product.refresh_from_db()
        osh_product.refresh_from_db()
        self.assertEqual(self.main_product.quantity, Decimal("100.000"))
        self.assertEqual(osh_product.quantity, Decimal("0.000"))

        # Повторная отмена → 400
        repeat_res = self.client.post(
            f"/main/branch-transfers/{transfer_id}/cancel/",
            {"reason": "Еще раз"},
            format="json",
        )
        self.assertEqual(repeat_res.status_code, status.HTTP_400_BAD_REQUEST)

        # Тест отмены, когда получатель уже продал часть товара
        # Делаем новое перемещение
        res2 = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        transfer_id2 = res2.data["id"]

        osh_product.refresh_from_db()
        # Имитируем продажу у получателя (остаток уменьшился до 3)
        osh_product.quantity = Decimal("3.000")
        osh_product.save()

        cancel_res2 = self.client.post(
            f"/main/branch-transfers/{transfer_id2}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(cancel_res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("уже частично продан/списан", cancel_res2.data["detail"])

    # 9. Перемещение не создаёт Cashflow, не меняет кассы/смены
    def test_09_no_cashflow_created(self):
        cf_count_before = CashFlow.objects.count()
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "items": [{"product": str(self.main_product.id), "quantity": 5}],
        }
        res = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        cf_count_after = CashFlow.objects.count()
        self.assertEqual(cf_count_before, cf_count_after)

    # 10. Список: фильтры branch, from_branch=main, status, date_*, search, пагинация
    def test_10_list_filters(self):
        # Создаем два перемещения
        self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": None,
                "to_branch": str(self.branch_osh.id),
                "comment": "Спецзаказ для Оша",
                "items": [{"product": str(self.main_product.id), "quantity": 1}],
            },
            format="json",
        )
        self.client.post(
            "/main/branch-transfers/",
            {
                "from_branch": None,
                "to_branch": str(self.branch_talas.id),
                "comment": "Поставка в Талас",
                "items": [{"product": str(self.main_product.id), "quantity": 2}],
            },
            format="json",
        )

        # 1. Список без фильтров
        res = self.client.get("/main/branch-transfers/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)

        # 2. Фильтр from_branch=main
        res_main = self.client.get("/main/branch-transfers/?from_branch=main")
        self.assertEqual(res_main.data["count"], 2)

        # 3. Фильтр to_branch=<osh>
        res_osh = self.client.get(f"/main/branch-transfers/?to_branch={self.branch_osh.id}")
        self.assertEqual(res_osh.data["count"], 1)
        self.assertEqual(res_osh.data["results"][0]["to_branch"]["name"], "Филиал Ош")

        # 4. Поиск
        res_search = self.client.get("/main/branch-transfers/?search=Талас")
        self.assertEqual(res_search.data["count"], 1)

    # 11. Схема B: двойник в получателе найден по barcode; повторное перемещение не плодит копии
    def test_11_scheme_b_duplicate_not_recreated(self):
        payload = {
            "from_branch": None,
            "to_branch": str(self.branch_osh.id),
            "items": [{"product": str(self.main_product.id), "quantity": 10}],
        }
        res1 = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        osh_count_1 = Product.objects.filter(
            company=self.company,
            branch=self.branch_osh,
            barcode=self.main_product.barcode,
        ).count()
        self.assertEqual(osh_count_1, 1)

        # Повторное перемещение того же товара
        res2 = self.client.post("/main/branch-transfers/", payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        osh_count_2 = Product.objects.filter(
            company=self.company,
            branch=self.branch_osh,
            barcode=self.main_product.barcode,
        ).count()
        self.assertEqual(osh_count_2, 1)

        osh_prod = Product.objects.get(
            company=self.company,
            branch=self.branch_osh,
            barcode=self.main_product.barcode,
        )
        self.assertEqual(osh_prod.quantity, Decimal("20.000"))

    # 12. Тест эндпоинта /main/products/list/?branch=main и ?branch=<uuid>
    def test_12_products_list_branch_filters(self):
        # Создаем товар в филиале Ош
        Product.objects.create(
            company=self.company,
            branch=self.branch_osh,
            name="Товар Ошский",
            article="OSH-1",
            barcode="4870005555555",
            quantity=Decimal("15.000"),
            price=Decimal("150.00"),
        )

        # 1. branch=main
        res_main = self.client.get("/main/products/list/?branch=main")
        self.assertEqual(res_main.status_code, status.HTTP_200_OK)
        ids_main = [p["id"] for p in res_main.data["results"]]
        self.assertIn(str(self.main_product.id), ids_main)

        # 2. branch=<osh>
        res_osh = self.client.get(f"/main/products/list/?branch={self.branch_osh.id}")
        self.assertEqual(res_osh.status_code, status.HTTP_200_OK)
        self.assertEqual(res_osh.data["count"], 1)
        self.assertEqual(res_osh.data["results"][0]["name"], "Товар Ошский")
        self.assertEqual(Decimal(str(res_osh.data["results"][0]["quantity"])), Decimal("15.000"))

        # 3. page_size=30
        res_ps = self.client.get("/main/products/list/?branch=main&page_size=1")
        self.assertEqual(res_ps.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_ps.data["results"]), 1)

        # 4. search по article
        res_art = self.client.get("/main/products/list/?search=MLK-1")
        self.assertEqual(res_art.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res_art.data["count"], 1)
        self.assertEqual(res_art.data["results"][0]["article"], "MLK-1")

    # 13. Удаление филиала: запрет если есть перемещения или остатки
    def test_13_branch_deletion_protection(self):
        # Сначала филиал Ош без перемещений и без остатков (создаем пустой)
        empty_branch = Branch.objects.create(company=self.company, name="Пустой филиал")
        res_del_ok = self.client.delete(f"/users/branches/{empty_branch.id}/")
        self.assertEqual(res_del_ok.status_code, status.HTTP_204_NO_CONTENT)

        # Филиал с остатками
        branch_with_stock = Branch.objects.create(company=self.company, name="С остатками")
        Product.objects.create(
            company=self.company,
            branch=branch_with_stock,
            name="Остаток",
            quantity=Decimal("5.000"),
            price=Decimal("10.00"),
        )
        res_del_fail = self.client.delete(f"/users/branches/{branch_with_stock.id}/")
        self.assertEqual(res_del_fail.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Нельзя удалить филиал", res_del_fail.data["detail"])
