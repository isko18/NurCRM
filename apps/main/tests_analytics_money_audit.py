"""
Аудит расчёта денег в аналитике (этап 1): H2–H8, H10, H11, H14, H16, H17, M1, M24.
"""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.construction.auto_cashflow import create_auto_cashflow
from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.main.analytics_market import AnalyticsView, Period, _get_active_branch, _get_period
from apps.main.cache_utils import cache_market_analytics_key
from apps.main.models import Cart, Sale, SaleItem
from apps.main.tests_kassa_api import KassaBase
from apps.users.models import Branch, Company, User


def _req(user, query=""):
    r = Request(APIRequestFactory().get(f"/analytics/market/{query}"))
    r.user = user
    return r


class CashFlowMoneyAnalyticsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.company = Company.objects.create(name="Audit Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.cashbox = Cashbox.objects.create(company=self.company, name="Касса")
        now = timezone.now()
        self.period = Period(start=now - timedelta(days=1), end=now + timedelta(days=1))
        self.request = _req(self.owner)

    def flow(self, type_, amount, **kw):
        kw.setdefault("status", CashFlow.Status.APPROVED)
        return CashFlow.objects.create(
            company=self.company, cashbox=self.cashbox, type=type_, amount=Decimal(amount), **kw
        )

    def finance(self):
        return AnalyticsView()._finance(self.request, self.company, None, self.period)["cards"]

    def cashflow(self):
        return AnalyticsView()._cashflow(self.request, self.company, None, self.period)

    def pnl(self):
        return AnalyticsView()._pnl(self.request, self.company, None, self.period)

    # H3: строки с одним source_id — разные деньги
    def test_mixed_payment_and_several_repayments_are_not_collapsed(self):
        self.flow("income", "600", source_kind="pos_sale", source_id="s1", payment_method="cash")
        self.flow("income", "400", source_kind="pos_sale", source_id="s1", payment_method="mbank")
        for _ in range(3):
            self.flow("income", "5000", source_kind="debt_repayment", source_id="deal-1", payment_method="cash")
        self.assertEqual(self.finance()["income_total"], "16000.00")
        inflow = self.cashflow()["inflow"]
        self.assertEqual((inflow["sales_cash"], inflow["sales_card"], inflow["debt_repayments"]),
                         ("600.00", "400.00", "15000.00"))

    def test_sale_and_its_return_both_counted(self):
        self.flow("income", "1000", source_kind="pos_sale", source_id="s2", payment_method="cash")
        self.flow("expense", "1000", source_kind="pos_sale_return", source_id="s2", payment_method="cash")
        cards = self.finance()
        self.assertEqual((cards["income_total"], cards["expense_total"], cards["net_flow"]),
                         ("1000.00", "1000.00", "0.00"))

    def test_raw_twin_of_canonical_row_is_excluded(self):
        self.flow("income", "1500", source_kind="pos_sale", source_id="s3", payment_method="cash", name="Продажа")
        self.flow("income", "1500", source_cashbox_flow_id="s3", name="Продажа")
        # сырая строка без канонического двойника — реальные деньги
        self.flow("income", "200", source_cashbox_flow_id="old-sale", name="Продажа")
        self.assertEqual(self.finance()["income_total"], "1700.00")

    # H4: строки заявок на правку/отмену — не деньги
    def test_cancel_and_edit_requests_are_not_money(self):
        cancelled = self.flow("income", "1000", name="Приход", status=CashFlow.Status.REJECTED)
        self.flow("expense", "1000", request_kind="cancel", target_flow=cancelled, source_kind="cashflow_cancel")
        edited = self.flow("income", "800", name="Приход 2")
        self.flow("income", "800", request_kind="edit", target_flow=edited, proposed={"amount": "800"})
        cards = self.finance()
        self.assertEqual((cards["income_total"], cards["expense_total"]), ("800.00", "0.00"))
        self.assertEqual(self.cashbox.get_summary()["income_total"], Decimal("800.00"))

    # H5/H11: зачёт и долговая часть чека — не деньги
    def test_offset_and_debt_lines_are_not_money(self):
        self.flow("income", "300", source_kind="pos_sale", source_id="s4", payment_method="cash")
        self.flow("income", "700", source_kind="pos_prepayment", source_id="s4", payment_method="debt")
        self.flow("income", "1000", source_kind="pos_sale", source_id="s5", payment_method="offset")
        self.assertEqual(self.finance()["income_total"], "300.00")
        self.assertEqual(self.cashbox.get_summary()["income_total"], Decimal("300.00"))

    # H6: закупки и возвраты не в OPEX
    def test_pnl_opex_excludes_purchases_and_returns(self):
        self.flow("expense", "50000", source_kind="procurement_receipt", name="Закупки")
        self.flow("expense", "1000", source_kind="pos_sale_return", source_id="s6")
        self.flow("expense", "3000", source_kind="manual", name="аренда офиса")
        self.flow("expense", "700", source_kind="manual", name="Хозтовары")
        opex = self.pnl()["opex"]
        self.assertEqual((opex["rent"], opex["other"], opex["total"]), ("3000.00", "700.00", "3700.00"))

    # H7: возврат один раз, поправка ящика не деньги, категории не пересекаются
    def test_cashflow_returns_once_and_categories_exclusive(self):
        self.flow("income", "1000", source_kind="pos_sale", source_id="s7", payment_method="cash")
        self.flow("expense", "100", source_kind="pos_sale_return", source_id="s7", payment_method="cash")
        self.flow("income", "100", source_kind="pos_sale_return", source_id="s7", name="Наличные остались в кассе")
        self.flow("expense", "10000", source_kind="manual", name="аренда и налог")
        self.flow("income", "3000", source_kind="pos_prepayment", source_id="s8", payment_method="cash")
        res = self.cashflow()
        self.assertEqual(res["inflow"]["sales_cash"], "4000.00")
        self.assertEqual(res["inflow"]["other_income"], "0.00")
        self.assertEqual(res["outflow"]["returns"], "100.00")
        self.assertEqual((res["outflow"]["rent"], res["outflow"]["taxes"]), ("10000.00", "0.00"))
        self.assertEqual(res["outflow"]["total"], "10100.00")
        self.assertEqual(res["net"], "-6100.00")


class CreateAutoCashflowIdempotencyTests(TestCase):
    """H8: без ключа одинаковая сумма другим способом — отдельное движение."""

    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.company = Company.objects.create(name="Idem Co", owner=self.owner)
        self.cashbox = Cashbox.objects.create(company=self.company, name="Касса")

    def make(self, method, status=None):
        return create_auto_cashflow(
            company=self.company, cashbox=self.cashbox, type="income", amount="500",
            source_kind="pos_sale", source_id="sale-1", payment_method=method,
        )

    def test_same_amount_other_method_and_after_reject(self):
        a = self.make("cash")
        b = self.make("mbank")
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(self.make("cash").id, a.id)
        CashFlow.objects.filter(pk=a.pk).update(status=CashFlow.Status.REJECTED)
        self.assertNotEqual(self.make("cash").id, a.id)


class PeriodAndBranchTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        self.company = Company.objects.create(name="Period Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

    # H2: пустой период в прошлом не подменяется текущим месяцем
    def test_past_empty_period_is_not_substituted(self):
        cashbox = Cashbox.objects.create(company=self.company, name="Касса")
        Sale.objects.create(company=self.company, cashbox=cashbox, status=Sale.Status.PAID,
                            total=Decimal("100"), paid_at=timezone.now())
        p = _get_period(_req(self.owner, "?period_start=2025-03-01&period_end=2025-03-31"))
        self.assertEqual(timezone.localtime(p.start).date().isoformat(), "2025-03-01")
        self.assertEqual(timezone.localtime(p.end).date().isoformat(), "2025-04-01")

    # H14: сотрудник без филиала в компании с филиалами — 403
    def test_employee_without_branch_denied_when_company_has_branches(self):
        emp = User.objects.create_user(email=f"e{uuid.uuid4().hex[:6]}@t.kg", password="x",
                                       company=self.company, role="salesperson")
        self.assertIsNone(_get_active_branch(_req(emp)))  # филиалов нет — вся компания
        Branch.objects.create(company=self.company, name="Филиал")
        with self.assertRaises(PermissionDenied):
            _get_active_branch(_req(emp))

    def test_sensitive_tabs_need_analytics_access(self):
        emp = User.objects.create_user(email=f"e{uuid.uuid4().hex[:6]}@t.kg", password="x",
                                       company=self.company, role="salesperson")
        view = AnalyticsView.as_view()
        req = APIRequestFactory().get("/analytics/market/?tab=salary")
        from rest_framework.test import force_authenticate
        force_authenticate(req, user=emp)
        self.assertEqual(view(req).status_code, 403)

    # M24: запись движения сбрасывает кэш аналитики
    def test_cache_key_changes_after_cashflow_write(self):
        cashbox = Cashbox.objects.create(company=self.company, name="Касса")
        k1 = cache_market_analytics_key(str(self.company.id), None, "finance", "h")
        with self.captureOnCommitCallbacks(execute=True):
            CashFlow.objects.create(company=self.company, cashbox=cashbox, type="income",
                                    amount=Decimal("1"), status=CashFlow.Status.APPROVED)
        self.assertNotEqual(cache_market_analytics_key(str(self.company.id), None, "finance", "h"), k1)


class ShiftBucketLocalTimeTests(TestCase):
    """H10: смена, открытая в 09:00 по Бишкеку, — «утро»."""

    def test_morning_shift_in_local_time(self):
        owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        company = Company.objects.create(name="Shift Co", owner=owner)
        owner.company = company
        owner.save()
        cashbox = Cashbox.objects.create(company=company, name="Касса")
        shift = CashShift.objects.create(company=company, cashbox=cashbox, cashier=owner)
        opened = datetime.combine(timezone.localdate(), datetime.min.time()).replace(
            hour=9, tzinfo=ZoneInfo("Asia/Bishkek"))
        CashShift.objects.filter(pk=shift.pk).update(opened_at=opened)
        Sale.objects.create(company=company, cashbox=cashbox, shift=shift, status=Sale.Status.PAID,
                            total=Decimal("100"), paid_at=opened + timedelta(minutes=5), user=owner)
        period = Period(start=opened - timedelta(hours=9), end=opened + timedelta(hours=15))
        res = AnalyticsView()._shifts(_req(owner), company, None, period)
        buckets = {b["key"]: b for b in _find_buckets(res)}
        self.assertEqual(buckets["morning"]["transactions"], 1)
        self.assertEqual(buckets["evening"]["transactions"], 0)


def _find_buckets(obj):
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and obj[0].get("key") == "morning":
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_buckets(v)
            if found:
                return found
    if isinstance(obj, list):
        for v in obj:
            found = _find_buckets(v)
            if found:
                return found
    return None


class CheckoutAndExchangeFlowsTests(KassaBase):
    def _cart_checkout(self, payments, key=None):
        from apps.main.kassa_views import PosQuickCheckoutAPIView

        cart = Cart.objects.create(company=self.company, user=self.owner, shift=self.shift, status=Cart.Status.ACTIVE)
        PosQuickCheckoutAPIView()._add_items(
            cart, [{"product": self.product.id, "qty": Decimal("10"), "price": Decimal("100.00")}], False, None
        )
        cart.recalc()
        body = {"payments": payments}
        if any(p["method"] == "debt" for p in payments):
            body["client"] = str(self.client_obj.id)
        return self.api.post(f"/api/main/pos/sales/{cart.id}/checkout/", body, format="json",
                             HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def _income(self, sale_id):
        return {
            (cf.payment_method, cf.amount)
            for cf in CashFlow.objects.filter(source_id=str(sale_id), type="income")
        }

    # H8: 500 нал + 500 Мбанк — два движения
    def test_mixed_equal_parts_create_two_flows(self):
        r = self._cart_checkout([{"method": "cash", "amount": "500"}, {"method": "mbank", "amount": "500"}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self._income(r.data["sale_id"]),
                         {("cash", Decimal("500.00")), ("mbank", Decimal("500.00"))})

    # H5: долговая строка в payments[] не принимается — новых доходов на сумму долга не будет
    def test_debt_line_in_payments_is_rejected(self):
        r = self._cart_checkout([{"method": "cash", "amount": "300"}, {"method": "debt", "amount": "700"}])
        self.assertEqual(r.status_code, 400, r.data)
        self.assertFalse(CashFlow.objects.filter(company=self.company, type="income").exists())

    # H11: обмен 1000 на 1000 даёт +1000 денег, ящик смены не меняется
    def test_exchange_counts_money_once_and_keeps_drawer(self):
        sale_id = self.quick(items=[{"product": str(self.product.id), "qty": "10", "price": "100.00"}],
                             payment={"method": "cash", "received": "1000"}).data["id"]
        drawer_before = self.shift.calc_live_totals(refresh=True)["expected_cash"]
        item = SaleItem.objects.get(sale_id=sale_id)
        r = self.api.post(f"/api/main/pos/sales/{sale_id}/exchange/", {
            "return_items": [{"item": str(item.id), "qty": "10"}],
            "new_items": [{"product": str(self.product.id), "qty": "10"}],
            "payment": {"method": "cash"},
        }, format="json", HTTP_IDEMPOTENCY_KEY="ex-audit")
        self.assertEqual(r.status_code, 201, r.data)
        now = timezone.now()
        period = Period(start=now - timedelta(days=1), end=now + timedelta(days=1))
        cards = AnalyticsView()._finance(_req(self.owner), self.company, None, period)["cards"]
        self.assertEqual(cards["net_flow"], "1000.00")
        self.assertEqual(self.shift.calc_live_totals(refresh=True)["expected_cash"], drawer_before)
        self.assertEqual(self.cashbox.get_summary()["income_total"], Decimal("1000.00"))


class CashFlowEmployeeRightsTests(KassaBase):
    """H17: кассир не одобряет сам, не воскрешает отклонённое, не меняет авто-движения."""

    def setUp(self):
        super().setUp()
        self.company.cashflow_requests_enabled = True
        self.company.save()
        self.emp_api = type(self.api)()
        self.emp_api.force_authenticate(self.cashier)

    def test_employee_cannot_self_approve(self):
        r = self.emp_api.post("/api/construction/cashflows/", {
            "cashbox": str(self.cashbox.id), "type": "expense", "name": "Расход", "amount": "100",
            "status": "approved",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        flow = CashFlow.objects.get(pk=r.data["id"])
        self.assertEqual(flow.status, CashFlow.Status.PENDING)
        r = self.emp_api.patch(f"/api/construction/cashflows/{flow.id}/", {"status": "approved"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)

    def test_bulk_cannot_resurrect_rejected(self):
        flow = CashFlow.objects.create(company=self.company, cashbox=self.cashbox, type="income",
                                       amount=Decimal("10"), status=CashFlow.Status.REJECTED)
        r = self.api.patch("/api/construction/cashflows/bulk/status/",
                           {"items": [{"id": str(flow.id), "status": "approved"}]}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        flow.refresh_from_db()
        self.assertEqual(flow.status, CashFlow.Status.REJECTED)
