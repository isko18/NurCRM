from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.main.analytics_market import AnalyticsView
from apps.main.models import Notification, Product
from apps.main.serializers import ProductListSerializer, ProductSerializer
from apps.main.services.product_list_filters import apply_product_list_filters
from apps.main.services_expiry import (
    send_product_expiry_digest_for_all_companies,
    send_product_expiry_digest_for_company,
)
from apps.main.tasks import product_expiry_digest
from apps.users.models import Branch, Company, SubscriptionPlan

User = get_user_model()


class ProductExpiryTrackingTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_exp_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(
            email=email_owner, password="pass", role="owner"
        )
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(
            name="Expiry Test Co", owner=self.owner, subscription_plan=plan
        )
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        # Today according to Asia/Bishkek timezone
        self.today = timezone.localdate()

        # 1. Expired product (today - 1)
        self.prod_expired = Product.objects.create(
            name="Просроченный товар",
            company=self.company,
            branch=self.branch,
            price=Decimal("100.00"),
            quantity=Decimal("5.000"),
            expiration_date=self.today - timedelta(days=1),
        )

        # 2. Critical product (today + 2)
        self.prod_critical = Product.objects.create(
            name="Критический товар",
            company=self.company,
            branch=self.branch,
            price=Decimal("150.00"),
            quantity=Decimal("10.000"),
            expiration_date=self.today + timedelta(days=2),
        )

        # 3. Warning product (today + 10)
        self.prod_warning = Product.objects.create(
            name="Предупреждающий товар",
            company=self.company,
            branch=self.branch,
            price=Decimal("200.00"),
            quantity=Decimal("15.000"),
            expiration_date=self.today + timedelta(days=10),
        )

        # 4. OK product far in future (today + 30)
        self.prod_ok = Product.objects.create(
            name="Свежий товар",
            company=self.company,
            branch=self.branch,
            price=Decimal("300.00"),
            quantity=Decimal("20.000"),
            expiration_date=self.today + timedelta(days=30),
        )

        # 5. Product without expiration date (None)
        self.prod_no_expiry = Product.objects.create(
            name="Товар без срока",
            company=self.company,
            branch=self.branch,
            price=Decimal("50.00"),
            quantity=Decimal("50.000"),
            expiration_date=None,
        )

    def test_analytics_products_tab_expiry_tracking(self):
        """
        Тестирует отображение товаров со сроком годности на вкладке аналитики 'products':
        - expired: days_left < 0, status='expired'
        - critical: 0 <= days_left <= 3, status='critical'
        - warning: 3 < days_left <= 14, status='warning'
        - свежие и без срока не попадают в таблицу expiring_products
        """
        view = AnalyticsView.as_view()
        request = self.factory.get("/api/main/analytics/market/?tab=products")
        force_authenticate(request, user=self.owner)
        response = view(request)
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertIn("cards", data)
        self.assertIn("tables", data)

        cards = data["cards"]
        self.assertEqual(cards["expired_products_count"], 1)
        self.assertEqual(cards["expiring_products_count"], 2)  # critical + warning

        tables = data["tables"]
        self.assertIn("expiring_products", tables)
        expiring = tables["expiring_products"]

        # В таблице ровно 3 товара (expired, critical, warning)
        self.assertEqual(len(expiring), 3)

        # Проверяем сортировку: expired первый
        self.assertEqual(expiring[0]["id"], str(self.prod_expired.id))
        self.assertEqual(expiring[0]["status"], "expired")
        self.assertEqual(expiring[0]["days_left"], -1)

        # critical второй
        self.assertEqual(expiring[1]["id"], str(self.prod_critical.id))
        self.assertEqual(expiring[1]["status"], "critical")
        self.assertEqual(expiring[1]["days_left"], 2)

        # warning третий
        self.assertEqual(expiring[2]["id"], str(self.prod_warning.id))
        self.assertEqual(expiring[2]["status"], "warning")
        self.assertEqual(expiring[2]["days_left"], 10)

        # ID свежего и товара без срока отсутствуют
        expiring_ids = {item["id"] for item in expiring}
        self.assertNotIn(str(self.prod_ok.id), expiring_ids)
        self.assertNotIn(str(self.prod_no_expiry.id), expiring_ids)

    def test_product_expiry_digest_notification_and_idempotency(self):
        """
        Тестирует отправку ночного дайджеста market.product.expiring:
        - Создаётся одно сводное уведомление
        - Уровень CRITICAL (т.к. есть просроченный товар)
        - Повторный запуск в тот же день идемпотентен (не дублирует)
        """
        # 1-й запуск
        notif = send_product_expiry_digest_for_company(self.company, today=self.today)
        self.assertIsNotNone(notif)
        self.assertEqual(notif.type, "market.product.expiring")
        self.assertEqual(notif.level, Notification.Level.CRITICAL)
        self.assertEqual(notif.data["expired_count"], 1)
        self.assertEqual(notif.data["critical_count"], 1)
        self.assertEqual(notif.data["warning_count"], 1)
        self.assertEqual(notif.data["source_kind"], "product_expiry_digest")

        # 2-й запуск в тот же день -> должен вернуть None и не создавать повторный Notif
        notif_second = send_product_expiry_digest_for_company(
            self.company, today=self.today
        )
        self.assertIsNone(notif_second)

        total_notifs = Notification.objects.filter(
            company=self.company, type="market.product.expiring"
        ).count()
        self.assertEqual(total_notifs, 1)

    def test_no_notification_when_no_expiring_products(self):
        """
        Если у компании нет товаров с истекающим/истёкшим сроком годности,
        дайджест не создаётся (никаких пустых уведомлений).
        """
        email_clean = f"owner_clean_{uuid.uuid4().hex[:6]}@test.com"
        clean_owner = User.objects.create_user(
            email=email_clean, password="pass", role="owner"
        )
        clean_company = Company.objects.create(
            name="Clean Co", owner=clean_owner
        )
        clean_owner.company = clean_company
        clean_owner.save()

        # Создаём только товар далеко в будущем
        Product.objects.create(
            name="Только свежий",
            company=clean_company,
            price=Decimal("10.00"),
            quantity=Decimal("1.000"),
            expiration_date=self.today + timedelta(days=60),
        )

        notif = send_product_expiry_digest_for_company(clean_company, today=self.today)
        self.assertIsNone(notif)
        self.assertEqual(
            Notification.objects.filter(
                company=clean_company, type="market.product.expiring"
            ).count(),
            0,
        )

    def test_warehouse_filters(self):
        """
        Тестирует фильтры склада expiring_only=true и expired_only=true через apply_product_list_filters.
        """
        base_qs = Product.objects.filter(company=self.company)

        # 1. expiring_only: expiration_date <= today + 14 days (включает expired, critical, warning)
        expiring_qs = apply_product_list_filters(base_qs, {"expiring_only": "true"})
        expiring_ids = set(expiring_qs.values_list("id", flat=True))
        self.assertIn(self.prod_expired.id, expiring_ids)
        self.assertIn(self.prod_critical.id, expiring_ids)
        self.assertIn(self.prod_warning.id, expiring_ids)
        self.assertNotIn(self.prod_ok.id, expiring_ids)
        self.assertNotIn(self.prod_no_expiry.id, expiring_ids)

        # 2. expired_only: expiration_date < today
        expired_qs = apply_product_list_filters(base_qs, {"expired_only": "true"})
        expired_ids = set(expired_qs.values_list("id", flat=True))
        self.assertIn(self.prod_expired.id, expired_ids)
        self.assertNotIn(self.prod_critical.id, expired_ids)
        self.assertNotIn(self.prod_warning.id, expired_ids)
        self.assertNotIn(self.prod_ok.id, expired_ids)
        self.assertNotIn(self.prod_no_expiry.id, expired_ids)

    def test_serializers_include_expiration_date(self):
        """
        Проверяет, что ProductListSerializer и ProductSerializer корректно сериализуют expiration_date.
        """
        list_data = ProductListSerializer(self.prod_critical).data
        self.assertIn("expiration_date", list_data)
        self.assertEqual(
            str(list_data["expiration_date"]),
            (self.today + timedelta(days=2)).isoformat(),
        )

        detail_data = ProductSerializer(self.prod_critical).data
        self.assertIn("expiration_date", detail_data)
        self.assertEqual(
            str(detail_data["expiration_date"]),
            (self.today + timedelta(days=2)).isoformat(),
        )

    def test_management_command_and_celery_task(self):
        """
        Проверяет вызов celery-таски product_expiry_digest и django management command check_product_expiry.
        """
        # Celery task
        res = product_expiry_digest()
        self.assertIn("created_notifications_count", res)

        # Management command
        call_command("check_product_expiry", company_id=str(self.company.id))
        call_command("check_product_expiry")
