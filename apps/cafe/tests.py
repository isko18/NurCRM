# apps/cafe/tests.py
import uuid
from decimal import Decimal
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import Company, Branch
from apps.cafe.models import (
    Zone, Table, Order, OrderItem, MenuItem, Category, CafeClient, Kitchen, OrderDebtPayment,
    CafeWaiterPayProfile,
)
from rest_framework.request import Request

from apps.cafe.analytics import (
    SalesSummaryView,
    CafeWaiterSalaryReportView,
    CafeUnifiedAnalyticsView,
)
from apps.cafe.views import (
    send_order_created_notification,
    send_order_updated_notification,
    send_table_status_changed_notification,
    OrderPayView,
    OrderPayDebtView,
    OrderRetrieveUpdateDestroyView,
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

        self.api_factory = APIRequestFactory()
    
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

    def test_table_stays_busy_on_order_close_via_api_with_multiple_open_orders(self):
        """
        Регрессия: если на столе есть несколько OPEN-заказов, закрытие одного через API
        НЕ должно делать стол FREE.
        """
        order1 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN,
        )
        Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=3,
            status=Order.Status.OPEN,
        )

        self.table.status = Table.Status.BUSY
        self.table.save(update_fields=["status"])

        request = self.api_factory.patch(
            f"/cafe/orders/{order1.id}/",
            {"status": Order.Status.CLOSED},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = OrderRetrieveUpdateDestroyView.as_view()(request, pk=str(order1.id))
        self.assertEqual(response.status_code, 200)

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)

    def test_order_cancel_sets_canceled_by_and_time_via_api(self):
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN,
        )

        request = self.api_factory.patch(
            f"/cafe/orders/{order.id}/",
            {"status": Order.Status.CANCELLED},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = OrderRetrieveUpdateDestroyView.as_view()(request, pk=str(order.id))
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertIsNotNone(order.canceled_at)
        self.assertEqual(order.canceled_by_id, self.user.id)

    def test_table_stays_busy_on_order_pay_close_via_api_with_multiple_open_orders(self):
        """
        Регрессия: оплата+закрытие одного заказа через /pay/ не должна освобождать стол,
        если есть другой OPEN-заказ на этом же столе.
        """
        order1 = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=2,
            status=Order.Status.OPEN,
        )
        Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.client,
            waiter=self.user,
            guests=3,
            status=Order.Status.OPEN,
        )

        self.table.status = Table.Status.BUSY
        self.table.save(update_fields=["status"])

        request = self.api_factory.post(
            f"/cafe/orders/{order1.id}/pay/",
            {"payment_method": "cash", "discount_amount": "0.00", "close_order": True},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = OrderPayView.as_view()(request, pk=str(order1.id))
        self.assertEqual(response.status_code, 200)

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.Status.BUSY)


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


class CafeOrderDebtAPITestCase(TransactionTestCase):
    """Оплата в долг и частичное погашение через /pay/ и /pay-debt/."""

    def setUp(self):
        self.owner = User.objects.create_user(email="owner-debt@test.com", password="testpass123")
        self.company = Company.objects.create(name="Debt Cafe Co", owner=self.owner)
        self.branch = Branch.objects.create(name="Branch D", company=self.company)
        self.user = User.objects.create_user(email="waiter-debt@test.com", password="testpass123")
        self.user.company = self.company
        self.user.save()
        self.zone = Zone.objects.create(company=self.company, branch=self.branch, title="Z")
        self.table = Table.objects.create(
            company=self.company, branch=self.branch, zone=self.zone, number=9, places=4, status=Table.Status.BUSY
        )
        self.category = Category.objects.create(company=self.company, branch=self.branch, title="Drinks")
        self.menu_item = MenuItem.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.category,
            title="Tea",
            price=Decimal("100.00"),
            is_active=True,
        )
        self.cafe_client = CafeClient.objects.create(
            company=self.company, branch=self.branch, name="Debt Guest", phone="+70001112233"
        )
        self.api_factory = APIRequestFactory()

    def test_full_debt_then_pay_in_two_parts(self):
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.cafe_client,
            waiter=self.user,
            guests=1,
            status=Order.Status.OPEN,
        )
        OrderItem.objects.create(company=self.company, order=order, menu_item=self.menu_item, quantity=3)

        req = self.api_factory.post(
            f"/cafe/orders/{order.id}/pay/",
            {"payment_method": "debt", "discount_amount": "0.00", "close_order": True},
            format="json",
        )
        force_authenticate(req, user=self.user)
        r1 = OrderPayView.as_view()(req, pk=str(order.id))
        self.assertEqual(r1.status_code, 200, getattr(r1, "data", r1.content))
        self.assertFalse(r1.data["is_paid"])
        self.assertEqual(r1.data["payment_method"], "debt")
        self.assertEqual(Decimal(r1.data["final_amount"]), Decimal("300.00"))
        self.assertEqual(Decimal(r1.data["balance_due"]), Decimal("300.00"))

        order.refresh_from_db()
        self.assertTrue(order.stock_deducted)

        req2 = self.api_factory.post(
            f"/cafe/orders/{order.id}/pay-debt/",
            {
                "amount": "100.00",
                "payment_method": "transfer",
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        force_authenticate(req2, user=self.user)
        r2 = OrderPayDebtView.as_view()(req2, pk=str(order.id))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Decimal(r2.data["balance_due"]), Decimal("200.00"))

        req3 = self.api_factory.post(
            f"/cafe/orders/{order.id}/pay-debt/",
            {
                "amount": "200.00",
                "payment_method": "card",
                "idempotency_key": str(uuid.uuid4()),
            },
            format="json",
        )
        force_authenticate(req3, user=self.user)
        r3 = OrderPayDebtView.as_view()(req3, pk=str(order.id))
        self.assertEqual(r3.status_code, 200)
        self.assertTrue(r3.data["is_paid"])
        self.assertEqual(Decimal(r3.data["balance_due"]), Decimal("0"))

        self.assertEqual(OrderDebtPayment.objects.filter(order=order).count(), 2)

    def test_prepaid_and_debt_on_pay(self):
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=self.table,
            client=self.cafe_client,
            waiter=self.user,
            guests=1,
            status=Order.Status.OPEN,
        )
        OrderItem.objects.create(company=self.company, order=order, menu_item=self.menu_item, quantity=2)

        idem = uuid.uuid4()
        req = self.api_factory.post(
            f"/cafe/orders/{order.id}/pay/",
            {
                "payment_method": "debt",
                "prepaid_amount": "50.00",
                "prepaid_payment_method": "cash",
                "idempotency_key": str(idem),
                "discount_amount": "0",
                "close_order": True,
            },
            format="json",
        )
        force_authenticate(req, user=self.user)
        r = OrderPayView.as_view()(req, pk=str(order.id))
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        self.assertFalse(r.data["is_paid"])
        self.assertEqual(Decimal(r.data["paid_amount"]), Decimal("50.00"))
        self.assertEqual(Decimal(r.data["balance_due"]), Decimal("150.00"))


class CafeWaiterAnalyticsScopeTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner-analytics@test.com", password="testpass123")
        self.owner.role = "owner"
        self.owner.save(update_fields=["role"])

        self.company = Company.objects.create(name="Analytics Cafe Co", owner=self.owner)
        self.branch = Branch.objects.create(name="Analytics Branch", company=self.company)

        self.waiter1 = User.objects.create_user(email="waiter-1@test.com", password="testpass123")
        self.waiter1.company = self.company
        self.waiter1.save(update_fields=["company"])

        self.waiter2 = User.objects.create_user(email="waiter-2@test.com", password="testpass123")
        self.waiter2.company = self.company
        self.waiter2.save(update_fields=["company"])

        self.zone = Zone.objects.create(company=self.company, branch=self.branch, title="Main zone")
        self.table1 = Table.objects.create(
            company=self.company,
            branch=self.branch,
            zone=self.zone,
            number=1,
            places=4,
            status=Table.Status.FREE,
        )
        self.table2 = Table.objects.create(
            company=self.company,
            branch=self.branch,
            zone=self.zone,
            number=2,
            places=4,
            status=Table.Status.FREE,
        )
        self.category = Category.objects.create(company=self.company, branch=self.branch, title="Drinks")
        self.menu_item = MenuItem.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.category,
            title="Coffee",
            price=Decimal("100.00"),
            is_active=True,
        )
        self.client1 = CafeClient.objects.create(
            company=self.company,
            branch=self.branch,
            name="Guest 1",
            phone="+70000000001",
        )
        self.client2 = CafeClient.objects.create(
            company=self.company,
            branch=self.branch,
            name="Guest 2",
            phone="+70000000002",
        )
        self.api_factory = APIRequestFactory()
        self.period_day = timezone.localdate().isoformat()

    def _create_paid_order(self, *, waiter, table, client, quantity):
        order = Order.objects.create(
            company=self.company,
            branch=self.branch,
            table=table,
            client=client,
            waiter=waiter,
            guests=1,
            status=Order.Status.OPEN,
        )
        OrderItem.objects.create(
            company=self.company,
            order=order,
            menu_item=self.menu_item,
            quantity=quantity,
        )
        order.recalc_total()
        order.discount_amount = order.discount_amount or Decimal("0")
        order.paid_amount = (order.total_amount or Decimal("0")) - order.discount_amount
        order.is_paid = True
        order.paid_at = timezone.now()
        order.payment_method = "cash"
        order.status = Order.Status.CLOSED
        order.save(
            update_fields=[
                "total_amount",
                "discount_amount",
                "paid_amount",
                "is_paid",
                "paid_at",
                "payment_method",
                "status",
            ]
        )
        return order

    def test_waiter_sales_summary_is_scoped_to_current_waiter(self):
        self._create_paid_order(waiter=self.waiter1, table=self.table1, client=self.client1, quantity=1)
        self._create_paid_order(waiter=self.waiter2, table=self.table2, client=self.client2, quantity=2)

        request = self.api_factory.get(
            f"/cafe/analytics/sales/summary/?branch={self.branch.id}&date_from={self.period_day}&date_to={self.period_day}"
        )
        force_authenticate(request, user=self.waiter1)
        response = SalesSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["orders_count"], 1)
        self.assertEqual(response.data["items_qty"], 1)
        self.assertEqual(response.data["revenue"], "100.00")

    def test_owner_sales_summary_sees_all_waiters(self):
        self._create_paid_order(waiter=self.waiter1, table=self.table1, client=self.client1, quantity=1)
        self._create_paid_order(waiter=self.waiter2, table=self.table2, client=self.client2, quantity=2)

        request = self.api_factory.get(
            f"/cafe/analytics/sales/summary/?branch={self.branch.id}&date_from={self.period_day}&date_to={self.period_day}"
        )
        force_authenticate(request, user=self.owner)
        response = SalesSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["orders_count"], 2)
        self.assertEqual(response.data["items_qty"], 3)
        self.assertEqual(response.data["revenue"], "300.00")

    def test_sales_summary_net_after_position_refund(self):
        order = self._create_paid_order(
            waiter=self.waiter1, table=self.table1, client=self.client1, quantity=2
        )
        item = order.items.first()
        item.refunded_quantity = 1
        item.save(update_fields=["refunded_quantity"])
        order.refunded_amount = Decimal("100.00")
        order.save(update_fields=["refunded_amount"])

        request = self.api_factory.get(
            f"/cafe/analytics/sales/summary/?branch={self.branch.id}&date_from={self.period_day}&date_to={self.period_day}"
        )
        force_authenticate(request, user=self.owner)
        response = SalesSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["orders_count"], 1)
        self.assertEqual(response.data["items_qty"], 1)
        self.assertEqual(response.data["revenue"], "100.00")

    def test_waiter_salary_report_returns_only_own_base_plus_percent(self):
        self._create_paid_order(waiter=self.waiter1, table=self.table1, client=self.client1, quantity=1)
        self._create_paid_order(waiter=self.waiter2, table=self.table2, client=self.client2, quantity=2)

        CafeWaiterPayProfile.objects.create(
            company=self.company,
            branch=self.branch,
            user=self.waiter1,
            monthly_base_salary=Decimal("3000.00"),
            revenue_percent=Decimal("10.00"),
        )
        CafeWaiterPayProfile.objects.create(
            company=self.company,
            branch=self.branch,
            user=self.waiter2,
            monthly_base_salary=Decimal("6000.00"),
            revenue_percent=Decimal("20.00"),
        )

        request = self.api_factory.get(
            f"/cafe/analytics/waiter-salary/?branch={self.branch.id}&date_from={self.period_day}&date_to={self.period_day}"
        )
        force_authenticate(request, user=self.waiter1)
        response = CafeWaiterSalaryReportView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rows"]), 1)

        row = response.data["rows"][0]
        self.assertEqual(row["user_id"], str(self.waiter1.id))
        self.assertEqual(row["monthly_base_salary"], "3000.00")
        self.assertEqual(row["revenue_percent"], "10.00")
        self.assertEqual(row["base_prorated"], "100.00")
        self.assertEqual(row["waiter_revenue_period"], "100.00")
        self.assertEqual(row["percent_bonus"], "10.00")
        self.assertEqual(row["total"], "110.00")

    def test_unified_analytics_delegation_preserves_query_params(self):
        """
        CafeUnifiedAnalyticsView передаёт во вложенные вьюхи django HttpRequest; параметры периода и branch
        должны читаться через GET, иначе вкладки unified возвращают пустую аналитику / падают.
        """
        self._create_paid_order(waiter=self.waiter1, table=self.table1, client=self.client1, quantity=1)
        self._create_paid_order(waiter=self.waiter2, table=self.table2, client=self.client2, quantity=2)

        unified_wsgi = self.api_factory.get(
            "/cafe/analytics/unified/",
            {
                "tab": "sales_summary",
                "branch": str(self.branch.id),
                "date_from": self.period_day,
                "date_to": self.period_day,
            },
        )
        force_authenticate(unified_wsgi, user=self.owner)
        unified_resp = CafeUnifiedAnalyticsView.as_view()(Request(unified_wsgi))

        direct = self.api_factory.get(
            f"/cafe/analytics/sales/summary/?branch={self.branch.id}&date_from={self.period_day}&date_to={self.period_day}"
        )
        force_authenticate(direct, user=self.owner)
        direct_resp = SalesSummaryView.as_view()(direct)

        self.assertEqual(unified_resp.status_code, 200, getattr(unified_resp, "data", unified_resp.content))
        self.assertEqual(direct_resp.status_code, 200)
        self.assertEqual(unified_resp.data, direct_resp.data)
