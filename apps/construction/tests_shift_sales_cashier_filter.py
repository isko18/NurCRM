"""
Приёмка фильтра «Кассир» в продажах смены (shift-sales-cashier-filter-backend.md §6).

Смена и касса общие, чеки пробивают несколько сотрудников — фильтр должен резать
по продавцу чека (Sale.user), а не по владельцу смены (CashShift.cashier).
"""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.construction.models import Cashbox, CashShift
from apps.main.models import Branch, Sale
from apps.users.models import Company

User = get_user_model()


class ShiftSalesCashierFilterTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:6]
        self.owner = User.objects.create_user(
            email=f"owner_scf_{suffix}@test.com", password="pass", role="owner"
        )
        self.company = Company.objects.create(name=f"SCF Co {suffix}", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main", company=self.company)
        self.cashbox = Cashbox.objects.create(
            name="Основная", role=Cashbox.CashboxRole.POS_MAIN,
            company=self.company, branch=self.branch,
        )

        # Два тёзки — проверяем, что фильтр разводит их по id, а не по имени (L2/Q2).
        self.anna_a = User.objects.create_user(
            email=f"anna_a_{suffix}@test.com", password="pass", company=self.company,
            first_name="Анна", last_name="Иванова",
        )
        self.anna_b = User.objects.create_user(
            email=f"anna_b_{suffix}@test.com", password="pass", company=self.company,
            first_name="Анна", last_name="Иванова",
        )
        self.idle = User.objects.create_user(
            email=f"idle_{suffix}@test.com", password="pass", company=self.company,
            first_name="Без", last_name="Продаж",
        )

        # Одна общая смена на всех кассиров, владелец смены — отдельный человек.
        self.shift = CashShift.objects.create(
            company=self.company, branch=self.branch, cashbox=self.cashbox,
            cashier=self.owner, opening_cash=Decimal("0.00"), status=CashShift.Status.OPEN,
        )

        self._sale(self.anna_a, "100.00", Sale.PaymentMethod.CASH)
        self._sale(self.anna_a, "150.00", Sale.PaymentMethod.CASH)
        self._sale(self.anna_b, "70.00", Sale.PaymentMethod.TRANSFER)

        self.client_api = APIClient()
        self.client_api.force_authenticate(self.owner)
        self.url = f"/api/construction/cash/shifts/{self.shift.id}/sales/"

    def _sale(self, user, total, payment_method, status=Sale.Status.PAID):
        return Sale.objects.create(
            company=self.company, branch=self.branch, cashbox=self.cashbox,
            shift=self.shift, user=user, total=Decimal(total),
            payment_method=payment_method, status=status,
        )

    # ── §6, кейс 1 ──
    def test_filter_returns_only_that_cashier_sales(self):
        resp = self.client_api.get(self.url, {"cashier": str(self.anna_a.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 2)
        self.assertTrue(
            all(r["cashier_id"] == str(self.anna_a.id) for r in resp.data["results"])
        )

    # ── §6, кейс 2 ──
    def test_cashier_without_sales_returns_empty_not_error(self):
        resp = self.client_api.get(self.url, {"cashier": str(self.idle.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["results"], [])

    # ── §6, кейс 3 ──
    def test_cashier_combines_with_other_filters_as_and(self):
        resp = self.client_api.get(
            self.url, {"cashier": str(self.anna_a.id), "payment_method": "transfer"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)

        resp = self.client_api.get(
            self.url, {"cashier": str(self.anna_a.id), "payment_method": "cash"}
        )
        self.assertEqual(resp.data["count"], 2)

    # ── §6, кейс 4: тёзки ──
    def test_namesakes_are_separated_by_id(self):
        displays = {
            self.client_api.get(self.url, {"cashier": str(u.id)}).data["results"][0]["cashier_display"]
            for u in (self.anna_a, self.anna_b)
        }
        self.assertEqual(displays, {"Анна Иванова"})  # имена совпадают…

        a = self.client_api.get(self.url, {"cashier": str(self.anna_a.id)}).data
        b = self.client_api.get(self.url, {"cashier": str(self.anna_b.id)}).data
        self.assertEqual((a["count"], b["count"]), (2, 1))  # …а выборки разные

    # ── §6, кейс 5: page_size (L1) ──
    def test_page_size_is_client_controlled(self):
        resp = self.client_api.get(self.url, {"page_size": 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 3)
        self.assertEqual(len(resp.data["results"]), 2)
        self.assertIsNotNone(resp.data["next"])

    # ── валидация ──
    def test_invalid_cashier_uuid_returns_400(self):
        resp = self.client_api.get(self.url, {"cashier": "не-uuid"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cashier", resp.data)

    def test_multiple_cashiers_comma_separated(self):
        resp = self.client_api.get(
            self.url, {"cashier": f"{self.anna_a.id},{self.anna_b.id}"}
        )
        self.assertEqual(resp.data["count"], 3)

    # ── §4.2 cashier_breakdown ──
    def test_cashier_breakdown_groups_by_seller_not_shift_owner(self):
        resp = self.client_api.get(f"/api/construction/shifts/{self.shift.id}/")
        self.assertEqual(resp.status_code, 200)
        breakdown = {r["cashier_id"]: r for r in resp.data["cashier_breakdown"]}

        self.assertEqual(set(breakdown), {str(self.anna_a.id), str(self.anna_b.id)})
        self.assertNotIn(str(self.owner.id), breakdown)  # владелец смены не продавал

        row_a = breakdown[str(self.anna_a.id)]
        self.assertEqual(row_a["sales_count"], 2)
        self.assertEqual(row_a["sales_total"], "250.00")
        self.assertEqual(row_a["cashier_display"], "Анна Иванова")

        # отсортировано по убыванию суммы
        self.assertEqual(resp.data["cashier_breakdown"][0]["cashier_id"], str(self.anna_a.id))

    def test_cashier_breakdown_excludes_cancelled_sales(self):
        self._sale(self.anna_b, "999.00", Sale.PaymentMethod.CASH, status=Sale.Status.CANCELED)
        rows = self.shift.calc_cashier_breakdown()
        row_b = next(r for r in rows if r["cashier_id"] == str(self.anna_b.id))
        self.assertEqual(row_b["sales_total"], "70.00")
        self.assertEqual(row_b["sales_count"], 1)


class SharedCashboxWarningTests(TestCase):
    """
    Одна касса — один физический ящик. Если на ней уже открыта чужая смена,
    кассир должен увидеть, чья она и сколько по её данным в ящике (иначе при
    закрытии он введёт чужой остаток и получит расхождение на чужие деньги).
    """

    def setUp(self):
        suffix = uuid.uuid4().hex[:6]
        self.owner = User.objects.create_user(
            email=f"owner_scw_{suffix}@test.com", password="pass", role="owner"
        )
        self.company = Company.objects.create(name=f"SCW Co {suffix}", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main", company=self.company)
        self.shared = Cashbox.objects.create(
            name="Основная касса компании", company=self.company, branch=self.branch,
        )
        self.own = Cashbox.objects.create(
            name="Касса 2 этаж", company=self.company, branch=self.branch,
        )

        self.balnura = User.objects.create_user(
            email=f"balnura_{suffix}@test.com", password="pass", company=self.company,
            first_name="Балнура", last_name="Мыломойка",
        )
        self.second_floor = User.objects.create_user(
            email=f"floor2_{suffix}@test.com", password="pass", company=self.company,
            first_name="2 этаж", last_name="косметика",
        )

        # Реальный ящик: 6738 начальных + продажа наличными 150 = 6888.
        self.balnura_shift = CashShift.objects.create(
            company=self.company, branch=self.branch, cashbox=self.shared,
            cashier=self.balnura, opening_cash=Decimal("6738.00"),
            status=CashShift.Status.OPEN,
        )
        Sale.objects.create(
            company=self.company, branch=self.branch, cashbox=self.shared,
            shift=self.balnura_shift, user=self.balnura, total=Decimal("150.00"),
            payment_method=Sale.PaymentMethod.CASH, status=Sale.Status.PAID,
        )

        self.api = APIClient()
        self.api.force_authenticate(self.second_floor)

    def test_warning_names_other_cashier_and_real_drawer(self):
        resp = self.api.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.shared.id), "opening_cash": "5899.00"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        warning = resp.data["cashbox_warning"]
        self.assertIsNotNone(warning)
        self.assertEqual(warning["code"], "cashbox_has_other_open_shift")
        self.assertIn("Балнура Мыломойка", warning["message"])
        # Именно реальное содержимое ящика, а не opening_cash чужой смены.
        self.assertIn("6888.00", warning["message"])

        other = warning["open_shifts"][0]
        self.assertEqual(other["cashier_id"], str(self.balnura.id))
        self.assertEqual(other["drawer_expected_cash"], "6888.00")
        self.assertEqual(other["opening_cash"], "6738.00")

    def test_no_warning_on_own_free_cashbox(self):
        resp = self.api.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.own.id), "opening_cash": "5899.00"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.data["cashbox_warning"])

    def test_opening_is_not_blocked(self):
        """Мягкий вариант: предупреждаем, но не запрещаем."""
        resp = self.api.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.shared.id), "opening_cash": "5899.00"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            CashShift.objects.filter(cashbox=self.shared, status=CashShift.Status.OPEN).count(), 2
        )

    def test_warning_persists_on_shift_detail(self):
        opened = self.api.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.shared.id), "opening_cash": "5899.00"},
            format="json",
        ).data
        resp = self.api.get(f"/api/construction/shifts/{opened['id']}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["cashbox_warning"]["code"], "cashbox_has_other_open_shift")

    def test_closed_shift_returns_null_warning_key(self):
        opened = self.api.post(
            "/api/construction/shifts/open/",
            {"cashbox": str(self.shared.id), "opening_cash": "5899.00"},
            format="json",
        ).data
        CashShift.objects.filter(id=opened["id"]).update(status=CashShift.Status.CLOSED)
        resp = self.api.get(f"/api/construction/shifts/{opened['id']}/")
        self.assertIn("cashbox_warning", resp.data)   # ключ есть всегда…
        self.assertIsNone(resp.data["cashbox_warning"])  # …но пустой

    def test_closed_shift_has_no_warning(self):
        self.balnura_shift.close(closing_cash=Decimal("6888.00"))
        shift = CashShift.objects.create(
            company=self.company, branch=self.branch, cashbox=self.shared,
            cashier=self.second_floor, opening_cash=Decimal("5899.00"),
            status=CashShift.Status.OPEN,
        )
        self.assertIsNone(shift.cashbox_shared_warning())
