"""Тесты для входящих лидов из WhatsApp, системы авто-распределения и API 01-leads.md."""
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import Company, User, CustomRole
from apps.consalting.models import (
    InboundLeadConsalting, LeadDistributionSettingsConsalting, SaleConsalting, ServicesConsalting
)
from apps.consalting.views import distribute_inbound_lead


class InboundLeadDistributionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create(email="owner@test.com", first_name="Owner", is_staff=True, is_superuser=True)
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

        InboundLeadConsalting.objects.create(
            company=self.company, full_name="Busy Lead", owner=self.user_a,
            status=InboundLeadConsalting.Status.IN_WORK
        )

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

        with self.assertRaises(Exception):
            InboundLeadConsalting.objects.create(
                company=self.company, external_id="msg-123", phone="+996700111222", source="whatsapp"
            )


class InboundLeadAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create(email="owner@test.com", first_name="Owner", is_staff=True, is_superuser=True)
        self.company = Company.objects.create(name="TestCompany", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.emp = User.objects.create(
            email="emp@test.com", first_name="Employee", company=self.company, is_active=True
        )

        self.lead_new = InboundLeadConsalting.objects.create(
            company=self.company, full_name="New Lead", phone="+996700000001",
            status=InboundLeadConsalting.Status.NEW, source="whatsapp"
        )
        self.lead_assigned = InboundLeadConsalting.objects.create(
            company=self.company, full_name="Assigned Lead", phone="+996700000002", owner=self.emp,
            status=InboundLeadConsalting.Status.ASSIGNED, source="instagram"
        )
        self.lead_in_work = InboundLeadConsalting.objects.create(
            company=self.company, full_name="In Work Lead", phone="+996700000003", owner=self.emp,
            status=InboundLeadConsalting.Status.IN_WORK, source="whatsapp"
        )

    def test_status_new_assigned_filter(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("inbound-leads-list-create") + "?status=new,assigned"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        statuses = [item["status"] for item in results]
        self.assertIn("new", statuses)
        self.assertIn("assigned", statuses)
        self.assertNotIn("in_work", statuses)

    def test_owner_none_filter(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("inbound-leads-list-create") + "?owner=none"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        for item in results:
            self.assertIsNone(item["owner"])

    def test_employee_visibility_restricted(self):
        self.client.force_authenticate(user=self.emp)
        url = reverse("inbound-leads-list-create") + "?owner=none"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        for item in results:
            self.assertEqual(str(item["owner"]), str(self.emp.id))

    def test_defer_action_validation(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("inbound-leads-defer", kwargs={"pk": self.lead_in_work.id})

        # Past date should fail with 400
        past_time = (timezone.now() - timedelta(hours=2)).isoformat()
        resp = self.client.post(url, {"remind_at": past_time, "reason": "call_later"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # reason="other" without comment should fail with 400
        future_time = (timezone.now() + timedelta(hours=2)).isoformat()
        resp = self.client.post(url, {"remind_at": future_time, "reason": "other", "comment": ""})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid defer request
        resp = self.client.post(url, {"remind_at": future_time, "reason": "call_later", "comment": "Call back"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "deferred")
        self.assertEqual(resp.data["defer_count"], 1)

        # Resume lead
        resume_url = reverse("inbound-leads-resume", kwargs={"pk": self.lead_in_work.id})
        resp_resume = self.client.post(resume_url, {})
        self.assertEqual(resp_resume.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_resume.data["status"], "in_work")
        self.assertEqual(resp_resume.data["defer_count"], 1)

    def test_counters_endpoint(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("inbound-leads-counters")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["all"], 3)
        self.assertEqual(resp.data["new"], 2)  # new + assigned
        self.assertEqual(resp.data["in_work"], 1)

    def test_analytics_endpoint(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("inbound-leads-analytics")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("totals", resp.data)
        self.assertEqual(resp.data["totals"]["leads"], 3)
