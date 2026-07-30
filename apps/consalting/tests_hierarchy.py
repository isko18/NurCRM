from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    SaleConsalting, LeadFunnelHistoryConsalting, ServicesConsalting
)


class FunnelHierarchyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@hierarchy.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Hierarchy Test Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.employee = User.objects.create(
            email="emp@hierarchy.com", password="password123", company=self.company
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг Услуга", price=Decimal("10000.00")
        )

        # Funnel 2 (Final)
        self.funnel_final = FunnelConsalting.objects.create(
            company=self.company, name="Финансовый отдел", is_final=True
        )
        self.stage_final_new = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_final, name="Оплата", order=1, stage_type="qualification"
        )
        self.stage_final_won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_final, name="Завершено", order=2, stage_type="won"
        )

        # Funnel 1 (Interim) -> points to Funnel 2
        self.funnel_interim = FunnelConsalting.objects.create(
            company=self.company, name="Первичная обработка",
            next_funnel=self.funnel_final, next_stage=self.stage_final_new,
            next_assign="user", next_assign_user=self.employee, is_final=False
        )
        self.stage_interim_new = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_interim, name="Новый", order=1, stage_type="new_lead"
        )
        self.stage_interim_won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_interim, name="Квалифицирован", order=2, stage_type="won"
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_interim_funnel_win_moves_lead_without_sale(self):
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_interim, stage=self.stage_interim_new,
            title="Тестовый лид", service=self.service, owner=self.owner, budget_confirmed=True
        )

        # Win in interim funnel
        res = self.client.post(f"/api/consalting/leads/{lead.id}/win/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        lead.refresh_from_db()
        self.assertEqual(lead.funnel, self.funnel_final)
        self.assertEqual(lead.stage, self.stage_final_new)
        self.assertEqual(lead.owner, self.employee)

        # Sale should NOT be created for interim funnel completion
        self.assertFalse(SaleConsalting.objects.filter(lead=lead).exists())

        # Check funnel history entry
        res_hist = self.client.get(f"/api/consalting/leads/{lead.id}/funnel-history/")
        self.assertEqual(res_hist.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_hist.data["results"]), 1)
        self.assertEqual(res_hist.data["results"][0]["funnel_display"], "Финансовый отдел")

    def test_final_funnel_win_creates_sale(self):
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_final, stage=self.stage_final_new,
            title="Финальный лид", service=self.service, owner=self.owner, budget_confirmed=True
        )

        res = self.client.post(f"/api/consalting/leads/{lead.id}/win/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        lead.refresh_from_db()
        self.assertEqual(lead.status, "won")
        self.assertTrue(SaleConsalting.objects.filter(lead=lead).exists())

    def test_cycle_validation_fails(self):
        # Pointing final funnel back to interim funnel (creating cycle A -> B -> A)
        payload = {
            "next_funnel": str(self.funnel_interim.id)
        }
        res = self.client.patch(f"/api/consalting/funnels/{self.funnel_final.id}/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("next_funnel", res.data)

    def test_is_sla_overdue_flag(self):
        stage_sla = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_final, name="SLA Stage", order=3, sla_hours=2
        )
        old_time = timezone.now() - timedelta(hours=5)
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_final, stage=stage_sla,
            title="Overdue Lead", stage_entered_at=old_time
        )

        res = self.client.get(f"/api/consalting/leads/{lead.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["is_sla_overdue"])
