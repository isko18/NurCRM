from datetime import date, timedelta
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.construction.models import Cashbox, CashFlow
from apps.main.models import (
    Branch,
    Client,
    ClientDeal,
    DealInstallment,
    DealInstallmentHistory,
    DealPayment,
)
from apps.main.tasks import check_overdue_debts
from apps.users.models import Company

User = get_user_model()


class DebtReschedulePromiseTests(TestCase):
    def setUp(self):
        self.api = APIClient()

        email = f"owner_{uuid.uuid4().hex[:8]}@test.com"
        self.user = User.objects.create_user(email=email, password="password123", is_staff=True)
        self.company = Company.objects.create(name=f"Company {uuid.uuid4().hex[:6]}", owner=self.user)
        self.user.company = self.company
        self.user.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.user.branch = self.branch
        self.user.save()

        self.cashbox = Cashbox.objects.create(
            name="POS Cashbox",
            company=self.company,
            branch=self.branch,
            role=Cashbox.CashboxRole.POS_BRANCH,
        )

        self.client_obj = Client.objects.create(
            company=self.company,
            branch=self.branch,
            full_name="Тестовый Покупатель",
            phone="+996555112233",
        )
        self.api.force_authenticate(self.user)

    def create_deal_v2(self, amount="1000.00", due_date=None):
        if due_date is None:
            due_date = timezone.localdate()
        deal = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_obj,
            title="Долг v2",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal(amount),
            debt_days=1,
            schedule_version="v2",
            first_due_date=due_date,
        )
        return deal

    def test_case_1_overdue_and_partial_payment_rules(self):
        """
        Взнос 1000, срок 2 дня назад.
        Оплачено 500: paid_amount=500, paid_on=None, is_overdue=True, overdue_days=2, overdue_amount=500.00.
        """
        two_days_ago = timezone.localdate() - timedelta(days=2)
        deal = self.create_deal_v2(amount="1000.00", due_date=two_days_ago)
        inst = deal.installments.first()
        self.assertIsNotNone(inst)
        self.assertEqual(inst.due_date, two_days_ago)

        pay_url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/pay/"
        res_pay = self.api.post(
            pay_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "installment_id": str(inst.id),
                "amount": "500.00",
                "date": str(timezone.localdate()),
                "payment_method": "cash",
                "cashbox_id": str(self.cashbox.id),
            },
            format="json",
        )
        self.assertEqual(res_pay.status_code, status.HTTP_200_OK)

        inst.refresh_from_db()
        self.assertEqual(inst.paid_amount, Decimal("500.00"))
        self.assertIsNone(inst.paid_on)
        self.assertTrue(inst.is_overdue)
        self.assertEqual(inst.overdue_days, 2)
        self.assertEqual(inst.overdue_amount, Decimal("500.00"))

        # Проверяем сериализацию в ответе
        deal_data = res_pay.data
        inst_data = deal_data["installments"][0]
        self.assertEqual(inst_data["paid_amount"], "500.00")
        self.assertIsNone(inst_data["paid_on"])
        self.assertTrue(inst_data["is_overdue"])
        self.assertEqual(inst_data["overdue_days"], 2)
        self.assertEqual(Decimal(str(inst_data["overdue_amount"])), Decimal("500.00"))
        self.assertIsNone(inst_data["promised_date"])

    def test_case_2_reschedule_due_date(self):
        """
        Перенос due_date на дату в будущем:
        is_overdue=False, overdue_days=0, overdue_amount=0, запись в историю создана.
        """
        two_days_ago = timezone.localdate() - timedelta(days=2)
        deal = self.create_deal_v2(amount="1000.00", due_date=two_days_ago)
        inst = deal.installments.first()

        future_date = timezone.localdate() + timedelta(days=5)
        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res = self.api.patch(
            url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "due_date": str(future_date),
                "note": "Перенесли по просьбе клиента",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        inst.refresh_from_db()
        self.assertEqual(inst.due_date, future_date)
        self.assertFalse(inst.is_overdue)
        self.assertEqual(inst.overdue_days, 0)
        self.assertEqual(inst.overdue_amount, Decimal("0.00"))

        history = DealInstallmentHistory.objects.filter(installment=inst).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_due_date, two_days_ago)
        self.assertEqual(history.new_due_date, future_date)
        self.assertEqual(history.note, "Перенесли по просьбе клиента")
        self.assertEqual(history.created_by, self.user)

    def test_case_3_and_4_promised_date_set_and_clear(self):
        """
        Установка promised_date и последующий сброс promised_date: null.
        """
        two_days_ago = timezone.localdate() - timedelta(days=2)
        deal = self.create_deal_v2(amount="1000.00", due_date=two_days_ago)
        inst = deal.installments.first()

        future_date = timezone.localdate() + timedelta(days=4)
        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"

        # Установка обещания
        res = self.api.patch(url, {"promised_date": str(future_date), "note": "Обещал в пятницу"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        inst.refresh_from_db()
        self.assertEqual(inst.due_date, two_days_ago)
        self.assertTrue(inst.is_overdue)
        self.assertEqual(inst.promised_date, future_date)

        # Снятие обещания (promised_date: null)
        res_clear = self.api.patch(url, {"promised_date": None}, format="json")
        self.assertEqual(res_clear.status_code, status.HTTP_200_OK)
        inst.refresh_from_db()
        self.assertIsNone(inst.promised_date)

    def test_case_5_already_paid_rejection(self):
        """
        PATCH на полностью оплаченный взнос -> 409 installment_already_paid.
        """
        today = timezone.localdate()
        deal = self.create_deal_v2(amount="500.00", due_date=today)
        inst = deal.installments.first()
        inst.paid_amount = Decimal("500.00")
        inst.paid_on = today
        inst.save()

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res = self.api.patch(
            url,
            {"due_date": str(today + timedelta(days=3))},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "installment_already_paid")

    def test_case_6_nothing_to_change(self):
        """
        PATCH без due_date / promised_date / note -> 400 nothing_to_change.
        """
        today = timezone.localdate()
        deal = self.create_deal_v2(amount="500.00", due_date=today)
        inst = deal.installments.first()

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res = self.api.patch(url, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("code"), "nothing_to_change")

    def test_case_7_date_in_past(self):
        """
        PATCH с датой в прошлом -> 400 date_in_past.
        """
        today = timezone.localdate()
        deal = self.create_deal_v2(amount="500.00", due_date=today)
        inst = deal.installments.first()
        yesterday = today - timedelta(days=1)

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res = self.api.patch(url, {"due_date": str(yesterday)}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("code"), "date_in_past")

        res_prom = self.api.patch(url, {"promised_date": str(yesterday)}, format="json")
        self.assertEqual(res_prom.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_prom.data.get("code"), "date_in_past")

    def test_case_8_idempotency(self):
        """
        PATCH с тем же idempotency_key дважды -> один результат, одна запись в истории.
        """
        today = timezone.localdate()
        deal = self.create_deal_v2(amount="500.00", due_date=today)
        inst = deal.installments.first()
        idem = str(uuid.uuid4())
        future = str(today + timedelta(days=7))

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res1 = self.api.patch(url, {"idempotency_key": idem, "due_date": future, "note": "test"}, format="json")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        res2 = self.api.patch(url, {"idempotency_key": idem, "due_date": future, "note": "test"}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)

        self.assertEqual(DealInstallmentHistory.objects.filter(installment=inst).count(), 1)

    def test_case_9_foreign_installment_404(self):
        """
        PATCH взноса другой компании -> 404.
        """
        other_user = User.objects.create_user(email="other@test.com", password="password123")
        other_comp = Company.objects.create(name="Other Co", owner=other_user)
        other_client = Client.objects.create(company=other_comp, full_name="Чужой клиент")
        other_deal = ClientDeal.objects.create(
            company=other_comp,
            client=other_client,
            title="Чужая сделка",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal("100.00"),
            debt_days=1,
            schedule_version="v2",
            first_due_date=timezone.localdate(),
        )
        other_inst = other_deal.installments.first()

        url = f"/main/clients/{self.client_obj.id}/deals/{other_deal.id}/installments/{other_inst.id}/"
        res = self.api.patch(url, {"due_date": str(timezone.localdate() + timedelta(days=2))}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_case_10_deal_v1_rejection(self):
        """
        PATCH для сделки v1 -> 409 deal_not_v2.
        """
        deal_v1 = ClientDeal.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_obj,
            title="Долг v1",
            kind=ClientDeal.Kind.DEBT,
            amount=Decimal("500.00"),
            debt_days=30,
            schedule_version="v1",
            first_due_date=timezone.localdate(),
        )
        inst = deal_v1.installments.first()

        url = f"/main/clients/{self.client_obj.id}/deals/{deal_v1.id}/installments/{inst.id}/"
        res = self.api.patch(url, {"due_date": str(timezone.localdate() + timedelta(days=5))}, format="json")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get("code"), "deal_not_v2")

    def test_case_11_pay_date_in_future_rejection(self):
        """
        pay/ с датой в будущем -> 400.
        """
        deal = self.create_deal_v2(amount="500.00")
        inst = deal.installments.first()
        tomorrow = str(timezone.localdate() + timedelta(days=1))

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/pay/"
        res = self.api.post(
            url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "installment_id": str(inst.id),
                "amount": "100.00",
                "date": tomorrow,
                "cashbox_id": str(self.cashbox.id),
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date", res.data)

    def test_case_12_and_13_pay_and_close_installment(self):
        """
        Оплата остатка взноса закрывает его (paid_on выставлен, remaining_debt=0, is_overdue=False).
        """
        today = timezone.localdate()
        past_date = today - timedelta(days=5)
        deal = self.create_deal_v2(amount="1000.00", due_date=past_date)
        inst = deal.installments.first()

        pay_url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/pay/"
        # Частичная оплата 500 в дату past_date
        self.api.post(
            pay_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "installment_id": str(inst.id),
                "amount": "500.00",
                "date": str(past_date),
                "cashbox_id": str(self.cashbox.id),
            },
            format="json",
        )

        # Перенос срока остатка на сегодня
        new_due = today
        resched_url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res_resched = self.api.patch(resched_url, {"due_date": str(new_due)}, format="json")
        self.assertEqual(res_resched.status_code, status.HTTP_200_OK)

        # Доплата оставшихся 500 на сегодня
        res_final = self.api.post(
            pay_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "installment_id": str(inst.id),
                "amount": "500.00",
                "date": str(new_due),
                "cashbox_id": str(self.cashbox.id),
            },
            format="json",
        )
        self.assertEqual(res_final.status_code, status.HTTP_200_OK)

        inst.refresh_from_db()
        deal.refresh_from_db()
        self.assertEqual(inst.paid_amount, Decimal("1000.00"))
        self.assertEqual(inst.paid_on, new_due)
        self.assertFalse(inst.is_overdue)
        self.assertEqual(deal.remaining_debt, Decimal("0.00"))

    def test_case_14_cron_debt_overdue_and_suppression(self):
        """
        Ночной cron check_overdue_debts:
        - partially paid просроченный взнос отправляет уведомление
        - если promised_date >= today, уведомление подавляется
        """
        past_date = timezone.localdate() - timedelta(days=3)
        deal = self.create_deal_v2(amount="1000.00", due_date=past_date)
        inst = deal.installments.first()
        inst.paid_amount = Decimal("300.00")
        inst.save()

        with patch("apps.main.tasks.notify_debt_overdue") as mock_notify:
            check_overdue_debts()
            mock_notify.assert_called_once_with(inst)

        # Теперь ставим promised_date в будущее
        inst.promised_date = timezone.localdate() + timedelta(days=2)
        inst.save()

        with patch("apps.main.tasks.notify_debt_overdue") as mock_notify:
            check_overdue_debts()
            mock_notify.assert_not_called()

    def test_case_15_no_cashflow_on_reschedule(self):
        """
        Перенос срока / установка обещания НЕ создает кассовых движений (CashFlow).
        """
        cf_count_before = CashFlow.objects.count()
        deal = self.create_deal_v2(amount="1000.00")
        inst = deal.installments.first()

        url = f"/main/clients/{self.client_obj.id}/deals/{deal.id}/installments/{inst.id}/"
        res = self.api.patch(
            url,
            {
                "due_date": str(timezone.localdate() + timedelta(days=10)),
                "promised_date": str(timezone.localdate() + timedelta(days=5)),
                "note": "Без движений денег",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(CashFlow.objects.count(), cf_count_before)
