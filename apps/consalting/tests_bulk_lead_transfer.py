from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.consalting.models import FunnelConsalting, FunnelStageConsalting, LeadConsalting
from apps.users.models import Company, User


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class BulkLeadOperationContractTests(TestCase):
    """Regression coverage for the one-lead API calls used by the bulk UI."""

    def setUp(self):
        self.owner = User.objects.create(
            email="owner@bulk-contract.test", role="owner", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Bulk contract company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save(update_fields=["company"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

        self.source = FunnelConsalting.objects.create(
            company=self.company, name="Source", is_main=True
        )
        self.source_stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.source, name="Source stage", order=1
        )
        self.target = FunnelConsalting.objects.create(company=self.company, name="Target")
        self.target_first = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.target, name="Target first", order=1
        )
        self.target_second = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.target, name="Target second", order=2
        )
        self.employee = User.objects.create(email="employee@bulk-contract.test", company=self.company)
        self.lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.source, stage=self.source_stage,
            title="Bulk lead", owner=self.owner,
        )

    def test_transfer_defaults_to_first_target_stage_and_preserves_owner(self):
        response = self.client.post(
            f"/api/consalting/leads/{self.lead.id}/transfer/",
            {"target_funnel": str(self.target.id), "target_stage": None},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.funnel_id, self.target.id)
        self.assertEqual(self.lead.stage_id, self.target_first.id)
        self.assertEqual(self.lead.owner_id, self.owner.id)

    def test_transfer_can_change_owner_in_the_same_request(self):
        response = self.client.post(
            f"/api/consalting/leads/{self.lead.id}/transfer/",
            {
                "target_funnel": str(self.target.id),
                "target_stage": str(self.target_second.id),
                "owner": str(self.employee.id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.funnel_id, self.target.id)
        self.assertEqual(self.lead.stage_id, self.target_second.id)
        self.assertEqual(self.lead.owner_id, self.employee.id)

    def test_move_stage_rejects_stage_from_another_funnel(self):
        response = self.client.post(
            f"/api/consalting/leads/{self.lead.id}/move-stage/",
            {"stage": str(self.target_first.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("stage", response.data)

    def test_transfer_rejects_same_funnel_without_owner_change(self):
        response = self.client.post(
            f"/api/consalting/leads/{self.lead.id}/transfer/",
            {"target_funnel": str(self.source.id), "target_stage": None},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("target_funnel", response.data)
