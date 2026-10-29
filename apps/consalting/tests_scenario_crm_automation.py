import uuid
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    EmployeeFunnelGrant,
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting, SaleConsalting,
    CashRequestConsalting, CashOperationConsalting,
    RegionalFunnelRoutingConsalting, RegionalFunnelRuleConsalting,
    CashConfirmationSettingsConsalting
)
from apps.consalting.funnel.regional_routing import resolve_funnel_and_assignee


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class ScenarioCRMAutomationTests(TestCase):
    """
    Сквозной интеграционный тест сценария автоматизации CRM (scenario-crm-automation.md).
    Проверяет цепочку:
    Inbound -> Региональная маршрутизация -> register-payment -> Дедуп лид->клиент
    -> Переход во Внедрение -> Изоляция продавца Б -> Подтверждение кассиром ->
    Создание CRM-аккаунта (Tenant) -> Аналитика KPI.
    """
    def setUp(self):
        # 1. Компания консалтинга и владелец
        self.owner = User.objects.create(
            email="owner@automation.kg", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Nur Consulting HQ", owner=self.owner)
        self.owner.company = self.company
        self.owner.role = "owner"
        self.owner.save()

        # Настройки кассы: только наличные требуют подтверждения
        self.cash_settings = CashConfirmationSettingsConsalting.objects.create(
            company=self.company, mode=CashConfirmationSettingsConsalting.Mode.ALWAYS
        )

        # 2. Сотрудники: Менеджер Бишкека (А), Менеджер Оша (Б), Кассир
        self.seller_a = User.objects.create(
            email="seller_a@automation.kg", password="password123", company=self.company,
            consulting_region_codes=["bishkek"],
        )
        self.seller_a.role = "salesperson"
        self.seller_a.save()

        self.seller_b = User.objects.create(
            email="seller_b@automation.kg", password="password123", company=self.company,
            consulting_region_codes=["osh"],
        )
        self.seller_b.role = "salesperson"
        self.seller_b.save()

        self.cashier = User.objects.create(
            email="cashier@automation.kg", password="password123", company=self.company
        )
        self.cashier.role = "admin"
        self.cashier.save()

        # 3. Воронки: Воронка внедрения (финальная) и Воронка Бишкек (первичная)
        self.onboarding_funnel = FunnelConsalting.objects.create(
            company=self.company, name="Внедрение CRM", is_final=True
        )
        self.onboarding_stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.onboarding_funnel, name="Старт внедрения", order=1
        )

        self.bishkek_funnel = FunnelConsalting.objects.create(
            company=self.company, name="Продажи Бишкек",
            next_funnel=self.onboarding_funnel,
            next_stage=self.onboarding_stage,
            next_assign=FunnelConsalting.NextAssign.KEEP,
            is_final=False
        )
        self.bishkek_stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.bishkek_funnel, name="Первичный контакт", order=1
        )

        # 4. Региональная маршрутизация
        self.routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True, fallback_strategy="default_funnel",
            default_funnel=self.bishkek_funnel
        )
        self.rule_bishkek = RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing,
            region_code="bishkek",
            funnel=self.bishkek_funnel,
            phone_prefixes=["+996555", "+996312"],
            assign_role_ids=["salesperson"],
            assign_strategy="round_robin",
        )

        EmployeeFunnelGrant.objects.create(
            employee=self.seller_a,
            funnel=self.bishkek_funnel,
            can_manage_leads=True,
        )

        # 5. Услуга и тариф с созданием CRM-аккаунта
        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Внедрение CRM NUR", price=Decimal("60000.00")
        )
        self.tariff = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Бизнес",
            price=Decimal("60000.00"), subscription_amount=Decimal("5000.00"),
            subscription_period="month", provisions_crm_account=True
        )

        # Клиенты API
        self.client_seller_a = APIClient()
        self.client_seller_a.force_authenticate(user=self.seller_a)

        self.client_seller_b = APIClient()
        self.client_seller_b.force_authenticate(user=self.seller_b)

        self.client_cashier = APIClient()
        self.client_cashier.force_authenticate(user=self.cashier)

    def test_full_scenario_crm_automation(self):
        """
        Полный сквозной прогон сценария scenario-crm-automation.md:
        1. Inbound WhatsApp -> Бишкек воронка + продавец А
        2. Продавец А вызывает register-payment
        3. Лид автоматически создает Клиента и переходит во Внедрение
        4. Продавец Б не видит продажу Продавца А
        5. Кассир подтверждает заявку кассы -> Tenant CRM-аккаунт создается
        6. Дашборд аналитики отображает выручку и оплату
        """
        # --- ШАГ 1: Inbound WhatsApp -> Маршрутизация ---
        inbound_phone = "+996 555 12-34-56"
        inbound_email = "newclient@kgcompany.kg"

        funnel, stage, rule, assigned_user = resolve_funnel_and_assignee(
            self.company, phone=inbound_phone, source="whatsapp"
        )
        self.assertEqual(funnel.id, self.bishkek_funnel.id)
        self.assertEqual(assigned_user.id, self.seller_a.id)

        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=funnel,
            stage=stage,
            owner=assigned_user,
            title="Заявка из WhatsApp",
            phone=inbound_phone,
            email=inbound_email,
        )

        # --- ШАГ 2: Продавец А регистрирует оплату (register-payment) ---
        res_pay = self.client_seller_a.post(f"/api/consalting/leads/{lead.id}/register-payment/", {
            "payment_mode": "cash",
            "amount": "60000.00",
            "services": str(self.service.id),
            "tariff": str(self.tariff.id),
            "subscription_enabled": True,
            "subscription_amount": "5000.00",
            "subscription_period": "month",
        })
        self.assertEqual(res_pay.status_code, status.HTTP_201_CREATED)

        # --- ШАГ 3: Проверка автоконверсии лида и переноса во Внедрение ---
        lead.refresh_from_db()
        self.assertIsNotNone(lead.client_id)
        client = lead.client
        self.assertEqual(client.email, inbound_email)
        # Нормализованный телефон E.164
        self.assertEqual(client.phone, "+996555123456")

        # Лид перемещён воронку Внедрение CRM
        self.assertEqual(lead.funnel_id, self.onboarding_funnel.id)
        self.assertEqual(lead.stage_id, self.onboarding_stage.id)
        self.assertTrue(lead.payment_registered)

        # Продажа создана со статусом pending_confirmation
        sale = SaleConsalting.objects.filter(lead=lead).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.status, SaleConsalting.Status.PENDING_CONFIRMATION)
        self.assertEqual(sale.total, Decimal("60000.00"))

        # Кассовая заявка создана
        req = CashRequestConsalting.objects.filter(sale=sale).first()
        self.assertIsNotNone(req)
        self.assertEqual(req.status, CashRequestConsalting.Status.PENDING)
        self.assertEqual(req.amount, Decimal("60000.00"))

        # --- ШАГ 4: Проверка изоляции прав (Продавец Б) ---
        # Продавец Б пытается открыть продажу Продавца А -> 404 Not Found
        res_b_detail = self.client_seller_b.get(f"/api/consalting/sales/{sale.id}/")
        self.assertEqual(res_b_detail.status_code, status.HTTP_404_NOT_FOUND)

        # В списке продаж Продавца Б пусто
        res_b_list = self.client_seller_b.get("/api/consalting/sales/")
        self.assertEqual(res_b_list.status_code, status.HTTP_200_OK)
        sales_data = res_b_list.data.get("results", res_b_list.data) if isinstance(res_b_list.data, dict) else res_b_list.data
        self.assertEqual(len(sales_data), 0)

        # В списке продаж Продавца А сделка отображается
        res_a_list = self.client_seller_a.get("/api/consalting/sales/")
        self.assertEqual(res_a_list.status_code, status.HTTP_200_OK)
        sales_a_data = res_a_list.data.get("results", res_a_list.data) if isinstance(res_a_list.data, dict) else res_a_list.data
        self.assertEqual(len(sales_a_data), 1)

        # --- ШАГ 5: Кассир подтверждает заявку кассы ---
        res_confirm = self.client_cashier.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/", {
            "comment": "Оплата принята в кассу Бишкек"
        })
        self.assertEqual(res_confirm.status_code, status.HTTP_200_OK)

        req.refresh_from_db()
        sale.refresh_from_db()
        self.assertEqual(req.status, CashRequestConsalting.Status.CONFIRMED)
        self.assertEqual(sale.status, SaleConsalting.Status.COMPLETED)

        # Создана подтверждённая операция кассы
        op = CashOperationConsalting.objects.filter(sale=sale).first()
        self.assertIsNotNone(op)
        self.assertEqual(op.direction, CashOperationConsalting.Direction.INCOME)
        self.assertEqual(op.amount, Decimal("60000.00"))

        # Tenant (CRM-аккаунт) успешно создан
        client.refresh_from_db()
        self.assertEqual(client.provision_status, "created")
        self.assertIsNotNone(client.nur_company_id)
        # Создана CRM-компания клиента
        tenant_company = Company.objects.filter(id=client.nur_company_id).first()
        self.assertIsNotNone(tenant_company)
        tenant_owner = User.objects.filter(email=inbound_email).first()
        self.assertIsNotNone(tenant_owner)
        self.assertEqual(tenant_company.owner_id, tenant_owner.id)

        # --- ШАГ 6: Аналитика KPI дашборда ---
        res_dash = self.client_cashier.get("/api/consalting/analytics/dashboard/")
        self.assertEqual(res_dash.status_code, status.HTTP_200_OK)
        kpis = res_dash.data["kpis"]

        # Выручка зафиксирована
        self.assertEqual(kpis["revenue"]["current"], 60000.0)
        # Оплаченный доход
        self.assertEqual(kpis["paid_income"]["current"], 60000.0)
        # Заявок в ожидании больше нет (0.0)
        self.assertEqual(kpis["pending_cash"]["current"], 0.0)
