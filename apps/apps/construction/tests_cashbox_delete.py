import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.construction.auto_cashflow import resolve_cashbox, create_auto_cashflow
from apps.main.models import Branch
from apps.users.models import Company

User = get_user_model()


class CashboxDeleteTests(APITestCase):
    def setUp(self):
        # 1. Владелец
        owner_email = f"owner_{uuid.uuid4().hex[:8]}@test.com"
        self.owner = User.objects.create_user(email=owner_email, password="password", role="owner")
        self.company = Company.objects.create(name=f"Company {uuid.uuid4().hex[:6]}", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        # 2. Кассир
        cashier_email = f"cashier_{uuid.uuid4().hex[:8]}@test.com"
        self.cashier = User.objects.create_user(email=cashier_email, password="password", role="cashier")
        self.cashier.company = self.company
        self.cashier.save()

        # 3. Филиал и кассы
        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        # POS кассы (2 шт, чтобы одна всегда оставалась активной)
        self.pos_1 = Cashbox.objects.create(
            name="Основная касса",
            role=Cashbox.CashboxRole.POS_MAIN,
            company=self.company,
        )
        self.pos_2 = Cashbox.objects.create(
            name="Касса филиала 1",
            role=Cashbox.CashboxRole.POS_BRANCH,
            company=self.company,
            branch=self.branch,
        )

        # Кассы расходов
        self.exp_1 = Cashbox.objects.create(
            name="Расходы 1",
            role=Cashbox.CashboxRole.EXPENSE_VARIABLE,
            is_consumption=True,
            company=self.company,
        )
        self.exp_2 = Cashbox.objects.create(
            name="Расходы 2",
            role=Cashbox.CashboxRole.EXPENSE_VARIABLE,
            is_consumption=True,
            company=self.company,
        )

        # Вторая компания (для проверки изоляции)
        other_owner = User.objects.create_user(email=f"other_{uuid.uuid4().hex[:8]}@test.com", password="password", role="owner")
        self.other_company = Company.objects.create(name=f"Other Company {uuid.uuid4().hex[:6]}", owner=other_owner)
        self.other_cashbox = Cashbox.objects.create(
            name="Чужая касса",
            role=Cashbox.CashboxRole.EXPENSE_VARIABLE,
            company=self.other_company,
        )

    def tearDown(self):
        try:
            self.company.delete()
            self.other_company.delete()
        except Exception:
            pass

    # Scenario 1: DELETE кассы expense_variable без движений и смен -> 204, строка физически удалена
    def test_01_delete_empty_cashbox_physical_delete(self):
        self.client.force_authenticate(user=self.owner)
        cb_id = self.exp_2.id

        res = self.client.delete(f"/api/construction/cashboxes/{cb_id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cashbox.objects.filter(id=cb_id).exists())

    # Scenario 2: DELETE кассы expense_variable с движениями -> 204, is_active=false, CashFlow на месте
    def test_02_delete_cashbox_with_cashflow_archives(self):
        self.client.force_authenticate(user=self.owner)
        cf = CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("500.00"),
            status=CashFlow.Status.APPROVED,
            name="Закупка канцелярии",
        )

        res = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        self.exp_1.refresh_from_db()
        self.assertFalse(self.exp_1.is_active)
        self.assertIsNotNone(self.exp_1.archived_at)
        self.assertEqual(self.exp_1.archived_by, self.owner)
        self.assertTrue(CashFlow.objects.filter(id=cf.id).exists())

    # Scenario 3: Отчет /cashboxes/{id}/report/ по архивной кассе -> работает, суммы прежние, is_active: false
    def test_03_report_on_archived_cashbox(self):
        self.client.force_authenticate(user=self.owner)
        now = timezone.now()
        CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("350.00"),
            status=CashFlow.Status.APPROVED,
            name="Хозрасходы",
            created_at=now,
        )

        # Архивируем
        res_del = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)

        # Отчёт за день
        day_str = now.strftime("%Y-%m-%d")
        res_day = self.client.get(f"/api/construction/cashboxes/{self.exp_1.id}/report/?period=day&date={day_str}")
        self.assertEqual(res_day.status_code, status.HTTP_200_OK)
        self.assertFalse(res_day.data["is_active"])
        self.assertEqual(res_day.data["summary"]["total_expense"], "350.00")

        # Отчёт за месяц
        month_str = now.strftime("%Y-%m")
        res_month = self.client.get(f"/api/construction/cashboxes/{self.exp_1.id}/report/?period=month&month={month_str}")
        self.assertEqual(res_month.status_code, status.HTTP_200_OK)
        self.assertFalse(res_month.data["is_active"])
        self.assertEqual(res_month.data["summary"]["total_expense"], "350.00")

    # Scenario 4: GET /construction/cashboxes/ после архивации -> архивной кассы нет в ответе (и есть при ?include_archived=1)
    def test_04_get_cashboxes_after_archive(self):
        self.client.force_authenticate(user=self.owner)
        CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")

        # По умолчанию без archived
        res = self.client.get("/api/construction/cashboxes/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [cb["id"] for cb in (res.data if isinstance(res.data, list) else res.data.get("results", []))]
        self.assertNotIn(str(self.exp_1.id), ids)

        # С include_archived=1
        res_archived = self.client.get("/api/construction/cashboxes/?include_archived=1")
        self.assertEqual(res_archived.status_code, status.HTTP_200_OK)
        archived_ids = [cb["id"] for cb in (res_archived.data if isinstance(res_archived.data, list) else res_archived.data.get("results", []))]
        self.assertIn(str(self.exp_1.id), archived_ids)

    # Scenario 5: resolve_cashbox для warehouse_purchase после архивации последней expense_variable при AUTO_CASHFLOWS=true -> 409 cashbox_role_required
    def test_05_delete_last_expense_variable_blocks_409(self):
        self.client.force_authenticate(user=self.owner)
        # Удаляем вторую кассу расходов (пустую)
        self.client.delete(f"/api/construction/cashboxes/{self.exp_2.id}/")

        # Пытаемся удалить оставшуюся (последнюю) кассу расходов
        res = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "cashbox_role_required")
        self.exp_1.refresh_from_db()
        self.assertTrue(self.exp_1.is_active)

    # Scenario 6: То же, но есть вторая активная expense_variable -> 204, вторая касса становится целевой
    def test_06_delete_expense_variable_when_second_exists(self):
        self.client.force_authenticate(user=self.owner)
        # Удаляем exp_1 (она пустая) -> 204
        res = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # resolve_cashbox для warehouse_purchase должен резолвить exp_2
        resolved = resolve_cashbox(company=self.company, source_kind="warehouse_purchase")
        self.assertEqual(resolved.id, self.exp_2.id)

    # Scenario 7: DELETE кассы с открытой сменой -> 409 cashbox_has_open_shift
    def test_07_delete_cashbox_with_open_shift_blocks_409(self):
        self.client.force_authenticate(user=self.owner)
        shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.pos_2,
            cashier=self.cashier,
            opening_cash=Decimal("100.00"),
            status=CashShift.Status.OPEN,
        )

        res = self.client.delete(f"/api/construction/cashboxes/{self.pos_2.id}/")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "cashbox_has_open_shift")
        self.pos_2.refresh_from_db()
        self.assertTrue(self.pos_2.is_active)

    # Scenario 8: DELETE кассы с pending движением -> 409 cashbox_has_pending
    def test_08_delete_cashbox_with_pending_blocks_409(self):
        self.client.force_authenticate(user=self.owner)
        CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("150.00"),
            status=CashFlow.Status.PENDING,
            name="Неодобренный расход",
        )

        res = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "cashbox_has_pending")
        self.exp_1.refresh_from_db()
        self.assertTrue(self.exp_1.is_active)

    # Scenario 9: DELETE под ролью кассир/менеджер -> 403
    def test_09_delete_under_cashier_returns_403(self):
        self.client.force_authenticate(user=self.cashier)
        res = self.client.delete(f"/api/construction/cashboxes/{self.exp_2.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.exp_2.refresh_from_db()
        self.assertTrue(self.exp_2.is_active)

    # Scenario 10: DELETE кассы чужой компании -> 404
    def test_10_delete_foreign_cashbox_returns_404(self):
        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(f"/api/construction/cashboxes/{self.other_cashbox.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # Scenario 11: Повторный DELETE уже архивной кассы -> 204 (no-op)
    def test_11_repeated_delete_archived_cashbox_returns_204(self):
        self.client.force_authenticate(user=self.owner)
        CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("200.00"),
            status=CashFlow.Status.APPROVED,
        )
        res1 = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res1.status_code, status.HTTP_204_NO_CONTENT)

        # Повторный DELETE
        res2 = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
        self.assertEqual(res2.status_code, status.HTTP_204_NO_CONTENT)

    # Scenario 12: Явный cashbox_id архивной кассы в авто-операции / ручном cashflow -> 400 cashbox_inactive
    def test_12_explicit_archived_cashbox_returns_400_cashbox_inactive(self):
        self.client.force_authenticate(user=self.owner)
        CashFlow.objects.create(
            company=self.company,
            cashbox=self.exp_1,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("200.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")

        # Ручной cashflow с архивной кассой
        post_res = self.client.post("/api/construction/cashflows/", {
            "cashbox": str(self.exp_1.id),
            "type": "expense",
            "amount": "100.00",
            "name": "Тест",
        })
        self.assertEqual(post_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(post_res.data.get("code"), "cashbox_inactive")

        # Auto cashflow с явным cashbox_id
        from rest_framework.exceptions import ValidationError as DRFValidationError
        with self.assertRaises(DRFValidationError) as ctx:
            resolve_cashbox(company=self.company, cashbox_id=str(self.exp_1.id))
        self.assertEqual(ctx.exception.detail.get("code"), "cashbox_inactive")

    # Scenario 13: AUTO_CASHFLOWS=false у компании, DELETE последней expense_variable -> 204
    def test_13_delete_last_expense_variable_when_auto_cashflows_disabled(self):
        self.client.force_authenticate(user=self.owner)
        # Удаляем exp_2
        self.client.delete(f"/api/construction/cashboxes/{self.exp_2.id}/")

        # Выключаем авто-cashflow для компании (через флаг settings или атрибут)
        with override_settings(AUTO_CASHFLOWS=False):
            res = self.client.delete(f"/api/construction/cashboxes/{self.exp_1.id}/")
            self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
            self.assertFalse(Cashbox.objects.filter(id=self.exp_1.id).exists())
