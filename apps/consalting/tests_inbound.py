"""Тесты для входящих лидов из WhatsApp и системы авто-распределения (§5)."""
from decimal import Decimal
from django.test import TestCase
from apps.users.models import Company, User, CustomRole
from apps.consalting.models import (
    InboundLeadConsalting, LeadDistributionSettingsConsalting,
)
from apps.consalting.views import distribute_inbound_lead


class InboundLeadDistributionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(email="owner@test.com", first_name="Owner")
        self.company = Company.objects.create(name="TestCompany", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.role_manager = CustomRole.objects.create(company=self.company, name="Manager")

        self.user_a = User.objects.create(
            email="user_a@test.com", first_name="User", last_name="A",
            company=self.company, custom_role=self.role_manager, is_active=True
        )
        self.user_b = User.objects.create(
            email="user_b@test.com", first_name="User", last_name="B",
            company=self.company, custom_role=self.role_manager, is_active=True
        )

        self.settings = LeadDistributionSettingsConsalting.objects.create(
            company=self.company,
            enabled=True,
            strategy=LeadDistributionSettingsConsalting.Strategy.ROUND_ROBIN,
        )
        self.settings.roles.add(self.role_manager)

    def test_round_robin_distribution(self):
        # Создаем 2 лида и проверяем распределение поровну между user_a и user_b
        lead1 = InboundLeadConsalting.objects.create(
            company=self.company, full_name="Client 1", phone="+996700111222", source="whatsapp"
        )
        distribute_inbound_lead(lead1)
        lead1.refresh_from_db()

        lead2 = InboundLeadConsalting.objects.create(
            company=self.company, full_name="Client 2", phone="+996700333444", source="whatsapp"
        )
        distribute_inbound_lead(lead2)
        lead2.refresh_from_db()

        owners = {lead1.owner_id, lead2.owner_id}
        self.assertEqual(owners, {self.user_a.id, self.user_b.id})
        self.assertEqual(lead1.status, InboundLeadConsalting.Status.ASSIGNED)
        self.assertEqual(lead2.status, InboundLeadConsalting.Status.ASSIGNED)

    def test_least_loaded_distribution(self):
        self.settings.strategy = LeadDistributionSettingsConsalting.Strategy.LEAST_LOADED
        self.settings.save()

        # Создаем активный лид для user_a
        InboundLeadConsalting.objects.create(
            company=self.company, full_name="Busy Lead", owner=self.user_a,
            status=InboundLeadConsalting.Status.IN_WORK
        )

        # Новый лид должен пойти наименее загруженному user_b
        new_lead = InboundLeadConsalting.objects.create(
            company=self.company, full_name="New Client", source="whatsapp"
        )
        distribute_inbound_lead(new_lead)
        new_lead.refresh_from_db()

        self.assertEqual(new_lead.owner_id, self.user_b.id)

    def test_duplicate_external_id(self):
        lead1 = InboundLeadConsalting.objects.create(
            company=self.company, external_id="msg-123", phone="+996700111222", source="whatsapp"
        )
        self.assertIsNotNone(lead1.id)

        # Повторное сообщение с тем же external_id вызовет ошибку уникальности
        with self.assertRaises(Exception):
            InboundLeadConsalting.objects.create(
                company=self.company, external_id="msg-123", phone="+996700111222", source="whatsapp"
            )
