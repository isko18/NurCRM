"""
Три колонки таблицы контрагентов: общий долг / оплачено / сколько в итоге должен.

Дебет — начислено (отгрузка клиенту, выдача денег), кредит — погашено (оплата,
возврат). «Сколько в итоге должен» = дебет − кредит и величина накопительная:
считается от сальдо на конец периода, а не от оборота внутри него.
"""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Company
from apps.warehouse import models

User = get_user_model()

SUMMARY_URL = "/api/warehouse/counterparties/balance-summary/"


class CounterpartyDebtColumnsTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:6]
        self.owner = User.objects.create_user(
            email=f"owner_cdc_{suffix}@test.com", password="pass", role="owner"
        )
        self.company = Company.objects.create(name=f"CDC Co {suffix}", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.wh = models.Warehouse.objects.create(
            company=self.company, name="Основной", location="Бишкек",
        )
        self.cp = models.Counterparty.objects.create(
            company=self.company, name="Должник", type=models.Counterparty.Type.CLIENT,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def _sale(self, total, date):
        """Отгрузка клиенту — дебет, долг растёт."""
        # У Document нет company — компания берётся через warehouse_from.
        return models.Document.objects.create(
            warehouse_from=self.wh, counterparty=self.cp,
            doc_type=models.Document.DocType.SALE, status=models.Document.Status.POSTED,
            total=Decimal(total), date=f"{date}T12:00:00+06:00",
        )

    def _payment(self, amount, date):
        """Приход денег — кредит, долг гасится."""
        return models.MoneyDocument.objects.create(
            company=self.company, counterparty=self.cp,
            doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT,
            status=models.MoneyDocument.Status.POSTED,
            amount=Decimal(amount), date=f"{date}T12:00:00+06:00",
        )

    def _summary(self, date_from="2026-09-01", date_to="2026-09-30"):
        resp = self.api.get(SUMMARY_URL, {"type": "client", "date_from": date_from, "date_to": date_to})
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_summary_exposes_net_and_totals(self):
        self._sale("1000.00", "2026-09-05")
        self._payment("300.00", "2026-09-10")

        data = self._summary()
        self.assertEqual(data["turnover"]["debit"], "1000.00")
        self.assertEqual(data["turnover"]["credit"], "300.00")
        self.assertEqual(data["turnover"]["net"], "700.00")

        totals = data["totals"]
        self.assertEqual(totals["debt_total"], "1000.00")       # 1 — общий долг
        self.assertEqual(totals["paid_total"], "300.00")        # 2 — оплачено
        self.assertEqual(totals["debt_remaining"], "700.00")    # 3 — в итоге должен
        self.assertEqual(totals["counterparties_owe"], "700.00")
        self.assertEqual(totals["company_owes"], "0.00")

    def test_overpayment_is_negative_remaining(self):
        self._sale("500.00", "2026-09-05")
        self._payment("800.00", "2026-09-10")

        totals = self._summary()["totals"]
        self.assertEqual(totals["debt_remaining"], "-300.00")   # переплата
        self.assertEqual(totals["counterparties_owe"], "0.00")
        self.assertEqual(totals["company_owes"], "300.00")

    def test_debt_is_cumulative_not_period_turnover(self):
        """Долг с прошлого месяца виден, даже если в периоде операций не было."""
        self._sale("1000.00", "2026-08-05")

        data = self._summary()
        self.assertEqual(data["turnover"]["net"], "0.00")       # внутри периода пусто
        self.assertEqual(data["opening"]["net"], "1000.00")     # но долг накоплен
        self.assertEqual(data["totals"]["debt_remaining"], "1000.00")

    def test_row_exposes_same_three_numbers(self):
        self._sale("1000.00", "2026-09-05")
        self._payment("300.00", "2026-09-10")

        resp = self.api.get(
            "/api/warehouse/crud/counterparties/",
            {"date_from": "2026-09-01", "date_to": "2026-09-30"},
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        row = next(r for r in rows if r["name"] == "Должник")
        debts = row["analytics"]["debts"]

        self.assertEqual(debts["debt_total"], "1000.00")
        self.assertEqual(debts["paid_total"], "300.00")
        self.assertEqual(debts["debt_remaining"], "700.00")
        self.assertEqual(debts["balance"], debts["debt_remaining"])


class CounterpartyNettedBalanceTests(CounterpartyDebtColumnsTests):
    """
    Сальдо сворачивается: дебет и кредит не бывают заполнены одновременно.
    Оборот не сворачивается — это реальные движения периода.
    """

    def test_reference_example_from_business(self):
        """
        Пример заказчика: 320 524,34 долга на начало, за период отгрузили
        414 115,00 и получили 518 985,34 → на конец остаётся долг 215 654,00,
        переплат нет.
        """
        self._sale("320524.34", "2026-03-15")          # долг до периода
        self._sale("414115.00", "2026-09-10")          # новые отгрузки
        self._payment("518985.34", "2026-09-20")       # оплаты

        data = self._summary()

        self.assertEqual(data["opening"]["debit"], "320524.34")
        self.assertEqual(data["opening"]["credit"], "0.00")     # предоплат не было

        self.assertEqual(data["turnover"]["debit"], "414115.00")
        self.assertEqual(data["turnover"]["credit"], "518985.34")

        self.assertEqual(data["closing"]["debit"], "215654.00")  # финальный долг
        self.assertEqual(data["closing"]["credit"], "0.00")      # переплат ни у кого нет

    def test_overpayment_lands_in_closing_credit_only(self):
        self._sale("500.00", "2026-09-05")
        self._payment("800.00", "2026-09-10")

        data = self._summary()
        self.assertEqual(data["closing"]["debit"], "0.00")
        self.assertEqual(data["closing"]["credit"], "300.00")   # мы должны ему товар

    def test_prepayment_before_period_is_opening_credit(self):
        self._payment("700.00", "2026-08-01")   # аванс до начала периода

        data = self._summary()
        self.assertEqual(data["opening"]["debit"], "0.00")
        self.assertEqual(data["opening"]["credit"], "700.00")

    def test_one_overpayment_does_not_hide_another_debt(self):
        """Итог по компании — сумма свёрнутых сальдо, а не свёртка общих сумм."""
        debtor = models.Counterparty.objects.create(
            company=self.company, name="Должник-2", type=models.Counterparty.Type.CLIENT,
        )
        models.Document.objects.create(
            warehouse_from=self.wh, counterparty=debtor,
            doc_type=models.Document.DocType.SALE, status=models.Document.Status.POSTED,
            total=Decimal("1000.00"), date="2026-09-05T12:00:00+06:00",
        )
        # self.cp — переплатил на 300
        self._sale("500.00", "2026-09-05")
        self._payment("800.00", "2026-09-10")

        data = self._summary()
        # Долг 1000 и переплата 300 показываются раздельно, а не как 700.
        self.assertEqual(data["closing"]["debit"], "1000.00")
        self.assertEqual(data["closing"]["credit"], "300.00")

    def test_row_balance_matches_netted_closing(self):
        self._sale("320524.34", "2026-03-15")
        self._sale("414115.00", "2026-09-10")
        self._payment("518985.34", "2026-09-20")

        resp = self.api.get(
            "/api/warehouse/crud/counterparties/",
            {"date_from": "2026-09-01", "date_to": "2026-09-30"},
        )
        debts = next(r for r in (resp.data["results"] if isinstance(resp.data, dict) else resp.data)
                     if r["name"] == "Должник")["analytics"]["debts"]
        self.assertEqual(debts["closing_debit"], "215654.00")
        self.assertEqual(debts["closing_credit"], "0.00")
        self.assertEqual(debts["debt_remaining"], "215654.00")


class TurnoverBalanceSheetAcceptanceTests(CounterpartyDebtColumnsTests):
    """
    Чек-лист приёмки из counterparties-turnover-balance-sheet.md §4.
    Оборотно-сальдовая ведомость: сальдо — остаток, оборот — движения периода.
    """

    def _doc(self, doc_type, total, date, counterparty=None, status=None):
        return models.Document.objects.create(
            warehouse_from=self.wh, counterparty=counterparty or self.cp,
            doc_type=doc_type,
            status=status or models.Document.Status.POSTED,
            total=Decimal(total), date=f"{date}T12:00:00+06:00",
        )

    def _money(self, doc_type, amount, date, counterparty=None, status=None):
        return models.MoneyDocument.objects.create(
            company=self.company, counterparty=counterparty or self.cp,
            doc_type=doc_type,
            status=status or models.MoneyDocument.Status.POSTED,
            amount=Decimal(amount), date=f"{date}T12:00:00+06:00",
        )

    def _row(self, name="Должник", date_from="2026-09-01", date_to="2026-09-30"):
        resp = self.api.get(
            "/api/warehouse/crud/counterparties/",
            {"date_from": date_from, "date_to": date_to, "page_size": "1000"},
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        return next(r for r in rows if r["name"] == name)["analytics"]["debts"]

    # ── только одна сторона сальдо заполнена ──
    def test_only_one_side_of_each_balance_is_filled(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-08-10")
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "2500.00", "2026-09-10")

        d = self._row()
        self.assertEqual(Decimal(d["opening_debit"]) * Decimal(d["opening_credit"]), 0)
        self.assertEqual(Decimal(d["closing_debit"]) * Decimal(d["closing_credit"]), 0)

    # ── инвариант §2.1 ──
    def test_invariant_closing_equals_opening_plus_turnover(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-08-10")
        self._doc(models.Document.DocType.SALE, "300.00", "2026-09-10")
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "450.00", "2026-09-12")

        d = self._row()
        left = Decimal(d["closing_debit"]) - Decimal(d["closing_credit"])
        right = (Decimal(d["opening_debit"]) - Decimal(d["opening_credit"])
                 + Decimal(d["turnover_debit"]) - Decimal(d["turnover_credit"]))
        self.assertEqual(left, right)
        self.assertEqual(left, Decimal("850.00"))  # 1000 + 300 − 450

    # ── границы периода включительно ──
    def test_movements_on_period_edges_land_in_turnover(self):
        self._doc(models.Document.DocType.SALE, "100.00", "2026-09-01")   # первый день
        self._doc(models.Document.DocType.SALE, "200.00", "2026-09-30")   # последний день
        self._doc(models.Document.DocType.SALE, "999.00", "2026-08-31")   # накануне

        d = self._row()
        self.assertEqual(d["turnover_debit"], "300.00")   # 100 + 200
        self.assertEqual(d["opening_debit"], "999.00")    # вчерашний — в сальдо

    # ── возврат товара клиентом уменьшает долг ──
    def test_sale_return_reduces_debt(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-09-05")
        self._doc(models.Document.DocType.SALE_RETURN, "400.00", "2026-09-06")

        d = self._row()
        self.assertEqual(d["turnover_debit"], "1000.00")
        self.assertEqual(d["turnover_credit"], "400.00")
        self.assertEqual(d["closing_debit"], "600.00")

    # ── черновики и отказанные не учитываются ──
    def test_draft_and_rejected_documents_are_ignored(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-09-05")
        self._doc(models.Document.DocType.SALE, "500.00", "2026-09-06",
                  status=models.Document.Status.DRAFT)
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "700.00", "2026-09-07",
                    status=models.MoneyDocument.Status.DRAFT)
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "900.00", "2026-09-08",
                    status=models.MoneyDocument.Status.REJECTED)

        d = self._row()
        self.assertEqual(d["turnover_debit"], "1000.00")
        self.assertEqual(d["turnover_credit"], "0.00")
        self.assertEqual(d["closing_debit"], "1000.00")

    # ── сальдо на конец периода == сальдо на начало следующего ──
    def test_period_shift_is_continuous(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-08-10")
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "250.00", "2026-08-20")

        august = self._row(date_from="2026-08-01", date_to="2026-08-31")
        september = self._row(date_from="2026-09-01", date_to="2026-09-30")

        self.assertEqual(august["closing_debit"], september["opening_debit"])
        self.assertEqual(august["closing_credit"], september["opening_credit"])
        self.assertEqual(september["opening_debit"], "750.00")

    # ── вкладка «Поставщик»: поставка без оплаты ──
    def test_supplier_purchase_without_payment_is_credit(self):
        supplier = models.Counterparty.objects.create(
            company=self.company, name="Поставщик", type=models.Counterparty.Type.SUPPLIER,
        )
        self._doc(models.Document.DocType.PURCHASE, "10000.00", "2026-09-05",
                  counterparty=supplier)

        d = self._row(name="Поставщик")
        self.assertEqual(d["turnover_credit"], "10000.00")
        self.assertEqual(d["closing_credit"], "10000.00")   # мы должны поставщику
        self.assertEqual(d["closing_debit"], "0.00")

        # после оплаты долг гасится
        self._money(models.MoneyDocument.DocType.MONEY_EXPENSE, "10000.00", "2026-09-10",
                    counterparty=supplier)
        d = self._row(name="Поставщик")
        self.assertEqual(d["closing_credit"], "0.00")
        self.assertEqual(d["closing_debit"], "0.00")

    # ── без периода обороты не отдаются ──
    def test_without_dates_turnover_is_not_returned(self):
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-09-05")

        resp = self.api.get("/api/warehouse/crud/counterparties/", {"page_size": "1000"})
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        debts = next(r for r in rows if r["name"] == "Должник")["analytics"]["debts"]
        self.assertNotIn("turnover_debit", debts)
        self.assertNotIn("opening_debit", debts)

    # ── §2.3: итог по списку не сворачивается между контрагентами ──
    def test_summary_sums_sides_without_cross_netting(self):
        debtor = models.Counterparty.objects.create(
            company=self.company, name="Д2", type=models.Counterparty.Type.CLIENT,
        )
        self._doc(models.Document.DocType.SALE, "1000.00", "2026-09-05", counterparty=debtor)
        self._money(models.MoneyDocument.DocType.MONEY_RECEIPT, "400.00", "2026-09-06")

        data = self._summary()
        self.assertEqual(data["closing"]["debit"], "1000.00")
        self.assertEqual(data["closing"]["credit"], "400.00")

        # Итоги повторяют стороны, а не схлопывают их в 600/0.
        totals = data["totals"]
        self.assertEqual(totals["counterparties_owe"], "1000.00")
        self.assertEqual(totals["company_owes"], "400.00")
        self.assertEqual(totals["debt_remaining"], "600.00")
