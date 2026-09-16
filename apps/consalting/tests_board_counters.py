from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import FunnelConsalting, FunnelStageConsalting, LeadConsalting


class BoardCountersTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@counters.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Counters Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.employee = User.objects.create(
            email="emp@counters.com", password="password123", company=self.company
        )

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="Основная воронка", is_main=True)
        self.stage1 = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Первичный контакт", order=1, sla_hours=24
        )
        self.stage2 = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Переговоры", order=2
        )

        # Lead 1: owned by owner, estimated_value=100000, grade=A
        self.lead1 = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage1,
            title="Лид Альфа", owner=self.owner, estimated_value=Decimal("100000.00"), score_grade="A"
        )
        # Lead 2: owned by employee, estimated_value=50000, grade=B
        self.lead2 = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage1,
            title="Лид Бета", owner=self.employee, estimated_value=Decimal("50000.00"), score_grade="B"
        )
        # Lead 3: pool (owner=None), estimated_value=30000, grade=A, stage2, overdue SLA
        self.lead3 = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage1,
            title="Лид Гамма", owner=None, estimated_value=Decimal("30000.00"), score_grade="A",
            stage_entered_at=timezone.now() - timedelta(hours=30)
        )

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.employee)

    def test_manager_board_counters(self):
        res = self.mgr_client.get(f"/api/consalting/funnels/{self.funnel.id}/board/?owner_scope=all")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertIn("scope_counts", data)
        self.assertEqual(data["scope_counts"]["all"], 3)
        self.assertEqual(data["scope_counts"]["mine"], 1)
        self.assertEqual(data["scope_counts"]["pool"], 1)

        self.assertEqual(data["totals"]["count"], 3)
        self.assertEqual(data["totals"]["amount"], 180000.0)

        # Stage 1 column details
        st1_col = next(c for c in data["columns"] if c["stage"]["id"] == str(self.stage1.id))
        self.assertEqual(st1_col["count"], 3)
        self.assertEqual(st1_col["amount"], 180000.0)
        self.assertEqual(st1_col["overdue_count"], 1)

    def test_employee_cannot_see_all_scope(self):
        res = self.emp_client.get(f"/api/consalting/funnels/{self.funnel.id}/board/?owner_scope=all")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertIsNone(data["scope_counts"]["all"])
        self.assertEqual(data["scope_counts"]["mine"], 1)

        # owner_scope=all coerced to mine for employee
        self.assertEqual(data["totals"]["count"], 1)
        self.assertEqual(data["totals"]["amount"], 50000.0)

    def test_filters_affect_counters(self):
        res = self.mgr_client.get(f"/api/consalting/funnels/{self.funnel.id}/board/?owner_scope=all&grade=A")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertEqual(data["scope_counts"]["all"], 2)
        self.assertEqual(data["totals"]["count"], 2)
        self.assertEqual(data["totals"]["amount"], 130000.0)

    def test_main_board_includes_leads_routed_to_regional_funnels(self):
        """The main board is a company-wide view, not a third routing target."""
        regional_funnel = FunnelConsalting.objects.create(
            company=self.company,
            name="Ош",
        )
        regional_stage = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=regional_funnel,
            name="Новая заявка",
            order=1,
            stage_type="new_lead",
        )
        routed_lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=regional_funnel,
            stage=regional_stage,
            title="Лид из Оша",
        )

        res = self.mgr_client.get(f"/api/consalting/funnels/{self.funnel.id}/board/?owner_scope=all")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["totals"]["count"], 4)
        self.assertEqual(res.data["funnel"]["leads_count"], 4)
        intake_column = next(
            column for column in res.data["columns"]
            if column["stage"]["name"] == "Первичный контакт"
        )
        self.assertEqual(intake_column["count"], 4)
        self.assertIn(
            str(routed_lead.id),
            [lead["id"] for lead in intake_column["leads"]],
        )
        routed_lead.refresh_from_db()
        self.assertEqual(routed_lead.funnel_id, regional_funnel.id)

    def test_funnel_list_uses_live_count_for_main_aggregate_board(self):
        """The list badge must include leads routed to regional funnels too."""
        regional_funnel = FunnelConsalting.objects.create(company=self.company, name="Бишкек")
        regional_stage = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=regional_funnel,
            name="Новая заявка",
            order=1,
            stage_type="new_lead",
        )
        LeadConsalting.objects.create(
            company=self.company,
            funnel=regional_funnel,
            stage=regional_stage,
            title="Региональный лид",
        )

        res = self.mgr_client.get("/api/consalting/funnels/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        counts = {item["id"]: item["leads_count"] for item in res.data}
        self.assertEqual(counts[str(self.funnel.id)], 4)
        self.assertEqual(counts[str(regional_funnel.id)], 1)
