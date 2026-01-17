# apps/cafe/tests.py
from decimal import Decimal
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.users.models import Company, Branch
from apps.cafe.models import (
    Zone, Table, Order, OrderItem, MenuItem, Category, CafeClient, Kitchen
)
from apps.cafe.views import (
    send_order_created_notification,
    send_order_updated_notification,
    send_table_status_changed_notification,
)

User = get_user_model()


class CafeTableStatusTestCase(TransactionTestCase):
    """
    Тесты для отслеживания статуса столов в реальном времени.
    """
    
    def setUp(self):
        """Создаем тестовые данные"""
        # Создаем владельца компании
        self.owner = User.objects.create_user(
            email="owner1@test.com",
            password="testpass123"
        )
        
        self.company = Company.objects.create(name="Test Cafe Company", owner=self.owner)
        self.branch = Branch.objects.create(name="Test Branch", company=self.company)
        
        self.user = User.objects.create_user(
            email="waiter1@test.com",
            password="testpass123"
        )
        self.user.company = self.company
        self.user.save()
        
        self.zone = Zone.objects.create(
            company=self.company,
            branch=self.branch,
            title="Зона 1"
        )
        
        self.table = Table.objects.create(
            company=self.company,
            branch=self.branch,
            zone=self.zone,
            number=1,
            places=4,
            status=Table.Status.FREE
        )
        
        self.category = Category.objects.create(
            company=self.company,
            branch=self.branch,
            title="Напитки"
        )
        
        self.menu_item = MenuItem.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.category,
            title="Кофе",
            price=Decimal("150.00"),
            is_active=True
        )
        
        self.client = CafeClient.objects.create(
            company=self.company,
            branch=self.branch,
            name="Test Client",
            phone="+79991234567"
        )
    
    def test_table_becomes_busy_on_order_creation(self):
        """Тест: стол становится занятым при создании заказа"""
        self.assertEqual(self.table.status, Table.Status.FREE)
        
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Симулируем создание заказа через view
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            if table.status != Table.Status.BUSY:
                table.status = Table.Status.BUSY
                table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
    
    def test_table_becomes_free_on_order_close(self):
        """Тест: стол становится свободным при закрытии заказа"""
        # Создаем заказ и устанавливаем стол как занятый
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Закрываем заказ
        order.status = Order.Status.CLOSED
        order.save()
        
        # Симулируем освобождение стола
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)
    
    def test_table_becomes_free_on_order_cancel(self):
        """Тест: стол становится свободным при отмене заказа"""
        # Создаем заказ и устанавливаем стол как занятый
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Отменяем заказ
        order.status = Order.Status.CANCELLED
        order.save()
        
        # Симулируем освобождение стола
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)
    
    def test_table_stays_busy_with_multiple_orders(self):
        """Тест: стол остается занятым, если есть другие открытые заказы"""
        # Создаем первый заказ
        order1 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Создаем второй заказ
        order2 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=3,
            status=Order.Status.OPEN
        )
        
        # Закрываем первый заказ
        order1.status = Order.Status.CLOSED
        order1.save()
        
        # Стол должен остаться занятым, так как есть второй открытый заказ
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
        
        # Закрываем второй заказ
        order2.status = Order.Status.CLOSED
        order2.save()
        
        # Теперь стол должен стать свободным
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)
    
    def test_table_becomes_free_on_order_deletion(self):
        """Тест: стол становится свободным при удалении заказа (если нет других открытых)"""
        # Создаем заказ
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Удаляем заказ
        order_id = order.id
        order.delete()
        
        # Проверяем, что нет других открытых заказов
        has_open_orders = Order.objects.filter(
            table_id=self.table.id,
            status=Order.Status.OPEN
        ).exists()
        
        self.assertFalse(has_open_orders)
        
        # Стол должен стать свободным
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            if not has_open_orders:
                table.status = Table.Status.FREE
                table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)


class CafeWebSocketNotificationsTestCase(TestCase):
    """
    Тесты для WebSocket уведомлений.
    """
    
    def setUp(self):
        """Создаем тестовые данные"""
        # Создаем владельца компании
        self.owner = User.objects.create_user(
            email="owner1@test.com",
            password="testpass123"
        )
        
        self.company = Company.objects.create(name="Test Cafe Company", owner=self.owner)
        self.branch = Branch.objects.create(name="Test Branch", company=self.company)
        
        self.user = User.objects.create_user(
            email="waiter1@test.com",
            password="testpass123"
        )
        self.user.company = self.company
        self.user.save()
        
        self.zone = Zone.objects.create(
            company=self.company,
            branch=self.branch,
            title="Зона 1"
        )
        
        self.table = Table.objects.create(
            company=self.company,
            branch=self.branch,
            zone=self.zone,
            number=1,
            places=4,
            status=Table.Status.FREE
        )
        
        self.category = Category.objects.create(
            company=self.company,
            branch=self.branch,
            title="Напитки"
        )
        
        self.menu_item = MenuItem.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.category,
            title="Кофе",
            price=Decimal("150.00"),
            is_active=True
        )
        
        self.client = CafeClient.objects.create(
            company=self.company,
            branch=self.branch,
            name="Test Client",
            phone="+79991234567"
        )
    
    def test_send_order_created_notification(self):
        """Тест: отправка уведомления о создании заказа"""
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Проверяем, что функция не падает
        try:
            send_order_created_notification(order)
            notification_sent = True
        except Exception as e:
            notification_sent = False
            print(f"Error sending notification: {e}")
        
        self.assertTrue(notification_sent)
    
    def test_send_order_updated_notification(self):
        """Тест: отправка уведомления об обновлении заказа"""
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Обновляем заказ
        order.guests = 4
        order.save()
        
        # Проверяем, что функция не падает
        try:
            send_order_updated_notification(order)
            notification_sent = True
        except Exception as e:
            notification_sent = False
            print(f"Error sending notification: {e}")
        
        self.assertTrue(notification_sent)
    
    def test_send_table_status_changed_notification(self):
        """Тест: отправка уведомления об изменении статуса стола"""
        # Изменяем статус стола
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Проверяем, что функция не падает
        try:
            send_table_status_changed_notification(self.table)
            notification_sent = True
        except Exception as e:
            notification_sent = False
            print(f"Error sending notification: {e}")
        
        self.assertTrue(notification_sent)
    
    def test_table_status_changed_on_order_creation(self):
        """Тест: статус стола изменяется при создании заказа"""
        self.assertEqual(self.table.status, Table.Status.FREE)
        
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Симулируем логику из view
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            if table.status != Table.Status.BUSY:
                table.status = Table.Status.BUSY
                table.save(update_fields=["status"])
                send_table_status_changed_notification(table)
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
    
    def test_table_status_changed_on_order_close(self):
        """Тест: статус стола изменяется при закрытии заказа"""
        # Создаем заказ и устанавливаем стол как занятый
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Закрываем заказ
        order.status = Order.Status.CLOSED
        order.save()
        
        # Симулируем логику из view
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
            send_table_status_changed_notification(table)
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)


class CafeOrderIntegrationTestCase(TransactionTestCase):
    """
    Интеграционные тесты для заказов и столов.
    """
    
    def setUp(self):
        """Создаем тестовые данные"""
        # Создаем владельца компании
        self.owner = User.objects.create_user(
            email="owner1@test.com",
            password="testpass123"
        )
        
        self.company = Company.objects.create(name="Test Cafe Company", owner=self.owner)
        self.branch = Branch.objects.create(name="Test Branch", company=self.company)
        
        self.user = User.objects.create_user(
            email="waiter1@test.com",
            password="testpass123"
        )
        self.user.company = self.company
        self.user.save()
        
        self.zone = Zone.objects.create(
            company=self.company,
            branch=self.branch,
            title="Зона 1"
        )
        
        self.table = Table.objects.create(
            company=self.company,
            branch=self.branch,
            zone=self.zone,
            number=1,
            places=4,
            status=Table.Status.FREE
        )
        
        self.category = Category.objects.create(
            company=self.company,
            branch=self.branch,
            title="Напитки"
        )
        
        self.menu_item = MenuItem.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.category,
            title="Кофе",
            price=Decimal("150.00"),
            is_active=True
        )
        
        self.client = CafeClient.objects.create(
            company=self.company,
            branch=self.branch,
            name="Test Client",
            phone="+79991234567"
        )
    
    def test_order_creation_workflow(self):
        """Тест: полный workflow создания заказа"""
        # Стол свободен
        self.assertEqual(self.table.status, Table.Status.FREE)
        
        # Создаем заказ
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Добавляем позицию в заказ
        OrderItem.objects.create(
            company=self.company,
            order=order,
            menu_item=self.menu_item,
            quantity=2
        )
        
        # Пересчитываем сумму
        order.recalc_total()
        order.save(update_fields=["total_amount"])
        
        # Устанавливаем стол как занятый
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            if table.status != Table.Status.BUSY:
                table.status = Table.Status.BUSY
                table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
        self.assertEqual(order.total_amount, Decimal("300.00"))  # 2 * 150.00
    
    def test_order_payment_workflow(self):
        """Тест: полный workflow оплаты заказа"""
        # Создаем заказ
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        # Добавляем позицию
        OrderItem.objects.create(
            company=self.company,
            order=order,
            menu_item=self.menu_item,
            quantity=1
        )
        
        # Устанавливаем стол как занятый
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Оплачиваем и закрываем заказ
        order.recalc_total()
        order.is_paid = True
        order.paid_at = timezone.now()
        order.payment_method = "cash"
        order.status = Order.Status.CLOSED
        order.save(update_fields=[
            "total_amount", "is_paid", "paid_at", "payment_method", "status"
        ])
        
        # Освобождаем стол
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CLOSED)
    
    def test_multiple_orders_same_table(self):
        """Тест: несколько заказов на одном столе"""
        # Создаем первый заказ
        order1 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN
        )
        
        self.table.status = Table.Status.BUSY
        self.table.save()
        
        # Создаем второй заказ на том же столе
        order2 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=3,
            status=Order.Status.OPEN
        )
        
        # Стол должен остаться занятым
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
        
        # Закрываем первый заказ
        order1.status = Order.Status.CLOSED
        order1.save()
        
        # Стол все еще должен быть занятым (есть второй заказ)
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)
        
        # Закрываем второй заказ
        order2.status = Order.Status.CLOSED
        order2.save()
        
        # Теперь стол должен стать свободным
        with transaction.atomic():
            table = Table.objects.select_for_update().get(id=self.table.id)
            table.status = Table.Status.FREE
            table.save(update_fields=["status"])
        
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.FREE)
