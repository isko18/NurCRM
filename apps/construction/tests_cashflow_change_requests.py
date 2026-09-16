import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.main.models import Branch
from apps.users.models import Company

User = get_user_model()


class CashFlowChangeRequestsTests(APITestCase):
    def setUp(self):
        # 1. Владелец
        owner_email = f"owner_{uuid.uuid4().hex[:8]}@test.com"
        self.owner = User.objects.create_user(email=owner_email, password="password", role="owner")
        self.company = Company.objects.create(
            name=f"Company {uuid.uuid4().hex[:6]}",
            owner=self.owner,
            cashflow_requests_enabled=True,
        )
        self.owner.company = self.company
        self.owner.save()

        # 2. Кассир
        cashier_email = f"cashier_{uuid.uuid4().hex[:8]}@test.com"
        self.cashier = User.objects.create_user(email=cashier_email, password="password", role="cashier")
        self.cashier.company = self.company
        self.cashier.save()

        # 3. Филиал и кассы
        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.cashbox_a = Cashbox.objects.create(
            name="Касса А",
            role=Cashbox.CashboxRole.POS_BRANCH,
            company=self.company,
            branch=self.branch,
        )
        self.cashbox_b = Cashbox.objects.create(
            name="Касса Б",
            role=Cashbox.CashboxRole.POS_BRANCH,
            company=self.company,
            branch=self.branch,
        )

        # 4. Открытая смена на кассе А
        self.shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            cashier=self.cashier,
            opening_cash=Decimal("100.00"),
            status=CashShift.Status.OPEN,
        )

    def tearDown(self):
        try:
            self.company.delete()
            self.owner.delete()
            self.cashier.delete()
        except Exception:
            pass

    def test_scenario_1_edit_request_amount_approve(self):
        """
        Сценарий 1: edit-request суммы 1000->820 по approved expense, затем approve
        -> target_flow.amount=820, баланс кассы +180, заявка approved
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            name="Закупка воды",
            amount=Decimal("1000.00"),
            status=CashFlow.Status.APPROVED,
        )
        # Исходный баланс кассы: -1000.00
        self.client.force_authenticate(user=self.owner)
        cb_res = self.client.get(f"/api/construction/cashboxes/{self.cashbox_a.id}/")
        self.assertEqual(cb_res.data["balance"], "-1000.00")

        # Создаем edit-request
        payload = {
            "proposed": {
                "name": "Закупка воды (исправлено)",
                "amount": "820.00",
                "type": "expense",
            },
            "reason": "Ошиблись суммой при вводе",
            "idempotency_key": f"edit-{target.id}",
        }
        res = self.client.post(f"/api/construction/cashflows/{target.id}/edit-request/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        req_id = res.data["id"]
        self.assertEqual(res.data["status"], "pending")
        self.assertEqual(res.data["request_kind"], "edit")
        self.assertEqual(res.data["target_flow"]["id"], str(target.id))
        self.assertEqual(res.data["proposed"]["amount"], "820.00")

        # Одобряем заявку
        patch_res = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        target.refresh_from_db()
        self.assertEqual(target.amount, Decimal("820.00"))
        self.assertEqual(target.name, "Закупка воды (исправлено)")

        # Баланс кассы стал -820.00 (дельта +180.00)
        cb_res2 = self.client.get(f"/api/construction/cashboxes/{self.cashbox_a.id}/")
        self.assertEqual(cb_res2.data["balance"], "-820.00")

    def test_scenario_2_edit_request_reject(self):
        """
        Сценарий 2: edit-request, затем reject
        -> target_flow без изменений; заявка rejected
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            name="Расход на канцтовары",
            amount=Decimal("500.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "300.00"}},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        req_id = res.data["id"]

        patch_res = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "rejected"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        target.refresh_from_db()
        self.assertEqual(target.amount, Decimal("500.00"))
        req_obj = CashFlow.objects.get(id=req_id)
        self.assertEqual(req_obj.status, CashFlow.Status.REJECTED)

    def test_scenario_3_edit_request_change_type_expense_to_income(self):
        """
        Сценарий 3: edit-request со сменой type expense->income
        -> после approve знак движения развернут
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("200.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"type": "income", "amount": "200.00"}},
            format="json",
        )
        req_id = res.data["id"]

        self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        target.refresh_from_db()
        self.assertEqual(target.type, CashFlow.Type.INCOME)
        cb_res = self.client.get(f"/api/construction/cashboxes/{self.cashbox_a.id}/")
        self.assertEqual(cb_res.data["balance"], "200.00")

    def test_scenario_4_cancel_request_income_approve(self):
        """
        Сценарий 4: cancel-request по approved income 500, approve
        -> income уходит из ленты/отчета; баланс кассы -500
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            amount=Decimal("500.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        # До отмены баланс +500.00
        cb_res = self.client.get(f"/api/construction/cashboxes/{self.cashbox_a.id}/")
        self.assertEqual(cb_res.data["balance"], "500.00")

        # Создаем заявку на отмену
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/cancel-request/",
            {"reason": "Ошибочный чек"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        req_id = res.data["id"]
        self.assertEqual(res.data["request_kind"], "cancel")

        # Одобряем отмену
        patch_res = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        target.refresh_from_db()
        self.assertEqual(target.status, CashFlow.Status.REJECTED)

        # Баланс кассы стал 0.00 (дельта -500)
        cb_res2 = self.client.get(f"/api/construction/cashboxes/{self.cashbox_a.id}/")
        self.assertEqual(cb_res2.data["balance"], "0.00")

        # В ленте approved движений target_flow больше нет
        feed_res = self.client.get(f"/api/construction/cashflows/?cashbox={self.cashbox_a.id}&status=approved")
        feed_ids = [item["id"] for item in feed_res.data.get("results", [])]
        self.assertNotIn(str(target.id), feed_ids)

    def test_scenario_5_double_edit_request_returns_409(self):
        """
        Сценарий 5: два edit-request подряд по одному target_flow -> второй 409 existing_request_id
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res1 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "120.00"}},
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        first_req_id = res1.data["id"]

        # Второй запрос
        res2 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "130.00"}},
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data["existing_request_id"], first_req_id)

    def test_scenario_6_idempotent_post_returns_200_same_request(self):
        """
        Сценарий 6: повтор POST edit-request с тем же idempotency_key -> 200, та же заявка, без дубля
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        key = f"idemp-{uuid.uuid4()}"
        res1 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "120.00"}, "idempotency_key": key},
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        req_id1 = res1.data["id"]

        # Повтор с тем же ключом
        res2 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "120.00"}, "idempotency_key": key},
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["id"], req_id1)

    def test_scenario_7_approve_edit_when_target_flow_rejected_returns_409(self):
        """
        Сценарий 7: approve edit, когда target_flow уже rejected -> 400/409
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "120.00"}},
            format="json",
        )
        req_id = res.data["id"]

        # Целевое движение отклонили другим процессом
        target.status = CashFlow.Status.REJECTED
        target.save()

        patch_res = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scenario_8_cashier_can_create_request_but_cannot_approve_403(self):
        """
        Сценарий 8: кассир создает cancel-request (201), но не может одобрить (403); owner одобряет
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            amount=Decimal("300.00"),
            status=CashFlow.Status.APPROVED,
        )
        # Кассир создает заявку
        self.client.force_authenticate(user=self.cashier)
        res = self.client.post(f"/api/construction/cashflows/{target.id}/cancel-request/", {"reason": "Ошибка кассы"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        req_id = res.data["id"]

        # Кассир пытается одобрить -> 403
        patch_cashier = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        self.assertEqual(patch_cashier.status_code, status.HTTP_403_FORBIDDEN)

        # Владелец одобряет -> 200
        self.client.force_authenticate(user=self.owner)
        patch_owner = self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")
        self.assertEqual(patch_owner.status_code, status.HTTP_200_OK)

    def test_scenario_9_request_for_cashbox_a_not_in_cashbox_b(self):
        """
        Сценарий 9: заявка по кассе А не видна в списке ?cashbox=Б
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            amount=Decimal("300.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(f"/api/construction/cashflows/{target.id}/cancel-request/", {}, format="json")
        req_id = res.data["id"]

        # Запрос списка pending по кассе Б
        res_b = self.client.get(f"/api/construction/cashflows/?cashbox={self.cashbox_b.id}&status=pending")
        ids_b = [x["id"] for x in res_b.data.get("results", [])]
        self.assertNotIn(req_id, ids_b)

        # Запрос списка pending по кассе А
        res_a = self.client.get(f"/api/construction/cashflows/?cashbox={self.cashbox_a.id}&status=pending")
        ids_a = [x["id"] for x in res_a.data.get("results", [])]
        self.assertIn(req_id, ids_a)

    def test_scenario_10_edit_request_invalid_amount_returns_400(self):
        """
        Сценарий 10: edit-request с amount=0 или отрицательным -> 400
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res1 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "0.00"}},
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)

        res2 = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "-15.00"}},
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scenario_11_cancel_drawer_affecting_flow_decreases_shift_expected_cash(self):
        """
        Сценарий 11: cancel движения с affects_shift_drawer=true в открытой смене
        -> drawer_expected_cash уменьшен на amount
        """
        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            shift=self.shift,
            cashier=self.cashier,
            type=CashFlow.Type.INCOME,
            amount=Decimal("400.00"),
            affects_shift_drawer=True,
            status=CashFlow.Status.APPROVED,
        )
        # Начальная сумма смены 100 + приход 400 = 500
        self.assertEqual(self.shift.drawer_expected_cash, Decimal("500.00"))

        # Создаем и одобряем cancel
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(f"/api/construction/cashflows/{target.id}/cancel-request/", {}, format="json")
        req_id = res.data["id"]
        self.client.patch(f"/api/construction/cashflows/{req_id}/", {"status": "approved"}, format="json")

        # Теперь drawer_expected_cash вернулся к 100.00
        self.assertEqual(self.shift.drawer_expected_cash, Decimal("100.00"))

    def test_scenario_12_bulk_approve_normal_edit_cancel(self):
        """
        Сценарий 12: bulk approve: 1 обычная операция + 1 edit + 1 cancel
        -> все три применяются корректно, атомарно по каждой
        """
        # 1. Обычная pending операция
        normal = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            amount=Decimal("150.00"),
            status=CashFlow.Status.PENDING,
        )

        # 2. Approved операция для edit
        target_edit = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("300.00"),
            status=CashFlow.Status.APPROVED,
        )
        edit_req = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("250.00"),
            request_kind=CashFlow.RequestKind.EDIT,
            target_flow=target_edit,
            proposed={"amount": "250.00"},
            status=CashFlow.Status.PENDING,
        )

        # 3. Approved операция для cancel
        target_cancel = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            amount=Decimal("200.00"),
            status=CashFlow.Status.APPROVED,
        )
        cancel_req = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("200.00"),
            request_kind=CashFlow.RequestKind.CANCEL,
            target_flow=target_cancel,
            status=CashFlow.Status.PENDING,
        )

        self.client.force_authenticate(user=self.owner)
        bulk_payload = {
            "items": [
                {"id": str(normal.id), "status": "approved"},
                {"id": str(edit_req.id), "status": "approved"},
                {"id": str(cancel_req.id), "status": "approved"},
            ]
        }
        res = self.client.patch("/api/construction/cashflows/bulk/status/", bulk_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 3)

        normal.refresh_from_db()
        self.assertEqual(normal.status, CashFlow.Status.APPROVED)

        target_edit.refresh_from_db()
        self.assertEqual(target_edit.amount, Decimal("250.00"))

        target_cancel.refresh_from_db()
        self.assertEqual(target_cancel.status, CashFlow.Status.REJECTED)

    def test_closed_shift_returns_422(self):
        """
        R6: Правки/отмены по движениям из закрытой смены возвращают 422
        """
        closed_shift = CashShift.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            cashier=self.cashier,
            status=CashShift.Status.CLOSED,
        )
        flow = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            shift=closed_shift,
            cashier=self.cashier,
            type=CashFlow.Type.EXPENSE,
            amount=Decimal("100.00"),
            status=CashFlow.Status.APPROVED,
        )
        self.client.force_authenticate(user=self.owner)
        res_edit = self.client.post(
            f"/api/construction/cashflows/{flow.id}/edit-request/",
            {"proposed": {"amount": "80.00"}},
            format="json",
        )
        self.assertEqual(res_edit.status_code, 422)

        res_cancel = self.client.post(
            f"/api/construction/cashflows/{flow.id}/cancel-request/",
            {},
            format="json",
        )
        self.assertEqual(res_cancel.status_code, 422)

    def test_requests_disabled_owner_direct_edit(self):
        """Когда cashflow_requests_enabled=False, owner редактирует сразу без создания заявки."""
        self.company.cashflow_requests_enabled = False
        self.company.save()

        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            name="Старое название",
            amount=Decimal("500.00"),
            status=CashFlow.Status.APPROVED,
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {
                "proposed": {
                    "name": "Новое название",
                    "amount": "450.00",
                    "type": "expense",
                }
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertEqual(target.name, "Новое название")
        self.assertEqual(target.amount, Decimal("450.00"))
        # Заявка не создаётся
        self.assertFalse(CashFlow.objects.filter(target_flow=target).exists())

    def test_requests_disabled_owner_direct_cancel(self):
        """Когда cashflow_requests_enabled=False, owner отменяет сразу без создания заявки."""
        self.company.cashflow_requests_enabled = False
        self.company.save()

        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.INCOME,
            name="Ошибочный приход",
            amount=Decimal("300.00"),
            status=CashFlow.Status.APPROVED,
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f"/api/construction/cashflows/{target.id}/cancel-request/",
            {},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertEqual(target.status, CashFlow.Status.REJECTED)
        self.assertFalse(CashFlow.objects.filter(target_flow=target).exists())

    def test_requests_disabled_cashier_forbidden(self):
        """Когда cashflow_requests_enabled=False, кассир не может вызывать edit/cancel (403)."""
        self.company.cashflow_requests_enabled = False
        self.company.save()

        target = CashFlow.objects.create(
            company=self.company,
            branch=self.branch,
            cashbox=self.cashbox_a,
            type=CashFlow.Type.EXPENSE,
            name="Расход",
            amount=Decimal("200.00"),
            status=CashFlow.Status.APPROVED,
        )

        self.client.force_authenticate(user=self.cashier)
        res_edit = self.client.post(
            f"/api/construction/cashflows/{target.id}/edit-request/",
            {"proposed": {"amount": "150.00"}},
            format="json",
        )
        self.assertEqual(res_edit.status_code, status.HTTP_403_FORBIDDEN)

        res_cancel = self.client.post(
            f"/api/construction/cashflows/{target.id}/cancel-request/",
            {},
            format="json",
        )
        self.assertEqual(res_cancel.status_code, status.HTTP_403_FORBIDDEN)

    def test_manual_cashflow_status_with_requests_toggle(self):
        """Ручные движения не-владельца одобряются сразу если toggle=False, иначе pending."""
        # 1. toggle = False (default)
        self.company.cashflow_requests_enabled = False
        self.company.save()

        self.client.force_authenticate(user=self.cashier)
        res_cashier = self.client.post(
            "/api/construction/cashflows/",
            {
                "cashbox": str(self.cashbox_a.id),
                "type": "expense",
                "name": "Расход кассира",
                "amount": "100.00",
            },
            format="json",
        )
        self.assertEqual(res_cashier.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_cashier.data["status"], "approved")

        # 2. toggle = True
        self.company.cashflow_requests_enabled = True
        self.company.save()

        res_cashier_pending = self.client.post(
            "/api/construction/cashflows/",
            {
                "cashbox": str(self.cashbox_a.id),
                "type": "expense",
                "name": "Расход кассира 2",
                "amount": "150.00",
            },
            format="json",
        )
        self.assertEqual(res_cashier_pending.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_cashier_pending.data["status"], "pending")


