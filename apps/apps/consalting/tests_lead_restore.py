from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import FunnelConsalting, FunnelStageConsalting, LeadConsalting


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class LeadRestoreTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="restore_owner@test.com",
            password="password123",
            role="owner",
            is_active=True,
        )
        self.company = Company.objects.create(name="Restore Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.employee_no_access = User.objects.create_user(
            email="employee_noaccess@test.com",
            password="password123",
            company=self.company,
            role="employee",
            is_active=True,
        )

        self.funnel = FunnelConsalting.objects.create(
            company=self.company,
            name="Main Sales Funnel",
        )
        self.stage1 = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            name="Новый",
            order=1,
            stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
        )
        self.stage2 = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            name="Квалификация",
            order=2,
            stage_type=FunnelStageConsalting.StageType.QUALIFICATION,
        )
        self.stage_terminal = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            name="Завершено",
            order=3,
            system_key="completed",
            stage_type=FunnelStageConsalting.StageType.COMPLETED,
            is_final=True,
        )

        self.other_funnel = FunnelConsalting.objects.create(
            company=self.company,
            name="Other Funnel",
        )
        self.other_stage = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=self.other_funnel,
            name="Other Stage",
            order=1,
        )

    def _url(self, name, **kwargs):
        path = reverse(name, kwargs=kwargs)
        if not path.startswith("/api"):
            path = f"/api{path}"
        return path

    def test_restore_lead_without_stage(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Архивный Лид 1",
            status=LeadConsalting.Status.WON,
            is_archived=True,
            archived_at=timezone.now(),
        )

        self.client.force_authenticate(user=self.owner)
        url = self._url("leads-restore", pk=lead.id)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        lead.refresh_from_db()
        self.assertFalse(lead.is_archived)
        self.assertIsNone(lead.archived_at)
        self.assertEqual(lead.status, LeadConsalting.Status.IN_WORK)
        self.assertEqual(lead.stage, self.stage1)

    def test_restore_lead_with_valid_stage(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Архивный Лид 2",
            status=LeadConsalting.Status.WON,
            is_archived=True,
            archived_at=timezone.now(),
        )

        self.client.force_authenticate(user=self.owner)
        url = self._url("leads-restore", pk=lead.id)
        res = self.client.post(url, {"stage": str(self.stage2.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        lead.refresh_from_db()
        self.assertFalse(lead.is_archived)
        self.assertEqual(lead.stage, self.stage2)

    def test_restore_lead_terminal_or_foreign_stage(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Архивный Лид 3",
            status=LeadConsalting.Status.WON,
            is_archived=True,
            archived_at=timezone.now(),
        )

        self.client.force_authenticate(user=self.owner)
        url = self._url("leads-restore", pk=lead.id)
        res1 = self.client.post(url, {"stage": str(self.stage_terminal.id)})
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)

        res2 = self.client.post(url, {"stage": str(self.other_stage.id)})
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_restore_already_active_lead(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage1,
            full_name="Активный Лид",
            status=LeadConsalting.Status.IN_WORK,
            is_archived=False,
        )

        self.client.force_authenticate(user=self.owner)
        url = self._url("leads-restore", pk=lead.id)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Лид не в архиве", res.data.get("detail", ""))

    def test_restore_lead_permission_denied(self):
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Архивный Лид 4",
            is_archived=True,
        )

        self.client.force_authenticate(user=self.employee_no_access)
        url = self._url("leads-restore", pk=lead.id)
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_archived_list_contains_won_and_lost(self):
        lead_won = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Won Lead",
            status=LeadConsalting.Status.WON,
            is_archived=True,
            archived_at=timezone.now(),
        )
        lead_lost = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage_terminal,
            full_name="Lost Lead",
            status=LeadConsalting.Status.LOST,
            is_archived=True,
            archived_at=timezone.now(),
        )

        self.client.force_authenticate(user=self.owner)
        url = self._url("leads-archived")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        results = res.data.get("results", res.data)
        ids = [item["id"] for item in results]
        self.assertIn(str(lead_won.id), ids)
        self.assertIn(str(lead_lost.id), ids)
