"""«Сводка» → последние продажи: exclude_status/only_finished, ordering=-paid_at, items_preview/items_count."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.main.models import Cart, Sale, SaleItem
from apps.main.tests_kassa_api import KassaBase


class RecentSalesListTests(KassaBase):
    def mk(self, status, created_ago_h, paid_ago_h=None, total="100", items=()):
        now = timezone.now()
        s = Sale.objects.create(
            company=self.company, user=self.owner, shift=self.shift, cashbox=self.cashbox,
            status=status, total=Decimal(total), payment_method="cash",
            paid_at=(now - timedelta(hours=paid_ago_h)) if paid_ago_h is not None else None,
        )
        Sale.objects.filter(pk=s.pk).update(created_at=now - timedelta(hours=created_ago_h))
        for name in items:
            SaleItem.objects.create(sale=s, company=self.company, name_snapshot=name,
                                    quantity=Decimal("1"), unit_price=Decimal("100"))
        s.refresh_from_db()
        return s

    def get(self, **params):
        r = self.api.get("/api/main/pos/sales/", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["results"]

    def test_exclude_status_and_only_finished_drop_open_carts(self):
        for _ in range(20):
            self.mk(Sale.Status.NEW, 1)
        paid = [self.mk(Sale.Status.PAID, i + 2, paid_ago_h=i + 2) for i in range(8)]
        for params in ({"exclude_status": "new"}, {"only_finished": "true"}, {"status": "paid,debt,canceled"}):
            rows = self.get(page_size=8, ordering="-created_at", **params)
            self.assertEqual({r["id"] for r in rows}, {str(s.id) for s in paid}, params)
            self.assertTrue(all(r["status"] != "new" for r in rows))

    def test_order_by_paid_at_uses_payment_time_and_keeps_debt_sales_in_place(self):
        opened_morning_paid_evening = self.mk(Sale.Status.PAID, 10, paid_ago_h=1)   # открыт давно, оплачен недавно
        midday = self.mk(Sale.Status.PAID, 6, paid_ago_h=6)
        debt = self.mk(Sale.Status.DEBT, 3)                                          # без paid_at, создан 3 ч назад
        ids = [r["id"] for r in self.get(ordering="-paid_at")]
        self.assertEqual(ids, [str(opened_morning_paid_evening.id), str(debt.id), str(midday.id)])

    def test_items_preview_and_count(self):
        s = self.mk(Sale.Status.PAID, 1, paid_ago_h=1, items=["Батончик Mars 50г", "Вода", "Хлеб"])
        row = next(r for r in self.get() if r["id"] == str(s.id))
        self.assertEqual(row["items_count"], 3)
        self.assertIn(row["items_preview"], {"Батончик Mars 50г", "Вода", "Хлеб"})
        self.assertEqual(row["items_preview"], row["first_item_name"])
        empty = self.mk(Sale.Status.PAID, 2, paid_ago_h=2)
        row = next(r for r in self.get() if r["id"] == str(empty.id))
        self.assertEqual((row["items_count"], row["items_preview"]), (0, None))

    def test_list_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(3):
            self.mk(Sale.Status.PAID, i + 1, paid_ago_h=i + 1, items=["А", "Б"])
        with CaptureQueriesContext(connection) as few:
            self.get()
        for i in range(10):
            self.mk(Sale.Status.PAID, i + 5, paid_ago_h=i + 5, items=["А", "Б"])
        with CaptureQueriesContext(connection) as many:
            self.get()
        self.assertLessEqual(len(many), len(few) + 2)

