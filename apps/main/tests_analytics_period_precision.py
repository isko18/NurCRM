"""period_end с точностью до секунды в местном времени (Бишкек), границы включительные."""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.construction.models import Cashbox
from apps.main.models import Sale
from apps.users.models import Company, User

TZ = ZoneInfo("Asia/Bishkek")


class PeriodEndPrecisionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.company = Company.objects.create(name="Period Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        cashbox = Cashbox.objects.create(company=self.company, name="Касса")
        self.yesterday = (timezone.now().astimezone(TZ) - timedelta(days=1)).date()
        paid = datetime(self.yesterday.year, self.yesterday.month, self.yesterday.day, 15, 0, 0, tzinfo=TZ)
        Sale.objects.create(company=self.company, cashbox=cashbox, user=self.owner, status=Sale.Status.PAID,
                            total=Decimal("1000"), paid_at=paid)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def revenue(self, start, end):
        r = self.api.get("/api/main/analytics/market/", {"tab": "sales", "period_start": start, "period_end": end})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["cards"]["revenue"]

    def test_sale_at_15_is_outside_10_30_and_inside_end_of_day(self):
        d = self.yesterday.isoformat()
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T10:30:00"), "0.00")
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T23:59:59"), "1000.00")

    def test_end_bound_is_inclusive_to_the_second(self):
        d = self.yesterday.isoformat()
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T14:59:59"), "0.00")
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T15:00:00"), "1000.00")

    def test_time_is_local_bishkek_not_utc(self):
        # 15:00 Бишкека = 09:00 UTC; граница 09:30 «по UTC» не должна включать чек, 15:30 местного — включает
        d = self.yesterday.isoformat()
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T09:30:00"), "0.00")
        self.assertEqual(self.revenue(f"{d}T00:00:00", f"{d}T15:30:00"), "1000.00")

    def test_date_only_end_covers_whole_day(self):
        d = self.yesterday.isoformat()
        self.assertEqual(self.revenue(d, d), "1000.00")


from apps.main.models import Product
from apps.main.tests_kassa_api import KassaBase


class CashierAccessTests(KassaBase):
    """Кассир без права «Аналитика»: что он видит на «Сводке»."""

    def setUp(self):
        super().setUp()
        self.cashier_api = APIClient()
        self.cashier_api.force_authenticate(self.cashier)

    def test_cashier_without_analytics_right(self):
        self.assertFalse(bool(getattr(self.cashier, "can_view_analytics", False)))
        # аналитика закрыта только правом на фронте: сервер отдаёт её любому сотруднику компании
        r = self.cashier_api.get("/api/main/analytics/market/", {"tab": "sales"})
        self.assertEqual(r.status_code, 200, r.data)
        # но финансы/зарплата/P&L/денежный поток — только владельцу, админу или с правом
        for tab in ("finance", "salary", "pnl", "cashflow"):
            self.assertEqual(self.cashier_api.get("/api/main/analytics/market/", {"tab": tab}).status_code, 403, tab)

    def test_cashier_sees_all_cashiers_sales_and_stock_list(self):
        # чек владельца (другой кассир) виден кассиру в «Последних продажах»
        sale_id = self.quick().data["id"]
        r = self.cashier_api.get("/api/main/pos/sales/", {"page_size": 8})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn(str(sale_id), {x["id"] for x in r.data["results"]})
        r = self.cashier_api.get("/api/main/products/list/", {"preset": "low_stock", "ordering": "quantity"})
        self.assertEqual(r.status_code, 200, r.data)
