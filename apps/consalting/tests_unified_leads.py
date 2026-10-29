import uuid
from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting, InboundLeadConsalting, LossReasonConsalting
)


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False, APPEND_SLASH=False)
class UnifiedLeadsTests(TestCase):
    def setUp(self):
        email = f"owner_{uuid.uuid4().hex[:8]}@unifiedleads.com"
        self.owner = User.objects.create(
            email=email, password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name=f"Unified Leads Company {uuid.uuid4().hex[:4]}", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Продажи", is_final=True, is_main=True
        )
        self.stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Квалификация", order=1
        )
        self.stage_won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Успех", order=10, stage_type="won"
        )
        self.stage_lost = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Отказ", order=11, stage_type="lost"
        )

        self.client = APIClient(secure=True)
        self.client.force_authenticate(user=self.owner)

    def tearDown(self):
        LeadConsalting.objects.filter(company=self.company).delete()
        InboundLeadConsalting.objects.filter(company=self.company).delete()
        FunnelStageConsalting.objects.filter(company=self.company).delete()
        FunnelConsalting.objects.filter(company=self.company).delete()
        if hasattr(self.company, 'loss_reasons'):
            self.company.loss_reasons.all().delete()
        self.company.delete()
        self.owner.delete()

    def test_create_lead_via_leads_api(self):
        payload = {
            "title": "Тестовый лид",
            "full_name": "Иван Иванов",
            "phone": "+996555112233",
            "description": "Заявка с сайта",
            "channel": "site",
            "funnel": str(self.funnel.id),
            "stage": str(self.stage.id),
        }
        res = self.client.post("/consalting/leads/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["queue_status"], "new")
        self.assertEqual(res.data["channel"], "site")

        # Verify InboundLeadConsalting log entry was created
        ib = InboundLeadConsalting.objects.filter(lead_id=res.data["id"]).first()
        self.assertIsNotNone(ib)
        self.assertEqual(ib.full_name, "Иван Иванов")

    def test_lead_defer_and_resume(self):
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Лид для откладывания", phone="+996555223344", queue_status="new"
        )
        # Defer
        defer_res = self.client.post(
            f"/consalting/leads/{lead.id}/defer/",
            {"remind_at": "2026-09-15T10:00:00Z", "reason": "call_later", "comment": "Занят до 15-го"},
            format="json"
        )
        self.assertEqual(defer_res.status_code, status.HTTP_200_OK)
        self.assertEqual(defer_res.data["queue_status"], "deferred")
        self.assertEqual(defer_res.data["defer_reason"], "call_later")
        self.assertEqual(defer_res.data["defer_reason_display"], "Просил перезвонить позже")

        # Resume
        resume_res = self.client.post(f"/consalting/leads/{lead.id}/resume/")
        self.assertEqual(resume_res.status_code, status.HTTP_200_OK)
        self.assertEqual(resume_res.data["queue_status"], "in_work")
        self.assertIsNone(resume_res.data["remind_at"])

    def test_lead_win_and_lose(self):
        lead_win = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Выигранный лид", phone="+996555334455"
        )
        win_res = self.client.post(f"/consalting/leads/{lead_win.id}/win/")
        self.assertEqual(win_res.status_code, status.HTTP_200_OK)
        self.assertEqual(win_res.data["status"], "won")
        self.assertEqual(win_res.data["queue_status"], "converted")

        loss_reason = LossReasonConsalting.objects.create(company=self.company, label="Дорого", code="expensive")
        lead_lose = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage,
            title="Проигранный лид", phone="+996555445566"
        )
        lose_res = self.client.post(
            f"/consalting/leads/{lead_lose.id}/lose/",
            {"loss_reason": str(loss_reason.id), "loss_comment": "Слишком дорого"},
            format="json"
        )
        self.assertEqual(lose_res.status_code, status.HTTP_200_OK)
        self.assertEqual(lose_res.data["status"], "lost")
        self.assertEqual(lose_res.data["queue_status"], "rejected")

    def test_lead_counters(self):
        LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage, title="L1", queue_status="new"
        )
        LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage, title="L2", queue_status="in_work"
        )
        LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.stage, title="L3", queue_status="converted", status="won"
        )

        res = self.client.get("/consalting/leads/counters/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["all"], 3)
        self.assertEqual(res.data["new"], 1)
        self.assertEqual(res.data["in_work"], 1)
        self.assertEqual(res.data["converted"], 1)

    def test_lead_analytics(self):
        res = self.client.get("/consalting/leads/analytics/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("total_leads", res.data)
        self.assertIn("conversion_rate", res.data)
