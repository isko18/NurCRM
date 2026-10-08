"""«Сводка»: charts.payment_methods строится по фактическим деньгам, Σ == выручке."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.construction.models import Cashbox
from apps.main.analytics_market import AnalyticsView, Period
from apps.main.models import Sale, SalePayment
from apps.users.models import Company, User


class PaymentMethodsBreakdownTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.company = Company.objects.create(name="Pay Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.cashbox = Cashbox.objects.create(company=self.company, name="Касса")
        now = timezone.now()
        self.period = Period(start=now - timedelta(days=1), end=now + timedelta(days=1))

    def sale(self, method, total, lines=(), status=Sale.Status.PAID):
        s = Sale.objects.create(company=self.company, cashbox=self.cashbox, user=self.owner, status=status,
                                payment_method=method, total=Decimal(total), paid_at=timezone.now())
        for m, amount in lines:
            SalePayment.objects.create(sale=s, company=self.company, method=m, amount=Decimal(amount))
        return s

    def result(self):
        r = Request(APIRequestFactory().get("/x/"))
        r.user = self.owner
        data = AnalyticsView()._sales(r, self.company, None, self.period)
        rows = {x["method"]: x for x in data["charts"]["payment_methods"]}
        return data, rows

    def test_mixed_sale_is_split_and_sum_equals_revenue(self):
        self.sale("mixed", "1000", [("cash", "600"), ("mbank", "400")])
        self.sale("cash", "500", [("cash", "500")])
        data, rows = self.result()
        self.assertNotIn("mixed", rows)
        self.assertEqual((rows["cash"]["total"], rows["mbank"]["total"]), ("1100.00", "400.00"))
        self.assertEqual(rows["cash"]["label"], "Наличные")
        self.assertEqual(sum(Decimal(r["total"]) for r in rows.values()), Decimal(data["cards"]["revenue"]))
        self.assertEqual((data["cards"]["mixed_total"], data["cards"]["mixed_count"]), ("1000.00", 1))

    def test_legacy_sale_without_lines_uses_sale_method(self):
        self.sale("mbank", "300")
        _, rows = self.result()
        self.assertEqual(rows["mbank"]["total"], "300.00")

    def test_lines_not_matching_total_are_scaled(self):
        # строки оплат на 1100 при чеке на 1000 (сдача) — приводим к total чека
        self.sale("mixed", "1000", [("cash", "1000"), ("mbank", "100")])
        data, rows = self.result()
        self.assertEqual(sum(Decimal(r["total"]) for r in rows.values()), Decimal("1000.00"))

    def test_legacy_mixed_line_without_split_stays_marked(self):
        self.sale("mixed", "700", [("mixed", "700")])
        data, rows = self.result()
        self.assertEqual(rows["mixed"]["total"], "700.00")
        self.assertTrue(rows["mixed"]["unsplit"])

    def test_empty_period_returns_empty_list(self):
        _, rows = self.result()
        self.assertEqual(rows, {})
