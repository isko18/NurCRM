from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, TariffConsalting
)
from apps.consalting.funnel.lead_conversion import (
    normalize_phone_kg, resolve_client_from_lead, merge_lead_into_client, find_client_duplicates
)


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class LeadClientConversionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@leadconv.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Lead Conv Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Автоматизация бизнеса", price=Decimal("120000.00")
        )
        self.tariff = TariffConsalting.objects.create(
            company=self.company, service=self.service, name="Тариф Базовый",
            price=Decimal("120000.00"), subscription_amount=Decimal("5000.00"),
            subscription_period="month"
        )
        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Продажи", is_final=True, is_main=True
        )
        self.stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Квалификация", order=1
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_phone_normalization_kg(self):
        self.assertEqual(normalize_phone_kg("0555 12 34 56"), "+996555123456")
        self.assertEqual(normalize_phone_kg("8555123456"), "+996555123456")
        self.assertEqual(normalize_phone_kg("+996 555 12-34-56"), "+996555123456")
        self.assertEqual(normalize_phone_kg("555123456"), "+996555123456")
        self.assertEqual(normalize_phone_kg("+77011234567"), "+77011234567")

    def test_resolve_client_creates_new_client(self):
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Новый лид", full_name="Азамат Бакиров", phone="0555112233",
            email="azamat@example.com", service=self.service
        )
        client = resolve_client_from_lead(lead, user=self.owner)
        self.assertIsNotNone(client)
        self.assertEqual(client.full_name, "Азамат Бакиров")
        self.assertEqual(client.phone, "+996555112233")
        self.assertEqual(client.email, "azamat@example.com")

        lead.refresh_from_db()
        self.assertEqual(lead.client_id, client.id)

    def test_resolve_client_deduplicates_by_phone(self):
        existing_client = Client.objects.create(
            company=self.company, full_name="Существующий Клиент", phone="+996555998877"
        )
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид повторный", phone="0555 99 88 77"
        )
        client, merged, warning = resolve_client_from_lead(lead, user=self.owner, return_meta=True)
        self.assertEqual(client.id, existing_client.id)
        self.assertTrue(merged)
        self.assertIn("Существующий Клиент", warning)

        lead.refresh_from_db()
        self.assertEqual(lead.client_id, existing_client.id)

    def test_resolve_client_deduplicates_by_email(self):
        existing_client = Client.objects.create(
            company=self.company, full_name="Клиент по почте", email="repeat@example.com"
        )
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид без телефона", email="REPEAT@EXAMPLE.COM"
        )
        client, merged, warning = resolve_client_from_lead(lead, user=self.owner, return_meta=True)
        self.assertEqual(client.id, existing_client.id)
        self.assertTrue(merged)

    def test_merge_lead_into_client_preserves_existing_data(self):
        existing_client = Client.objects.create(
            company=self.company, full_name="Главный Клиент", phone="+996555000111", email=""
        )
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид", full_name="Другое Имя", phone="0555 00 01 11",
            email="added@example.com"
        )
        merged_client = merge_lead_into_client(existing_client, lead)
        # full_name не затирается, email дополняется
        self.assertEqual(merged_client.full_name, "Главный Клиент")
        self.assertEqual(merged_client.email, "added@example.com")

    def test_lead_register_payment_auto_resolves_client(self):
        """register-payment автоматически создает клиента, если у лида его еще не было."""
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид для оплаты", phone="+996777123456", estimated_value=Decimal("50000.00")
        )
        res = self.client.post(f"/api/consalting/leads/{lead.id}/register-payment/", {
            "payment_mode": "cash",
            "amount": "50000.00"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        lead.refresh_from_db()
        self.assertIsNotNone(lead.client_id)
        self.assertEqual(lead.client.phone, "+996777123456")

    def test_lead_create_client_api_endpoint(self):
        """POST /api/consalting/leads/{id}/create-client/ создает клиента и возвращает статус."""
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид из формы", phone="0700123456", full_name="Нурбек"
        )
        res = self.client.post(f"/api/consalting/leads/{lead.id}/create-client/", {
            "email": "nurbek@test.kg"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertFalse(res.data["merged"])
        self.assertEqual(res.data["client_display"], "Нурбек")

    def test_clients_lookup_api_endpoint(self):
        """GET /api/consalting/clients/lookup/ находит дубликаты."""
        Client.objects.create(
            company=self.company, full_name="Клиент Поиска", phone="+996555443322", email="find@test.kg"
        )
        res = self.client.get("/api/consalting/clients/lookup/?phone=0555443322")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["matches"]), 1)
        self.assertEqual(res.data["matches"][0]["full_name"], "Клиент Поиска")
