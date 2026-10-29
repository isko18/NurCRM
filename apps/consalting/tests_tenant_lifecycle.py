from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting, SaleConsalting,
    SubscriptionConsalting, SubscriptionPaymentConsalting,
    CashRequestConsalting, TenantSubscriptionExtension
)
from apps.consalting.funnel.completion import create_sale_side_effects
from apps.consalting.funnel.cash_confirmation import confirm_request
from apps.consalting.funnel.tenant_lifecycle import provision_tenant_account, extend_tenant_subscription


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class TenantLifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@tenantnur.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Nur Consulting Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp = User.objects.create(
            email="emp@tenantnur.com", password="password123", company=self.company
        )

        self.client_entity = Client.objects.create(
            company=self.company, full_name="ОсОО Ромашка", email="client@romashka.kg"
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Внедрение CRM", price=Decimal("150000.00")
        )

        self.tariff = TariffConsalting.objects.create(
            company=self.company,
            service=self.service,
            name="CRM Стандарт",
            price=Decimal("150000.00"),
            subscription_amount=Decimal("5000.00"),
            subscription_period="month",
            provisions_crm_account=True,
            initial_access_days=30
        )

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.emp)

    def test_sale_confirm_auto_provisions_crm_account(self):
        """Подтверждение продажи с тарифом provisions_crm_account создает Company + User."""
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity,
            total=Decimal("150000.00"), status="pending_confirmation"
        )
        req = CashRequestConsalting.objects.create(
            company=self.company, sale=sale, user=self.emp, client=self.client_entity,
            kind="sale", direction="income", amount=Decimal("150000.00"),
            payment_method="cash", status="pending"
        )

        res = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.client_entity.refresh_from_db()
        self.assertEqual(self.client_entity.provision_status, "created")
        self.assertIsNotNone(self.client_entity.nur_company)
        self.assertEqual(self.client_entity.nur_company.name, "ОсОО Ромашка")
        self.assertEqual(self.client_entity.nur_company.owner.email, "client@romashka.kg")
        self.assertIsNotNone(self.client_entity.provisioned_at)

    def test_provision_tenant_account_idempotency(self):
        """Повторный вызов provision_tenant_account возвращает skipped=True без ошибок."""
        res1 = provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        self.assertFalse(res1.skipped)
        self.assertIsNotNone(res1.company_id)

        res2 = provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        self.assertTrue(res2.skipped)
        self.assertEqual(res1.company_id, res2.company_id)

    def test_provision_missing_email_fails(self):
        """Клиент без email получает provision_status=failed."""
        client_no_email = Client.objects.create(
            company=self.company, full_name="Без Почты"
        )
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=client_no_email, total=Decimal("100000.00")
        )
        req = CashRequestConsalting.objects.create(
            company=self.company, sale=sale, user=self.emp, client=client_no_email,
            kind="sale", direction="income", amount=Decimal("100000.00"), status="pending"
        )

        res = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        client_no_email.refresh_from_db()
        self.assertEqual(client_no_email.provision_status, "failed")
        self.assertIn("email", client_no_email.provision_error.lower())

    def test_subscription_payment_confirm_extends_tenant_subscription(self):
        """Подтверждение абонплаты продлевает end_date CRM-компании клиента."""
        # 1. Сначала создаем tenant-аккаунт
        provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        self.client_entity.refresh_from_db()
        tenant_company = self.client_entity.nur_company
        initial_end_date = timezone.localdate(tenant_company.end_date)

        # 2. Создаем абонентскую подписку и плановый платеж
        sale = SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity,
            subscription_amount=Decimal("5000.00"), subscription_period="month"
        )
        sub = create_sale_side_effects(sale)
        payment = sub.payments.first()

        # 3. Создаем заявку кассы на абонплату и подтверждаем
        req = CashRequestConsalting.objects.create(
            company=self.company, user=self.emp, client=self.client_entity,
            subscription_payment=payment,
            kind="subscription", direction="income", amount=Decimal("5000.00"), status="pending"
        )
        res = self.mgr_client.post(f"/api/consalting/cashbox/requests/{req.id}/confirm/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        tenant_company.refresh_from_db()
        new_end = timezone.localdate(tenant_company.end_date)
        self.assertGreater(new_end, initial_end_date)

        # Проверяем запись в журнале продлений
        ext = TenantSubscriptionExtension.objects.filter(consalting_client=self.client_entity).first()
        self.assertIsNotNone(ext)
        self.assertEqual(ext.new_end_date, new_end)

    def test_client_tenant_account_get_endpoint(self):
        """GET /api/consalting/clients/{id}/tenant-account/ возвращает статус и данные аккаунта."""
        provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        res = self.mgr_client.get(f"/api/consalting/clients/{self.client_entity.id}/tenant-account/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["provision_status"], "created")
        self.assertEqual(res.data["company_name"], "ОсОО Ромашка")
        self.assertEqual(res.data["owner_email"], "client@romashka.kg")

    def test_client_provision_tenant_post_endpoint(self):
        """POST /api/consalting/clients/{id}/provision-tenant/ создает аккаунт вручную."""
        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity, total=Decimal("150000.00")
        )

        res = self.mgr_client.post(f"/api/consalting/clients/{self.client_entity.id}/provision-tenant/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["provision_status"], "created")
        self.assertIsNotNone(res.data.get("generated_password"))

    def test_client_provision_tenant_non_admin_forbidden(self):
        """Обычный сотрудник получает 403 при вызове provision-tenant."""
        res = self.emp_client.post(f"/api/consalting/clients/{self.client_entity.id}/provision-tenant/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_lead_serializer_tenant_provision_status(self):
        """LeadConsaltingSerializer корректно отдает tenant_provision_status (§10.6)."""
        funnel = FunnelConsalting.objects.create(company=self.company, name="Воронка", is_final=True)
        stage = FunnelStageConsalting.objects.create(company=self.company, funnel=funnel, name="Новый", order=1)
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=funnel, stage=stage, client=self.client_entity,
            title="Тестовый лид для CRM"
        )
        self.client_entity.provision_status = Client.ProvisionStatus.CREATED
        self.client_entity.save()

        res = self.mgr_client.get(f"/api/consalting/leads/{lead.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["tenant_provision_status"], "created")
        self.assertEqual(res.data["tenant_provision_status_display"], "Аккаунт создан")

    def test_provision_creates_cashbox_with_is_active_true(self):
        """Провижн компании создает 'Основная касса компании' с is_active=True (ТЗ 11 Задача A)."""
        from apps.construction.models import Cashbox
        res = provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        self.assertFalse(res.skipped)
        new_company = Company.objects.get(id=res.company_id)
        cashboxes = Cashbox.objects.filter(company=new_company)
        self.assertEqual(cashboxes.count(), 1)
        cb = cashboxes.first()
        self.assertEqual(cb.name, "Основная касса компании")
        self.assertTrue(cb.is_active)

    def test_provision_default_sector_market(self):
        """Тариф без crm_sector_id получает сектор 'Маркет' / 'Магазин' по умолчанию (ТЗ 11 Задача B)."""
        from apps.users.models import Sector
        market_sector, _ = Sector.objects.get_or_create(name="Магазин")
        res = provision_tenant_account(
            client=self.client_entity,
            tariff=self.tariff,
            actor=self.owner
        )
        self.assertFalse(res.skipped)
        new_company = Company.objects.get(id=res.company_id)
        self.assertEqual(new_company.sector_id, market_sector.id)

    def test_provision_tenant_crm_sector_override_and_invalid(self):
        """Override crm_sector в POST /provision-tenant/ и валидация несуществующего сектора."""
        from apps.users.models import Sector
        custom_sector, _ = Sector.objects.get_or_create(name="Кафе")
        SaleConsalting.objects.create(
            company=self.company, user=self.emp, services=self.service,
            tariff=self.tariff, client=self.client_entity, total=Decimal("150000.00")
        )

        # 1. Несуществующий сектор -> 400
        res_bad = self.mgr_client.post(
            f"/api/consalting/clients/{self.client_entity.id}/provision-tenant/",
            {"crm_sector": "00000000-0000-0000-0000-000000000000"},
            format="json"
        )
        self.assertEqual(res_bad.status_code, status.HTTP_400_BAD_REQUEST)

        # 2. Корректный override -> компания создается с выбранным сектором
        res_ok = self.mgr_client.post(
            f"/api/consalting/clients/{self.client_entity.id}/provision-tenant/",
            {"crm_sector": str(custom_sector.id)},
            format="json"
        )
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ok.data["provision_status"], "created")
        self.client_entity.refresh_from_db()
        self.assertEqual(self.client_entity.nur_company.sector_id, custom_sector.id)

    def test_tenant_account_get_shows_expected_sector_before_provision(self):
        """GET /tenant-account/ для непровижненного клиента отдает сектор по умолчанию."""
        from apps.users.models import Sector
        market_sector, _ = Sector.objects.get_or_create(name="Магазин")
        res = self.mgr_client.get(f"/api/consalting/clients/{self.client_entity.id}/tenant-account/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data["sector"])
        self.assertEqual(res.data["sector"]["id"], str(market_sector.id))
        self.assertEqual(res.data["sector"]["name"], "Магазин")

    def _existing_tenant(self, email="existing@romashka.kg"):
        """A direct NurCRM registration, not created through consulting."""
        from apps.users.models import Sector
        owner = User.objects.create_user(email=email, password="password123", role="owner")
        sector = Sector.objects.create(name="Кафе")
        company = Company.objects.create(name="Существующая компания", owner=owner, sector=sector)
        owner.company = company
        owner.save(update_fields=["company"])
        return company

    def test_tenant_account_lookup_finds_existing_direct_registration(self):
        company = self._existing_tenant()
        response = self.mgr_client.get("/api/consalting/tenant-accounts/lookup/?email=EXISTING@ROMASHKA.KG")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["match"]["nur_company_id"], str(company.id))
        self.assertEqual(response.data["match"]["company_name"], company.name)

    def test_tenant_account_lookup_returns_null_when_missing(self):
        response = self.mgr_client.get("/api/consalting/tenant-accounts/lookup/?email=missing@example.kg")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["match"])

    def test_link_existing_tenant_is_idempotent_and_does_not_change_owner(self):
        tenant = self._existing_tenant()
        url = f"/api/consalting/clients/{self.client_entity.id}/link-tenant/"
        response = self.mgr_client.post(url, {"nur_company_id": str(tenant.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["nur_company_id"], str(tenant.id))
        self.assertEqual(tenant.owner.email, "existing@romashka.kg")

        response = self.mgr_client.post(url, {"nur_company_id": str(tenant.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_link_existing_tenant_rejects_link_used_by_another_client(self):
        tenant = self._existing_tenant()
        another_client = Client.objects.create(company=self.company, full_name="Другой", email="another@example.kg")
        another_client.nur_company = tenant
        another_client.save(update_fields=["nur_company"])
        response = self.mgr_client.post(
            f"/api/consalting/clients/{self.client_entity.id}/link-tenant/",
            {"nur_company_id": str(tenant.id)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_provision_email_collision_returns_existing_company(self):
        tenant = self._existing_tenant(email=self.client_entity.email)
        response = self.mgr_client.post(
            f"/api/consalting/clients/{self.client_entity.id}/provision-tenant/", format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["existing_company"]["nur_company_id"], str(tenant.id))
