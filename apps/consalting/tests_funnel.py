"""E2E-проверка воронки 2.0: state machine, scoring, автоматизация, аналитика."""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.users.models import Company, User
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    LeadTaskConsalting, AutomationRuleConsalting, AutomationLogConsalting,
    LossReasonConsalting, StageTransitionConsalting,
)
from apps.consalting.funnel.state_machine import FunnelStateMachine
from apps.consalting.funnel.scoring import ScoringService
from apps.consalting.funnel.analytics import PipelineAnalytics

T = FunnelStageConsalting.StageType


class FunnelFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(email="o@x.com", first_name="O", last_name="W")
        self.company = Company.objects.create(name="Acme", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="Main")
        self.stages = {}
        defs = [("New", 0, T.NEW_LEAD), ("Qual", 1, T.QUALIFICATION),
                ("Proposal", 2, T.PROPOSAL_SENT), ("Won", 3, T.WON), ("Lost", 4, T.LOST)]
        for name, order, stype in defs:
            self.stages[stype] = FunnelStageConsalting.objects.create(
                company=self.company, funnel=self.funnel, name=name, order=order, stage_type=stype,
            )

    def _lead(self, **kw):
        data = dict(company=self.company, funnel=self.funnel, stage=self.stages[T.NEW_LEAD],
                    owner=self.owner, title="Lead", estimated_value=Decimal("120000"),
                    next_action_type="call", next_action_date=timezone.now(),
                    stage_entered_at=timezone.now())
        data.update(kw)
        return LeadConsalting.objects.create(**data)

    def test_derived_stage_flags(self):
        self.assertTrue(self.stages[T.WON].is_final and self.stages[T.WON].is_success)
        self.assertTrue(self.stages[T.LOST].is_final and not self.stages[T.LOST].is_success)
        self.assertFalse(self.stages[T.QUALIFICATION].is_final)

    def test_scoring(self):
        lead = self._lead(budget_confirmed=True, urgency="high",
                          decision_maker_engaged=True, avg_response_minutes=10)
        value, grade, _ = ScoringService.recalculate(lead)
        self.assertEqual(value, 100)
        self.assertEqual(grade, "A")

    def test_transition_logs_and_lifecycle(self):
        lead = self._lead()
        FunnelStateMachine.transition(lead, self.stages[T.QUALIFICATION], actor=self.owner)
        lead.refresh_from_db()
        self.assertEqual(lead.stage_id, self.stages[T.QUALIFICATION].id)
        self.assertEqual(lead.status, LeadConsalting.Status.IN_WORK)
        self.assertEqual(StageTransitionConsalting.objects.filter(lead=lead).count(), 1)

    def test_automation_creates_task_on_stage_change(self):
        AutomationRuleConsalting.objects.create(
            company=self.company, name="proposal-fu",
            trigger=AutomationRuleConsalting.Trigger.STAGE_CHANGED,
            conditions={"to_type": T.PROPOSAL_SENT.value},
            actions=[{"type": "create_task", "task_type": "follow_up",
                      "title": "FU по КП", "due_in_days": 2}],
        )
        lead = self._lead(stage=self.stages[T.QUALIFICATION], budget_confirmed=True)
        FunnelStateMachine.transition(lead, self.stages[T.PROPOSAL_SENT], actor=self.owner)
        self.assertTrue(LeadTaskConsalting.objects.filter(lead=lead, created_by_automation=True).exists())
        self.assertTrue(AutomationLogConsalting.objects.filter(lead=lead, matched=True).exists())

    def test_win_and_lost_flow(self):
        lead = self._lead(budget_confirmed=True)
        FunnelStateMachine.transition(lead, self.stages[T.WON], actor=self.owner)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadConsalting.Status.WON)
        self.assertIsNotNone(lead.won_at)

        reason = LossReasonConsalting.objects.create(company=self.company, code="price", label="Дорого")
        lead2 = self._lead()
        lead2.loss_reason = reason
        lead2.save()
        FunnelStateMachine.transition(lead2, self.stages[T.LOST], actor=self.owner)
        lead2.refresh_from_db()
        self.assertEqual(lead2.status, LeadConsalting.Status.LOST)
        self.assertIsNotNone(lead2.lost_at)

    def test_analytics_shape(self):
        won = self._lead(budget_confirmed=True)
        FunnelStateMachine.transition(won, self.stages[T.WON], actor=self.owner)
        data = PipelineAnalytics.compute(self.funnel)
        self.assertIn("totals", data)
        self.assertEqual(data["totals"]["won"], 1)
        self.assertTrue(any(s["stage_type"] == T.WON for s in data["stages"]))
