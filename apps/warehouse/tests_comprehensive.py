from decimal import Decimal
from datetime import datetime

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.warehouse import models
from apps.warehouse import services
from django.apps import apps
from apps.users.models import Roles


User = get_user_model()


class WarehouseComprehensiveTests(TestCase):
    """Полный набор тестов для Warehouse функционала."""
    
    def setUp(self):
        """Создание тестовых данных."""
        # Пользователь и компания
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
            first_name="Test",
            last_name="User"
        )
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="Test Company", owner=self.user)
        self.branch = Branch.objects.create(company=self.company, name="Main Branch")
        
        # Склады
        self.wh1 = models.Warehouse.objects.create(
            name="Склад 1",
            company=self.company,
            branch=self.branch,
            location="Локация 1",
            status=models.Warehouse.Status.active
        )
        self.wh2 = models.Warehouse.objects.create(
            name="Склад 2",
            company=self.company,
            branch=self.branch,
            location="Локация 2",
            status=models.Warehouse.Status.active
        )
        
        # Категория и бренд
        self.category = models.WarehouseProductCategory.objects.create(
            name="Категория 1",
            company=self.company,
            branch=self.branch
        )
        self.brand = models.WarehouseProductBrand.objects.create(
            name="Бренд 1",
            company=self.company,
            branch=self.branch
        )
        
        # Товары
        self.prod1 = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh1,
            category=self.category,
            brand=self.brand,
            name="Товар 1",
            code="P001",
            barcode="1234567890123",
            unit="шт",
            is_weight=False,
            purchase_price=Decimal("100.00"),
            price=Decimal("150.00"),
            quantity=Decimal("0.000")
        )
        
        self.prod2 = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh1,
            category=self.category,
            name="Товар 2 (весовой)",
            code="P002",
            barcode="1234567890124",
            unit="кг",
            is_weight=True,
            plu=1001,
            purchase_price=Decimal("50.00"),
            price=Decimal("75.00"),
            quantity=Decimal("0.000")
        )
        
        # Товар на втором складе
        self.prod3 = models.WarehouseProduct.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.wh2,
            category=self.category,
            name="Товар 3",
            code="P003",
            unit="шт",
            purchase_price=Decimal("200.00"),
            price=Decimal("300.00"),
            quantity=Decimal("0.000")
        )
        
        # Контрагенты
        self.client = models.Counterparty.objects.create(
            name="Клиент 1",
            phone="+996700000010",
            type=models.Counterparty.Type.CLIENT,
        )
        self.supplier = models.Counterparty.objects.create(
            name="Поставщик 1",
            phone="+996700000011",
            type=models.Counterparty.Type.SUPPLIER,
        )
        
        # Очистка кэша перед каждым тестом
        cache.clear()
    
    # ==================== ТЕСТЫ ПРОДАЖИ (SALE) ====================
    
    def test_sale_decreases_stock_balance(self):
        """Тест: продажа уменьшает остаток на складе."""
        # Начальный остаток
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        # Создаем документ продажи
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("30.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("0.00")
        )
        
        # Проводим документ
        services.post_document(doc)
        
        # Проверяем остаток
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("70.000"))
        
        # Проверяем создание StockMove
        moves = models.StockMove.objects.filter(document=doc)
        self.assertEqual(moves.count(), 1)
        move = moves.first()
        self.assertEqual(move.qty_delta, Decimal("-30.000"))
        self.assertEqual(move.warehouse, self.wh1)
        self.assertEqual(move.product, self.prod1)
    
    def test_sale_with_discount_calculates_total(self):
        """Тест: продажа со скидкой правильно рассчитывает итог."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        item = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("10.00")
        )
        
        # Проверяем line_total
        expected_total = Decimal("150.00") * Decimal("10.000") * (Decimal("1") - Decimal("0.10"))
        self.assertEqual(item.line_total, expected_total.quantize(Decimal("0.01")))
        
        services.post_document(doc)
        
        # Проверяем total документа
        doc.refresh_from_db()
        self.assertEqual(doc.total, expected_total.quantize(Decimal("0.01")))
    
    def test_sale_prevents_negative_stock(self):
        """Тест: продажа не позволяет создать отрицательный остаток."""
        # Начальный остаток меньше требуемого
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("5.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00")
        )
        
        # Пытаемся провести - должна быть ошибка
        with self.assertRaises(ValueError) as cm:
            services.post_document(doc)
        
        self.assertIn("Недостаточно товара", str(cm.exception))
        
        # Остаток не должен измениться
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("5.000"))
    
    def test_sale_unpost_restores_balance(self):
        """Тест: отмена продажи восстанавливает остаток."""
        initial_qty = Decimal("100.000")
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=initial_qty
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("25.000"),
            price=Decimal("150.00")
        )
        
        services.post_document(doc)
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("75.000"))
        
        # Отменяем проведение
        services.unpost_document(doc)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, initial_qty)
        
        # Проверяем, что moves удалены
        self.assertEqual(models.StockMove.objects.filter(document=doc).count(), 0)
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
    
    # ==================== ТЕСТЫ ПРИХОДА (PURCHASE) ====================
    
    def test_purchase_increases_stock_balance(self):
        """Тест: приход увеличивает остаток на складе."""
        initial_qty = Decimal("50.000")
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=initial_qty
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.PURCHASE,
            warehouse_from=self.wh1,
            counterparty=self.supplier
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("30.000"),
            price=Decimal("100.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("80.000"))
        
        move = models.StockMove.objects.get(document=doc)
        self.assertEqual(move.qty_delta, Decimal("30.000"))
    
    def test_purchase_creates_balance_if_not_exists(self):
        """Тест: приход создает StockBalance, если его не было."""
        # Убеждаемся, что баланса нет
        self.assertFalse(
            models.StockBalance.objects.filter(warehouse=self.wh1, product=self.prod1).exists()
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.PURCHASE,
            warehouse_from=self.wh1,
            counterparty=self.supplier
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("20.000"),
            price=Decimal("100.00")
        )
        
        services.post_document(doc)
        
        # Баланс должен быть создан
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("20.000"))
    
    # ==================== ТЕСТЫ ПЕРЕМЕЩЕНИЯ (TRANSFER) ====================
    
    def test_transfer_creates_two_moves(self):
        """Тест: перемещение создает два StockMove."""
        # Начальный остаток на складе-источнике
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=self.wh2
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("40.000"),
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        # Проверяем два moves
        moves = models.StockMove.objects.filter(document=doc).order_by('qty_delta')
        self.assertEqual(moves.count(), 2)
        
        move_from = moves.filter(warehouse=self.wh1).first()
        move_to = moves.filter(warehouse=self.wh2).first()
        
        self.assertEqual(move_from.qty_delta, Decimal("-40.000"))
        self.assertEqual(move_to.qty_delta, Decimal("40.000"))
        
        # Проверяем остатки
        bal1 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        dest_prod = models.WarehouseProduct.objects.get(warehouse=self.wh2, barcode=self.prod1.barcode)
        bal2 = models.StockBalance.objects.get(warehouse=self.wh2, product=dest_prod)
        self.assertEqual(bal1.qty, Decimal("60.000"))
        self.assertEqual(bal2.qty, Decimal("40.000"))
    
    def test_transfer_prevents_negative_stock(self):
        """Тест: перемещение не позволяет создать отрицательный остаток."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("10.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=self.wh2
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("50.000"),
            price=Decimal("0.00")
        )
        
        with self.assertRaises(ValueError) as cm:
            services.post_document(doc)
        
        self.assertIn("Недостаточно товара", str(cm.exception))
    
    def test_transfer_unpost_reverses_both_balances(self):
        """Тест: отмена перемещения восстанавливает оба остатка."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=self.wh2
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("30.000"),
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        bal1 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        dest_prod = models.WarehouseProduct.objects.get(warehouse=self.wh2, barcode=self.prod1.barcode)
        bal2 = models.StockBalance.objects.get(warehouse=self.wh2, product=dest_prod)
        self.assertEqual(bal1.qty, Decimal("70.000"))
        self.assertEqual(bal2.qty, Decimal("30.000"))
        
        # Отменяем
        services.unpost_document(doc)
        
        bal1.refresh_from_db()
        bal2.refresh_from_db()
        self.assertEqual(bal1.qty, Decimal("100.000"))
        self.assertEqual(bal2.qty, Decimal("0.000"))
    
    # ==================== ТЕСТЫ ИНВЕНТАРИЗАЦИИ (INVENTORY) ====================
    
    def test_inventory_sets_absolute_quantity(self):
        """Тест: инвентаризация устанавливает абсолютное количество."""
        # Текущий остаток
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.INVENTORY,
            warehouse_from=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("75.000"),  # Фактическое количество
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("75.000"))
        
        move = models.StockMove.objects.get(document=doc)
        self.assertEqual(move.qty_delta, Decimal("-25.000"))  # Разница
    
    def test_inventory_creates_balance_if_not_exists(self):
        """Тест: инвентаризация создает баланс, если его не было."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.INVENTORY,
            warehouse_from=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("50.000"),
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("50.000"))
    
    def test_inventory_skips_zero_delta(self):
        """Тест: инвентаризация пропускает items с нулевой разницей."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.INVENTORY,
            warehouse_from=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("100.000"),  # Совпадает с текущим
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        # Не должно быть StockMove для нулевой разницы
        self.assertEqual(models.StockMove.objects.filter(document=doc).count(), 0)
    
    # ==================== ТЕСТЫ ВОЗВРАТА ПРОДАЖИ (SALE_RETURN) ====================
    
    def test_sale_return_increases_stock(self):
        """Тест: возврат продажи увеличивает остаток."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("50.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE_RETURN,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("60.000"))
        
        move = models.StockMove.objects.get(document=doc)
        self.assertEqual(move.qty_delta, Decimal("10.000"))
    
    # ==================== ТЕСТЫ ВОЗВРАТА ПОКУПКИ (PURCHASE_RETURN) ====================
    
    def test_purchase_return_decreases_stock(self):
        """Тест: возврат покупки уменьшает остаток."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.PURCHASE_RETURN,
            warehouse_from=self.wh1,
            counterparty=self.supplier
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("20.000"),
            price=Decimal("100.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("80.000"))
        
        move = models.StockMove.objects.get(document=doc)
        self.assertEqual(move.qty_delta, Decimal("-20.000"))
    
    # ==================== ТЕСТЫ ПРИХОДА (RECEIPT) ====================
    
    def test_receipt_increases_stock(self):
        """Тест: приход увеличивает остаток."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("50.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.RECEIPT,
            warehouse_from=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("25.000"),
            price=Decimal("100.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("75.000"))
    
    # ==================== ТЕСТЫ СПИСАНИЯ (WRITE_OFF) ====================
    
    def test_write_off_decreases_stock(self):
        """Тест: списание уменьшает остаток."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.WRITE_OFF,
            warehouse_from=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("15.000"),
            price=Decimal("0.00")
        )
        
        services.post_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("85.000"))
    
    # ==================== ТЕСТЫ ВАЛИДАЦИИ ====================
    
    def test_document_requires_warehouse_from(self):
        """Тест: документ требует warehouse_from для большинства операций."""
        doc = models.Document(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=None,
            counterparty=self.client
        )
        
        with self.assertRaises(ValidationError):
            doc.clean()
    
    def test_document_requires_counterparty_for_sale(self):
        """Тест: документ продажи требует counterparty."""
        doc = models.Document(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=None
        )
        
        with self.assertRaises(ValidationError):
            doc.clean()
    
    def test_transfer_requires_both_warehouses(self):
        """Тест: перемещение требует оба склада."""
        doc = models.Document(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=None
        )
        
        with self.assertRaises(ValidationError):
            doc.clean()
    
    def test_transfer_prevents_same_warehouse(self):
        """Тест: перемещение не позволяет одинаковые склады."""
        doc = models.Document(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=self.wh1
        )
        
        with self.assertRaises(ValidationError):
            doc.clean()
    
    def test_document_item_validates_product_warehouse(self):
        """Тест: item проверяет соответствие товара складу."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        # Пытаемся добавить товар с другого склада
        item = models.DocumentItem(
            document=doc,
            product=self.prod3,  # Товар на wh2
            qty=Decimal("10.000"),
            price=Decimal("300.00")
        )
        
        with self.assertRaises(ValidationError):
            item.clean()
    
    def test_document_item_validates_quantity_positive(self):
        """Тест: item требует положительное количество."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        item = models.DocumentItem(
            document=doc,
            product=self.prod1,
            qty=Decimal("0.000"),
            price=Decimal("150.00")
        )
        
        with self.assertRaises(ValidationError):
            item.clean()
    
    def test_document_item_validates_discount_range(self):
        """Тест: item проверяет диапазон скидки."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        item = models.DocumentItem(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("150.00")  # Больше 100%
        )
        
        with self.assertRaises(ValidationError):
            item.clean()
    
    def test_document_item_validates_integer_qty_for_pieces(self):
        """Тест: item требует целое количество для штучных товаров."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        item = models.DocumentItem(
            document=doc,
            product=self.prod1,  # Штучный товар
            qty=Decimal("10.500"),  # Не целое
            price=Decimal("150.00")
        )
        
        with self.assertRaises(ValidationError):
            item.clean()
    
    # ==================== ТЕСТЫ ПЕРЕСЧЕТА ИТОГОВ ====================
    
    def test_recalc_document_totals(self):
        """Тест: пересчет итогов документа."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        item1 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("0.00")
        )
        
        item2 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("5.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("10.00")
        )
        
        services.recalc_document_totals(doc)

        doc.refresh_from_db()
        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(item1.line_total, (Decimal("150.00") * Decimal("10.000")).quantize(Decimal("0.01")))
        self.assertEqual(item2.line_total, (Decimal("150.00") * Decimal("5.000") * Decimal("0.90")).quantize(Decimal("0.01")))
        expected_total = item1.line_total + item2.line_total
        self.assertEqual(doc.total, expected_total)

    def test_recalc_document_totals_document_discount_as_line_fallback(self):
        """Общая скидка документа подставляется в строку, если у позиции discount_percent = 0."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client,
            discount_percent=Decimal("10.00"),
        )
        item = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("3.000"),
            price=Decimal("1450.02"),
            discount_percent=Decimal("0.00"),
        )
        services.recalc_document_totals(doc)
        item.refresh_from_db()
        doc.refresh_from_db()
        expected_line = (Decimal("3.000") * Decimal("1450.02") * Decimal("0.90")).quantize(Decimal("0.01"))
        self.assertEqual(item.line_total, expected_line)
        self.assertEqual(doc.total, expected_line)

    def test_recalc_document_totals_line_discount_overrides_document(self):
        """Индивидуальная скидка строки имеет приоритет над общей скидкой документа."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client,
            discount_percent=Decimal("10.00"),
        )
        item = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("3.000"),
            price=Decimal("1450.02"),
            discount_percent=Decimal("5.00"),
        )
        services.recalc_document_totals(doc)
        item.refresh_from_db()
        doc.refresh_from_db()
        expected_line = (Decimal("3.000") * Decimal("1450.02") * Decimal("0.95")).quantize(Decimal("0.01"))
        self.assertEqual(item.line_total, expected_line)
        self.assertEqual(doc.total, expected_line)

    # ==================== ТЕСТЫ НОМЕРАЦИИ ДОКУМЕНТОВ ====================
    
    def test_document_number_generation(self):
        """Тест: генерация номера документа."""
        # Сначала создаем остаток для продажи
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00")
        )
        
        # Номера еще нет
        self.assertIsNone(doc.number)
        
        services.post_document(doc)
        
        # Номер должен быть сгенерирован
        doc.refresh_from_db()
        self.assertIsNotNone(doc.number)
        self.assertTrue(doc.number.startswith("SALE-"))
    
    def test_document_number_sequential(self):
        """Тест: номера документов последовательные."""
        # Сначала создаем остаток для продажи
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc1 = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc1,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00")
        )
        
        doc2 = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc2,
            product=self.prod1,
            qty=Decimal("5.000"),
            price=Decimal("150.00")
        )
        
        services.post_document(doc1)
        services.post_document(doc2)
        
        doc1.refresh_from_db()
        doc2.refresh_from_db()
        
        # Номера должны быть разными
        self.assertNotEqual(doc1.number, doc2.number)
        # Последние 4 цифры должны отличаться на 1
        seq1 = int(doc1.number.split("-")[-1])
        seq2 = int(doc2.number.split("-")[-1])
        self.assertEqual(seq2, seq1 + 1)
    
    # ==================== ТЕСТЫ ОТРИЦАТЕЛЬНЫХ ОСТАТКОВ ====================
    
    def test_allow_negative_stock_setting(self):
        """Тест: настройка ALLOW_NEGATIVE_STOCK."""
        old_setting = getattr(settings, "ALLOW_NEGATIVE_STOCK", False)
        
        try:
            # Включаем отрицательные остатки
            settings.ALLOW_NEGATIVE_STOCK = True
            
            models.StockBalance.objects.create(
                warehouse=self.wh1,
                product=self.prod1,
                qty=Decimal("5.000")
            )
            
            doc = models.Document.objects.create(
                doc_type=models.Document.DocType.SALE,
                warehouse_from=self.wh1,
                counterparty=self.client
            )
            models.DocumentItem.objects.create(
                document=doc,
                product=self.prod1,
                qty=Decimal("10.000"),
                price=Decimal("150.00")
            )
            
            # Должно пройти без ошибки
            services.post_document(doc)
            
            bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
            self.assertEqual(bal.qty, Decimal("-5.000"))
            
        finally:
            settings.ALLOW_NEGATIVE_STOCK = old_setting
    
    # ==================== ТЕСТЫ МНОЖЕСТВЕННЫХ ITEMS ====================
    
    def test_document_with_multiple_items(self):
        """Тест: документ с несколькими товарами."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod2,
            qty=Decimal("50.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        item1 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("20.000"),
            price=Decimal("150.00")
        )
        
        item2 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod2,
            qty=Decimal("10.000"),
            price=Decimal("75.00")
        )
        
        services.post_document(doc)
        
        # Проверяем остатки
        bal1 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        bal2 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod2)
        self.assertEqual(bal1.qty, Decimal("80.000"))
        self.assertEqual(bal2.qty, Decimal("40.000"))
        
        # Проверяем moves
        moves = models.StockMove.objects.filter(document=doc)
        self.assertEqual(moves.count(), 2)
        
        # Проверяем total
        doc.refresh_from_db()
        expected_total = (
            Decimal("150.00") * Decimal("20.000") +
            Decimal("75.00") * Decimal("10.000")
        )
        self.assertEqual(doc.total, expected_total)
    
    # ==================== ТЕСТЫ ОТМЕНЫ ПРОВЕДЕНИЯ ====================
    
    def test_unpost_creates_balance_if_missing(self):
        """Тест: отмена проведения создает баланс, если его не было."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.PURCHASE,
            warehouse_from=self.wh1,
            counterparty=self.supplier,
            status=models.Document.Status.POSTED
        )
        
        # Создаем move напрямую (симулируем проведенный документ)
        move = models.StockMove.objects.create(
            document=doc,
            warehouse=self.wh1,
            product=self.prod1,
            qty_delta=Decimal("30.000"),
            move_kind=models.StockMove.MoveKind.RECEIPT,
        )
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("30.000")
        )
        
        # Удаляем баланс (симулируем edge case)
        models.StockBalance.objects.filter(warehouse=self.wh1, product=self.prod1).delete()
        
        # Отменяем - баланс должен быть создан и отменен move должен быть применен
        services.unpost_document(doc)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        # Документ добавил +30, отмена должна вычесть -30, поэтому баланс будет -30
        self.assertEqual(bal.qty, Decimal("-30.000"))
    
    def test_cannot_post_already_posted_document(self):
        """Тест: нельзя провести уже проведенный документ."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client,
            status=models.Document.Status.POSTED
        )
        
        with self.assertRaises(ValueError) as cm:
            services.post_document(doc)
        
        self.assertIn("already posted", str(cm.exception).lower())
    
    def test_cannot_unpost_draft_document(self):
        """Тест: нельзя отменить проведение черновика."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client,
            status=models.Document.Status.DRAFT
        )
        
        with self.assertRaises(ValueError) as cm:
            services.unpost_document(doc)
        
        self.assertIn("not posted", str(cm.exception).lower())
    
    def test_cannot_post_empty_document(self):
        """Тест: нельзя провести пустой документ."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        with self.assertRaises(ValueError) as cm:
            services.post_document(doc)
        
        self.assertIn("empty", str(cm.exception).lower())
    
    # ==================== ТЕСТЫ ВЕСОВЫХ ТОВАРОВ ====================
    
    def test_weight_product_allows_decimal_qty(self):
        """Тест: весовой товар позволяет дробное количество."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        
        # Весовой товар - дробное количество должно быть OK
        item = models.DocumentItem(
            document=doc,
            product=self.prod2,  # Весовой товар
            qty=Decimal("1.500"),  # Дробное
            price=Decimal("75.00")
        )
        
        # Не должно быть ошибки валидации
        try:
            item.clean()
        except ValidationError:
            self.fail("Weight product should allow decimal quantity")
    
    # ==================== ТЕСТЫ КЭШИРОВАНИЯ ====================
    
    def test_product_barcode_caching(self):
        """Тест: кэширование поиска товара по штрих-коду."""
        from apps.warehouse.views_documents import ProductListCreateView
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        
        # Очищаем кэш
        cache.clear()
        
        # Первый запрос - должен идти в БД
        cache_key = f"warehouse_product_barcode:{self.company.id}:{self.prod1.barcode}"
        self.assertIsNone(cache.get(cache_key))
        
        # Симулируем поиск (в реальности это делается через API)
        # Здесь просто проверяем, что кэш работает
        cache.set(cache_key, self.prod1.id, 300)
        cached_id = cache.get(cache_key)
        self.assertEqual(cached_id, self.prod1.id)
        
        # Инвалидация при изменении barcode
        old_barcode = self.prod1.barcode
        self.prod1.barcode = "9999999999999"
        self.prod1.save()
        
        # Старый ключ должен быть удален
        self.assertIsNone(cache.get(f"warehouse_product_barcode:{self.company.id}:{old_barcode}"))
    
    # ==================== ТЕСТЫ ОПТИМИЗАЦИИ ЗАПРОСОВ ====================
    
    def test_post_document_uses_select_related(self):
        """Тест: post_document использует select_related для оптимизации."""
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("10.000"),
            price=Decimal("150.00")
        )
        
        # Проверяем, что запросы оптимизированы (нет N+1)
        # Используем разумное количество запросов (с учетом select_related/prefetch_related)
        with self.assertNumQueries(20):  # Разумное количество запросов
            services.post_document(doc)
    
    # ==================== ТЕСТЫ КОМПЛЕКСНЫХ СЦЕНАРИЕВ ====================
    
    def test_complex_sale_workflow(self):
        """Тест: комплексный сценарий продажи."""
        # 1. Начальные остатки
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod2,
            qty=Decimal("50.000")
        )
        
        # 2. Создаем документ продажи
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client,
            comment="Тестовая продажа"
        )
        
        item1 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod1,
            qty=Decimal("20.000"),
            price=Decimal("150.00"),
            discount_percent=Decimal("5.00")
        )
        
        item2 = models.DocumentItem.objects.create(
            document=doc,
            product=self.prod2,
            qty=Decimal("10.000"),
            price=Decimal("75.00"),
            discount_percent=Decimal("0.00")
        )
        
        # 3. Проводим документ
        services.post_document(doc)
        
        # 4. Проверяем результаты
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        self.assertIsNotNone(doc.number)
        
        bal1 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        bal2 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod2)
        self.assertEqual(bal1.qty, Decimal("80.000"))
        self.assertEqual(bal2.qty, Decimal("40.000"))
        
        moves = models.StockMove.objects.filter(document=doc)
        self.assertEqual(moves.count(), 2)
        
        # 5. Отменяем проведение
        services.unpost_document(doc)
        
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.DRAFT)
        
        bal1.refresh_from_db()
        bal2.refresh_from_db()
        self.assertEqual(bal1.qty, Decimal("100.000"))
        self.assertEqual(bal2.qty, Decimal("50.000"))
        
        self.assertEqual(models.StockMove.objects.filter(document=doc).count(), 0)
    
    def test_multiple_transfers_chain(self):
        """Тест: цепочка перемещений между складами."""
        # Начальный остаток на складе 1
        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("100.000")
        )
        
        # Перемещение 1: wh1 -> wh2
        doc1 = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh1,
            warehouse_to=self.wh2
        )
        models.DocumentItem.objects.create(
            document=doc1,
            product=self.prod1,
            qty=Decimal("30.000"),
            price=Decimal("0.00")
        )
        services.post_document(doc1)
        
        bal1 = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        dest_prod = models.WarehouseProduct.objects.get(warehouse=self.wh2, barcode=self.prod1.barcode)
        bal2 = models.StockBalance.objects.get(warehouse=self.wh2, product=dest_prod)
        self.assertEqual(bal1.qty, Decimal("70.000"))
        self.assertEqual(bal2.qty, Decimal("30.000"))
        
        # Перемещение 2: wh2 -> wh1 (обратно часть)
        doc2 = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=self.wh2,
            warehouse_to=self.wh1
        )
        models.DocumentItem.objects.create(
            document=doc2,
            product=dest_prod,
            qty=Decimal("10.000"),
            price=Decimal("0.00")
        )
        services.post_document(doc2)
        
        bal1.refresh_from_db()
        bal2.refresh_from_db()
        self.assertEqual(bal1.qty, Decimal("80.000"))
        self.assertEqual(bal2.qty, Decimal("20.000"))
    
    def test_sale_after_purchase(self):
        """Тест: продажа после прихода."""
        # 1. Приход товара
        doc_purchase = models.Document.objects.create(
            doc_type=models.Document.DocType.PURCHASE,
            warehouse_from=self.wh1,
            counterparty=self.supplier
        )
        models.DocumentItem.objects.create(
            document=doc_purchase,
            product=self.prod1,
            qty=Decimal("100.000"),
            price=Decimal("100.00")
        )
        services.post_document(doc_purchase)
        
        bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(bal.qty, Decimal("100.000"))
        
        # 2. Продажа части товара
        doc_sale = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh1,
            counterparty=self.client
        )
        models.DocumentItem.objects.create(
            document=doc_sale,
            product=self.prod1,
            qty=Decimal("40.000"),
            price=Decimal("150.00")
        )
        services.post_document(doc_sale)
        
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("60.000"))
        
        # 3. Отменяем продажу
        services.unpost_document(doc_sale)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("100.000"))
        
        # 4. Отменяем приход
        services.unpost_document(doc_purchase)
        bal.refresh_from_db()
        self.assertEqual(bal.qty, Decimal("0.000"))

    def test_owner_can_create_sale_when_payload_contains_self_as_agent(self):
        """Владелец с agent=self не должен переводить документ в агентский режим."""
        self.user.role = Roles.OWNER
        self.user.save(update_fields=["role"])

        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("10.000"),
        )

        api_client = APIClient()
        api_client.force_authenticate(user=self.user)

        response = api_client.post(
            reverse("warehouse-documents-sale"),
            {
                "warehouse_from": str(self.wh1.id),
                "counterparty": str(self.client.id),
                "agent": str(self.user.id),
                "items": [
                    {
                        "product": str(self.prod1.id),
                        "qty": "1.000",
                        "price": "150.00",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        doc = models.Document.objects.get(id=response.data["id"])
        self.assertIsNone(doc.agent_id)
        self.assertFalse(doc.use_common_stock)

    def test_counterparties_crud_filters_by_activity_date_range(self):
        """Список контрагентов должен учитывать date_from/date_to по связанным операциям."""
        self.user.role = Roles.OWNER
        self.user.save(update_fields=["role"])

        cp_in_range = models.Counterparty.objects.create(
            company=self.company,
            branch=self.branch,
            name="Контрагент в периоде",
            phone="+996700000101",
            type=models.Counterparty.Type.CLIENT,
        )
        cp_out_range = models.Counterparty.objects.create(
            company=self.company,
            branch=self.branch,
            name="Контрагент вне периода",
            phone="+996700000102",
            type=models.Counterparty.Type.CLIENT,
        )

        in_range_doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.POSTED,
            warehouse_from=self.wh1,
            counterparty=cp_in_range,
            total=Decimal("100.00"),
        )
        out_range_doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            status=models.Document.Status.POSTED,
            warehouse_from=self.wh1,
            counterparty=cp_out_range,
            total=Decimal("200.00"),
        )

        models.Document.objects.filter(pk=in_range_doc.pk).update(
            date=timezone.make_aware(datetime(2026, 4, 5, 12, 0, 0))
        )
        models.Document.objects.filter(pk=out_range_doc.pk).update(
            date=timezone.make_aware(datetime(2026, 3, 20, 12, 0, 0))
        )

        api_client = APIClient()
        api_client.force_authenticate(user=self.user)

        response = api_client.get(
            reverse("counterparties-crud"),
            {
                "date_from": "2026-04-01",
                "date_to": "2026-04-11",
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data.get("results", response.data)
        ids = {row["id"] for row in payload}
        self.assertIn(str(cp_in_range.id), ids)
        self.assertNotIn(str(cp_out_range.id), ids)

        row = next(item for item in payload if item["id"] == str(cp_in_range.id))
        self.assertEqual(row["analytics"]["sales"]["count"], 1)
        self.assertEqual(row["analytics"]["sales"]["total"], "100.00")

    def test_owner_can_assign_agent_to_specific_warehouse_via_api(self):
        self.user.role = Roles.OWNER
        self.user.save(update_fields=["role"])

        agent = User.objects.create_user(
            email="warehouse-agent@example.com",
            password="testpass123",
            first_name="Warehouse",
            last_name="Agent",
        )

        api_client = APIClient()
        api_client.force_authenticate(user=self.user)

        response = api_client.post(
            reverse("warehouse-agents-company-memberships"),
            {
                "user": str(agent.id),
                "assigned_warehouse": str(self.wh1.id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        membership = models.CompanyWarehouseAgent.objects.get(company=self.company, user=agent)
        self.assertEqual(membership.status, models.CompanyWarehouseAgent.Status.ACTIVE)
        self.assertEqual(membership.assigned_warehouse_id, self.wh1.id)
        self.assertEqual(response.data["assigned_warehouse"], str(self.wh1.id))

    def test_agent_with_assigned_warehouse_sees_only_allowed_warehouse_and_cannot_use_other(self):
        agent = User.objects.create_user(
            email="warehouse-scope-agent@example.com",
            password="testpass123",
            first_name="Scoped",
            last_name="Agent",
        )
        models.CompanyWarehouseAgent.objects.create(
            company=self.company,
            user=agent,
            status=models.CompanyWarehouseAgent.Status.ACTIVE,
            assigned_warehouse=self.wh1,
        )

        api_client = APIClient()
        api_client.force_authenticate(user=agent)

        response = api_client.get(reverse("warehouse"))
        self.assertEqual(response.status_code, 200, response.data)
        warehouse_ids = {row["id"] for row in response.data}
        self.assertEqual(warehouse_ids, {str(self.wh1.id)})

        forbidden_cart_response = api_client.post(
            reverse("warehouse-agent-carts"),
            {
                "warehouse": str(self.wh2.id),
                "note": "Попытка доступа к чужому складу",
            },
            format="json",
        )
        self.assertEqual(forbidden_cart_response.status_code, 400, forbidden_cart_response.data)
        self.assertIn("warehouse", forbidden_cart_response.data)

        allowed_cart_response = api_client.post(
            reverse("warehouse-agent-carts"),
            {
                "warehouse": str(self.wh1.id),
                "note": "Доступный склад",
            },
            format="json",
        )
        self.assertEqual(allowed_cart_response.status_code, 201, allowed_cart_response.data)
        self.assertEqual(allowed_cart_response.data["warehouse"], str(self.wh1.id))

    def test_owner_can_dispatch_goods_to_agent_without_agent_request(self):
        self.user.role = Roles.OWNER
        self.user.save(update_fields=["role"])

        agent = User.objects.create_user(
            email="dispatch-agent@example.com",
            password="testpass123",
            first_name="Dispatch",
            last_name="Agent",
        )
        models.CompanyWarehouseAgent.objects.create(
            company=self.company,
            user=agent,
            status=models.CompanyWarehouseAgent.Status.ACTIVE,
            assigned_warehouse=self.wh1,
        )

        models.StockBalance.objects.create(
            warehouse=self.wh1,
            product=self.prod1,
            qty=Decimal("50.000"),
        )
        self.prod1.quantity = Decimal("50.000")
        self.prod1.save(update_fields=["quantity"])

        owner_client = APIClient()
        owner_client.force_authenticate(user=self.user)

        cart_response = owner_client.post(
            reverse("warehouse-agent-carts"),
            {
                "warehouse": str(self.wh1.id),
                "agent": str(agent.id),
                "note": "Выдача от владельца",
            },
            format="json",
        )
        self.assertEqual(cart_response.status_code, 201, cart_response.data)
        cart_id = cart_response.data["id"]
        self.assertEqual(cart_response.data["agent"], str(agent.id))

        item_response = owner_client.post(
            reverse("warehouse-agent-cart-items"),
            {
                "cart": cart_id,
                "product": str(self.prod1.id),
                "quantity_requested": "10.000",
            },
            format="json",
        )
        self.assertEqual(item_response.status_code, 201, item_response.data)

        dispatch_response = owner_client.post(
            reverse("warehouse-agent-cart-dispatch", kwargs={"pk": cart_id}),
            {},
            format="json",
        )
        self.assertEqual(dispatch_response.status_code, 200, dispatch_response.data)
        self.assertEqual(dispatch_response.data["status"], models.AgentRequestCart.Status.APPROVED)

        warehouse_bal = models.StockBalance.objects.get(warehouse=self.wh1, product=self.prod1)
        self.assertEqual(warehouse_bal.qty, Decimal("40.000"))

        agent_bal = models.AgentStockBalance.objects.get(
            agent=agent,
            warehouse=self.wh1,
            product=self.prod1,
        )
        self.assertEqual(agent_bal.qty, Decimal("10.000"))

    def test_owner_must_specify_agent_when_creating_cart(self):
        self.user.role = Roles.OWNER
        self.user.save(update_fields=["role"])

        owner_client = APIClient()
        owner_client.force_authenticate(user=self.user)

        response = owner_client.post(
            reverse("warehouse-agent-carts"),
            {
                "warehouse": str(self.wh1.id),
                "note": "Без агента",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("agent", response.data)
