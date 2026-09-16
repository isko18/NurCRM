from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client, ClientDeal, DealInstallment
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting, SaleConsalting, SaleItemConsalting,
    CashRequestConsalting, CashOperationConsalting, CashConfirmationSettingsConsalting,
    SalarySchemeConsalting, SalaryAccrualConsalting
)
from apps.consalting.funnel.cash_confirmation import confirm_request
from apps.consalting.funnel.sale_cancel import cancel_sale


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class AddonSalesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@addon.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Addon Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        # Настройка кассы: требует подтверждения для cash/transfer
        CashConfirmationSettingsConsalting.objects.create(
            company=self.company,
            mode=CashConfirmationSettingsConsalting.Mode.ALWAYS,
            skip_for_cashier=False,
        )

        # Ставка зарплаты сотрудника: 10%
        SalarySchemeConsalting.objects.create(
            company=self.company,
            user=self.owner,
            percent_enabled=True,
            percent=Decimal("10.00"),
        )

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Действующий Клиент", phone="+77019998877"
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="CRM Бухгалтерия", price=Decimal("150000.00")
        )
        self.tariff = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Месячный", price=Decimal("150000.00"),
            subscription_amount=Decimal("1300.00"), subscription_period="month"
        )

        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Основная воронка", is_final=True, is_main=True
        )
        self.stage_won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Успешно", order=1, stage_type="won"
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_client_addon_sale_basic(self):
        """
        1. POST /consalting/sales/ со свободными позициями (name, price, quantity)
           без services/tariff.
        """
        payload = {
            "client": str(self.client_entity.id),
            "items": [
                {"name": "Умные весы", "price": 3500, "quantity": 1},
                {"name": "Доставка", "price": 300, "quantity": 1}
            ],
            "amount": 3800,
            "payment_mode": "cash",
            "status": "Продажа",
            "kind": "addon",
            "source": "client_card",
            "description": "Умные весы + ещё 1",
        }

        resp = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        sale_id = resp.data["id"]
        sale = SaleConsalting.objects.get(id=sale_id)
        self.assertEqual(sale.kind, "addon")
        self.assertEqual(sale.source, "client_card")
        self.assertEqual(sale.total, Decimal("3800.00"))
        self.assertEqual(sale.status, SaleConsalting.Status.PENDING_CONFIRMATION)

        # Строки SaleItem
        items = list(sale.items.order_by("created_at"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].name, "Умные весы")
        self.assertEqual(items[0].price, Decimal("3500.00"))
        self.assertEqual(items[0].quantity, 1)
        self.assertEqual(items[1].name, "Доставка")
        self.assertEqual(items[1].price, Decimal("300.00"))
        self.assertEqual(items[1].quantity, 1)

        # Кассовый запрос
        cash_req = CashRequestConsalting.objects.filter(sale=sale).first()
        self.assertIsNotNone(cash_req)
        self.assertEqual(cash_req.amount, Decimal("3800.00"))
        self.assertEqual(cash_req.status, CashRequestConsalting.Status.PENDING)

        # Сделка клиента создана и привязана
        self.assertIsNotNone(sale.deal)
        self.assertEqual(sale.deal.kind, ClientDeal.Kind.SALE)
        self.assertEqual(sale.deal.amount, Decimal("3800.00"))

        # Подтверждение кассиром
        confirm_request(cash_req, user=self.owner)
        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleConsalting.Status.COMPLETED)

        # Зарплата начислена (10% от 3800 = 380)
        accrual = SalaryAccrualConsalting.objects.filter(sale=sale, user=self.owner).first()
        self.assertIsNotNone(accrual)
        self.assertEqual(accrual.amount, Decimal("380.00"))

    def test_client_addon_sale_validation_errors(self):
        """
        Проверка валидации:
        - 400 при отсутствии client
        - 404 если client не найден
        - 400 если пустой items
        - 400 если позиция без названия
        - 400 если сумма <= 0
        """
        # Без клиента
        resp = self.client.post("/api/consalting/sales/", {
            "items": [{"name": "Товар", "price": 100, "quantity": 1}],
            "amount": 100
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Несуществующий клиент
        import uuid
        resp = self.client.post("/api/consalting/sales/", {
            "client": str(uuid.uuid4()),
            "items": [{"name": "Товар", "price": 100, "quantity": 1}],
            "amount": 100
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

        # Пустой items
        resp = self.client.post("/api/consalting/sales/", {
            "client": str(self.client_entity.id),
            "items": [],
            "amount": 100
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Позиция без имени
        resp = self.client.post("/api/consalting/sales/", {
            "client": str(self.client_entity.id),
            "items": [{"name": "", "price": 100, "quantity": 1}],
            "amount": 100
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Сумма <= 0
        resp = self.client.post("/api/consalting/sales/", {
            "client": str(self.client_entity.id),
            "items": [{"name": "Товар", "price": 0, "quantity": 1}],
            "amount": 0
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_client_addon_sale_idempotency(self):
        """
        Повторный запрос с тем же idempotency_key возвращает 200 и ту же продажу.
        """
        payload = {
            "client": str(self.client_entity.id),
            "items": [{"name": "Товар", "price": 1500, "quantity": 2}],
            "amount": 3000,
            "idempotency_key": "test-idem-key-999",
        }
        resp1 = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        sale_id1 = resp1.data["id"]

        resp2 = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data["id"], sale_id1)

        # Убедимся, что продажа только одна
        self.assertEqual(SaleConsalting.objects.filter(idempotency_key="test-idem-key-999").count(), 1)

    def test_client_addon_sale_installment(self):
        """
        Рассрочка на 3 месяца:
        - Создаётся ClientDeal(kind=DEBT) с графиком из 3 платежей.
        - Первый платёж уходит в CashRequestConsalting.
        - При подтверждении кассы первый платёж помечается оплаченным.
        """
        payload = {
            "client": str(self.client_entity.id),
            "items": [{"name": "Оборудование", "price": 9000, "quantity": 1}],
            "amount": 9000,
            "payment_mode": "installment",
            "debt_months": 3,
            "kind": "addon",
        }
        resp = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        sale = SaleConsalting.objects.get(id=resp.data["id"])
        self.assertEqual(sale.payment_mode, "installment")
        self.assertEqual(sale.debt_months, 3)

        deal = sale.deal
        self.assertIsNotNone(deal)
        self.assertEqual(deal.kind, ClientDeal.Kind.DEBT)
        self.assertEqual(deal.installments.count(), 3)

        first_inst = deal.installments.order_by("number").first()
        self.assertEqual(first_inst.amount, Decimal("3000.00"))

        cash_req = CashRequestConsalting.objects.filter(sale=sale).first()
        self.assertIsNotNone(cash_req)
        self.assertEqual(cash_req.amount, Decimal("3000.00"))

        # Подтверждаем первый платёж
        confirm_request(cash_req, user=self.owner)
        first_inst.refresh_from_db()
        self.assertIsNotNone(first_inst.paid_on)
        self.assertEqual(first_inst.paid_amount, Decimal("3000.00"))

    def test_client_addon_sale_cancellation(self):
        """
        Отмена доп. продажи откатывает кассу и зарплату.
        """
        payload = {
            "client": str(self.client_entity.id),
            "items": [{"name": "Услуга", "price": 5000, "quantity": 1}],
            "amount": 5000,
            "payment_mode": "cash",
            "kind": "addon",
        }
        resp = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        sale = SaleConsalting.objects.get(id=resp.data["id"])
        cash_req = CashRequestConsalting.objects.get(sale=sale)
        confirm_request(cash_req, user=self.owner)

        # Отменяем продажу
        cancel_resp = self.client.post(f"/api/consalting/sales/{sale.id}/cancel/", {
            "reason": "client_refused",
            "comment": "Клиент передумал",
            "refund_mode": "cash"
        }, format="json")
        self.assertEqual(cancel_resp.status_code, status.HTTP_200_OK)

        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleConsalting.Status.CANCELED)

        # Начисление зарплаты отменено
        accrual = SalaryAccrualConsalting.objects.get(sale=sale)
        self.assertEqual(accrual.status, "canceled")

    def test_lead_register_payment_modes_with_items(self):
        """
        Тестирование второго входа (§6.2):
        Mode 1: N=1, разовые позиции -> разовый платёж, график не создаётся.
        Mode 2: N=3, subscription_autorenew=False -> items не входят в sub_amount.
        Mode 3: N=2, subscription_autorenew=True -> items входят в sub_amount.
        """
        # Mode 1: Разовые
        lead1 = LeadConsalting.objects.create(
            company=self.company, client=self.client_entity, title="Лид 1",
            funnel=self.funnel, stage=self.stage_won, service=self.service
        )
        resp1 = self.client.post(f"/api/consalting/leads/{lead1.id}/register-payment/", {
            "amount": "4000",
            "payment_mode": "cash",
            "paid_months": 1,
            "items": [{"name": "Доставка", "price": 1000, "quantity": 1}],
        }, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        sale1 = SaleConsalting.objects.get(lead=lead1)
        self.assertEqual(sale1.items.count(), 1)
        self.assertFalse(sale1.subscriptions.exists())

        # Mode 2: Фикс. график N=3, subscription_autorenew=False
        lead2 = LeadConsalting.objects.create(
            company=self.company, client=self.client_entity, title="Лид 2",
            funnel=self.funnel, stage=self.stage_won, service=self.service
        )
        # base = 6000 (2000/мес), items = 3800 -> amount = 9800
        resp2 = self.client.post(f"/api/consalting/leads/{lead2.id}/register-payment/", {
            "amount": "9800",
            "payment_mode": "cash",
            "paid_months": 3,
            "subscription_autorenew": False,
            "items": [{"name": "Весы", "price": 3800, "quantity": 1}],
        }, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        sale2 = SaleConsalting.objects.get(lead=lead2)
        self.assertEqual(sale2.subscription_amount, Decimal("2000.00"))
        sub2 = sale2.subscriptions.first()
        self.assertIsNotNone(sub2)
        self.assertEqual(sub2.amount, Decimal("2000.00"))
        self.assertFalse(sub2.autorenew)
        self.assertEqual(sub2.payments.count(), 3)

        # Mode 3: Подписка N=2, subscription_autorenew=True
        # тариф base = 1300/мес, items = 1200 + 1250 = 2450 -> sub_amount = 3750, amount = 7500
        lead3 = LeadConsalting.objects.create(
            company=self.company, client=self.client_entity, title="Лид 3",
            funnel=self.funnel, stage=self.stage_won, service=self.service, tariff=self.tariff
        )
        resp3 = self.client.post(f"/api/consalting/leads/{lead3.id}/register-payment/", {
            "amount": "7500",
            "payment_mode": "cash",
            "subscription_enabled": True,
            "subscription_autorenew": True,
            "subscription_prepaid_periods": 2,
            "items": [
                {"name": "Модуль 1", "price": 1200, "quantity": 1},
                {"name": "Модуль 2", "price": 1250, "quantity": 1},
            ],
        }, format="json")
        self.assertEqual(resp3.status_code, status.HTTP_201_CREATED)
        sale3 = SaleConsalting.objects.get(lead=lead3)
        self.assertEqual(sale3.subscription_amount, Decimal("3750.00"))
        sub3 = sale3.subscriptions.first()
        self.assertIsNotNone(sub3)
        self.assertEqual(sub3.amount, Decimal("3750.00"))
        self.assertTrue(sub3.autorenew)
        self.assertEqual(sub3.payments.count(), 12)

    def test_client_addon_sale_analytics_slice(self):
        """
        Проверка среза доп. продаж в аналитике.
        """
        # Создаем завершенную доп. продажу
        payload = {
            "client": str(self.client_entity.id),
            "items": [{"name": "Курс", "price": 4500, "quantity": 1}],
            "amount": 4500,
            "payment_mode": "cash",
            "kind": "addon",
            "source": "client_card",
        }
        resp = self.client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sale = SaleConsalting.objects.get(id=resp.data["id"])
        cash_req = CashRequestConsalting.objects.get(sale=sale)
        confirm_request(cash_req, user=self.owner)

        analytics_resp = self.client.get("/api/consalting/sales/analytics/")
        self.assertEqual(analytics_resp.status_code, status.HTTP_200_OK)
        kpis = analytics_resp.data.get("kpis", {})
        self.assertIn("addon_revenue", kpis)
        self.assertGreaterEqual(kpis["addon_revenue"], 4500.0)
        self.assertGreaterEqual(kpis["addon_count"], 1)

        by_kind = analytics_resp.data.get("by_kind", [])
        addon_kind_entry = next((k for k in by_kind if k["kind"] == "addon"), None)
        self.assertIsNotNone(addon_kind_entry)
        self.assertGreaterEqual(addon_kind_entry["revenue"], 4500.0)

    def test_seller_isolation(self):
        """
        Salesperson не может оформить продажу на чужого клиента.
        """
        seller1 = User.objects.create(
            email="seller1@addon.com", password="password123", company=self.company
        )
        seller2 = User.objects.create(
            email="seller2@addon.com", password="password123", company=self.company
        )
        # Клиент закреплен за seller2
        client_of_seller2 = Client.objects.create(
            company=self.company, full_name="Клиент Продавца 2", salesperson=seller2
        )

        # seller1 пытается создать продажу на клиента seller2
        seller1_client = APIClient()
        seller1_client.force_authenticate(user=seller1)

        payload = {
            "client": str(client_of_seller2.id),
            "items": [{"name": "Товар", "price": 1000, "quantity": 1}],
            "amount": 1000,
        }
        resp = seller1_client.post("/api/consalting/sales/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

