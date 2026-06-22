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


class FunnelDnDApiTests(TestCase):
    """API: bulk-reorder стадий и per-user порядок воронок."""

    def setUp(self):
        from rest_framework.test import APIClient

        self.owner = User.objects.create(email="o2@x.com", first_name="O", last_name="W")
        self.company = Company.objects.create(name="Acme2", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="F1")
        self.funnel2 = FunnelConsalting.objects.create(company=self.company, name="F2")

        self.s0 = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="A", order=0, stage_type=T.NEW_LEAD)
        self.s1 = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="B", order=1, stage_type=T.QUALIFICATION)
        self.s2 = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="C", order=2, stage_type=T.PROPOSAL_SENT)
        self.sys = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Done", order=3,
            stage_type=T.COMPLETED, is_system=True, system_key="completed")

        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # ---- reorder стадий ----
    def test_reorder_swaps_orders(self):
        resp = self.client.post(
            "/api/consalting/funnel-stages/reorder/",
            [{"id": str(self.s0.id), "order": 2},
             {"id": str(self.s1.id), "order": 0},
             {"id": str(self.s2.id), "order": 1}],
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["updated"], 3)
        self.s0.refresh_from_db(); self.s1.refresh_from_db(); self.s2.refresh_from_db()
        self.assertEqual((self.s0.order, self.s1.order, self.s2.order), (2, 0, 1))

    def test_reorder_partial_move_shifts_others(self):
        # двигаем одну стадию на позицию, которую занимает другая (не из запроса):
        # остальные должны сдвинуться, без конфликта UniqueConstraint(funnel, order)
        resp = self.client.post(
            "/api/consalting/funnel-stages/reorder/",
            [{"id": str(self.s2.id), "order": 1}],
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.s0.refresh_from_db(); self.s1.refresh_from_db(); self.s2.refresh_from_db()
        # ожидаем плотную нумерацию: s0=0, s2=1 (на запрошенной позиции), s1=2
        self.assertEqual((self.s0.order, self.s2.order, self.s1.order), (0, 1, 2))
        # системная стадия осталась последней и не тронута семантически
        self.sys.refresh_from_db()
        self.assertEqual(self.sys.order, 3)

    def test_reorder_rejects_system_stage(self):
        resp = self.client.post(
            "/api/consalting/funnel-stages/reorder/",
            [{"id": str(self.sys.id), "order": 0}],
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(str(self.sys.id), resp.data["ids"])
        self.sys.refresh_from_db()
        self.assertEqual(self.sys.order, 3)  # не тронули

    def test_reorder_missing_stage_404(self):
        import uuid as _uuid
        resp = self.client.post(
            "/api/consalting/funnel-stages/reorder/",
            [{"id": str(_uuid.uuid4()), "order": 0}],
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    # ---- user-preferences ----
    def test_preferences_empty_get(self):
        resp = self.client.get("/api/consalting/user-preferences/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["funnel_order"], [])

    def test_preferences_patch_and_get(self):
        order = [str(self.funnel2.id), str(self.funnel.id)]
        resp = self.client.patch(
            "/api/consalting/user-preferences/",
            {"funnel_order": order}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["funnel_order"], order)

        resp = self.client.get("/api/consalting/user-preferences/")
        self.assertEqual(resp.data["funnel_order"], order)

    def test_preferences_drops_deleted_funnels_on_get(self):
        order = [str(self.funnel2.id), str(self.funnel.id)]
        self.client.patch(
            "/api/consalting/user-preferences/",
            {"funnel_order": order}, format="json")
        self.funnel2.delete()
        resp = self.client.get("/api/consalting/user-preferences/")
        self.assertEqual(resp.data["funnel_order"], [str(self.funnel.id)])


class ServiceRoleApiTests(TestCase):
    """API: услуги, привязанные к кастомной роли (custom_role)."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.users.models import CustomRole

        self.owner = User.objects.create(email="o3@x.com", first_name="O", last_name="W")
        self.company = Company.objects.create(name="Acme3", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.role = CustomRole.objects.create(company=self.company, name="Менеджер")
        self.other_owner = User.objects.create(email="o3b@x.com")
        self.other_company = Company.objects.create(name="Other", owner=self.other_owner)
        self.other_role = CustomRole.objects.create(company=self.other_company, name="Чужая")

        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_create_service_with_role_and_get(self):
        resp = self.client.post(
            "/api/consalting/services/",
            {"name": "Консультация", "price": "5000.00", "custom_role": str(self.role.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(str(resp.data["custom_role"]), str(self.role.id))

        sid = resp.data["id"]
        resp = self.client.get(f"/api/consalting/services/{sid}/")
        self.assertEqual(str(resp.data["custom_role"]), str(self.role.id))

    def test_create_service_without_role_is_general(self):
        resp = self.client.post(
            "/api/consalting/services/",
            {"name": "Общая", "price": "100.00"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(resp.data["custom_role"])

    def test_reject_role_from_other_company(self):
        resp = self.client.post(
            "/api/consalting/services/",
            {"name": "X", "price": "1.00", "custom_role": str(self.other_role.id)},
            format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("custom_role", resp.data)

    def test_filter_services_by_role(self):
        self.client.post("/api/consalting/services/",
                         {"name": "Ролевая", "price": "1.00", "custom_role": str(self.role.id)},
                         format="json")
        self.client.post("/api/consalting/services/",
                         {"name": "Общая2", "price": "1.00"}, format="json")
        resp = self.client.get(f"/api/consalting/services/?custom_role={self.role.id}")
        names = {s["name"] for s in resp.data.get("results", resp.data)}
        self.assertIn("Ролевая", names)
        self.assertNotIn("Общая2", names)

    def test_role_delete_nulls_service(self):
        resp = self.client.post(
            "/api/consalting/services/",
            {"name": "Сохранится", "price": "1.00", "custom_role": str(self.role.id)},
            format="json")
        sid = resp.data["id"]
        self.role.delete()
        from apps.consalting.models import ServicesConsalting
        svc = ServicesConsalting.objects.get(id=sid)
        self.assertIsNone(svc.custom_role_id)  # услуга осталась, роль обнулилась


class SubscriptionScheduleApiTests(TestCase):
    """API: расписание абонентки (deal/installment_id) + оплата периода."""

    def setUp(self):
        import uuid as _uuid
        from rest_framework.test import APIClient
        from apps.main.models import Client
        from apps.consalting.models import SaleConsalting

        self.owner = User.objects.create(email="o4@x.com", first_name="O", last_name="W")
        self.company = Company.objects.create(name="Acme4", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.client_obj = Client.objects.create(
            company=self.company, full_name="Иван", phone="+700", salesperson=self.owner)
        self.sale = SaleConsalting.objects.create(
            company=self.company, client=self.client_obj,
            subscription_amount=Decimal("5000.00"), subscription_period="month",
            subscription_started_at=timezone.now(),
        )

        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self._uuid = _uuid

    def _schedule(self):
        resp = self.client.get(
            f"/api/main/clients/{self.client_obj.id}/subscription-schedule/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.data["items"]

    def test_schedule_items_have_deal_and_installment(self):
        items = self._schedule()
        self.assertTrue(items)
        for it in items:
            self.assertIsNotNone(it["deal"])
            self.assertIsNotNone(it["installment_id"])
            self.assertIn(it["status"], ("planned", "paid"))
        # скользящее окно: минимум ~12 периодов вперёд
        self.assertGreaterEqual(len(items), 12)

    def test_schedule_is_idempotent_no_duplicate_deals(self):
        from apps.consalting.models import SaleConsalting
        self._schedule()
        n1 = len(self._schedule())
        self.sale.refresh_from_db()
        self.assertIsNotNone(self.sale.subscription_deal_id)
        # повторный GET не плодит взносы
        self.assertEqual(len(self._schedule()), n1)

    def test_pay_marks_period_paid(self):
        items = self._schedule()
        target = items[0]
        deal_id, inst_id = target["deal"], target["installment_id"]
        self.assertFalse(target["paid"])

        resp = self.client.post(
            f"/api/main/clients/{self.client_obj.id}/deals/{deal_id}/pay/",
            {"installment_id": inst_id, "amount": "5000.00",
             "idempotency_key": str(self._uuid.uuid4())},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        items = self._schedule()
        paid = next(i for i in items if i["installment_id"] == inst_id)
        self.assertTrue(paid["paid"])
        self.assertEqual(paid["status"], "paid")

    def test_pay_idempotency_key_blocks_double_charge(self):
        from apps.main.models import DealPayment
        items = self._schedule()
        target = items[0]
        deal_id, inst_id = target["deal"], target["installment_id"]
        key = str(self._uuid.uuid4())
        body = {"installment_id": inst_id, "amount": "5000.00", "idempotency_key": key}
        r1 = self.client.post(
            f"/api/main/clients/{self.client_obj.id}/deals/{deal_id}/pay/", body, format="json")
        r2 = self.client.post(
            f"/api/main/clients/{self.client_obj.id}/deals/{deal_id}/pay/", body, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(DealPayment.objects.filter(deal_id=deal_id).count(), 1)


class SalesCompletionAnalyticsApiTests(TestCase):
    """API: создание продажи на выигрыше (win/move-stage) + аналитика продаж."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.consalting.models import ServicesConsalting

        self.owner = User.objects.create(email="o5@x.com", first_name="O", last_name="W")
        self.company = Company.objects.create(name="Acme5", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="F")
        self.new = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="New", order=0, stage_type=T.NEW_LEAD)
        self.won = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Won", order=1, stage_type=T.WON)
        self.service = ServicesConsalting.objects.create(
            company=self.company, name="Консультация", price=Decimal("0"))

        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _lead(self):
        return LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.new, owner=self.owner,
            title="Лид", estimated_value=Decimal("50000"), stage_entered_at=timezone.now())

    def _sales_count(self, lead):
        from apps.consalting.models import SaleConsalting
        return SaleConsalting.objects.filter(lead=lead).count()

    def test_win_endpoint_creates_sale(self):
        lead = self._lead()
        resp = self.client.post(f"/api/consalting/leads/{lead.id}/win/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._sales_count(lead), 1)
        from apps.consalting.models import SaleConsalting
        sale = SaleConsalting.objects.get(lead=lead)
        self.assertEqual(sale.total, Decimal("50000"))

    def test_move_to_won_stage_creates_sale(self):
        lead = self._lead()
        resp = self.client.post(
            f"/api/consalting/leads/{lead.id}/move-stage/",
            {"stage": str(self.won.id)}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._sales_count(lead), 1)

    def test_sale_creation_is_idempotent(self):
        lead = self._lead()
        self.client.post(f"/api/consalting/leads/{lead.id}/win/", {}, format="json")
        # повторный move-stage на ту же WON-стадию не должен плодить продажу
        self.client.post(
            f"/api/consalting/leads/{lead.id}/move-stage/",
            {"stage": str(self.won.id)}, format="json")
        self.assertEqual(self._sales_count(lead), 1)

    def test_sales_analytics_aggregates(self):
        from apps.consalting.models import SaleConsalting
        SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            total=Decimal("30000"))
        SaleConsalting.objects.create(
            company=self.company, user=self.owner, services=self.service,
            total=Decimal("20000"), subscription_amount=Decimal("5000"),
            subscription_period="month")

        resp = self.client.get("/api/consalting/sales/analytics/")
        self.assertEqual(resp.status_code, 200, resp.content)
        t = resp.data["totals"]
        self.assertEqual(t["count"], 2)
        self.assertEqual(t["revenue"], 50000.0)
        self.assertEqual(t["avg_check"], 25000.0)
        self.assertEqual(t["subscription_count"], 1)
        self.assertEqual(t["subscription_total"], 5000.0)
        # топ услуг и сотрудников непустые
        self.assertTrue(any(s["service_name"] == "Консультация" for s in resp.data["by_service"]))
        self.assertTrue(resp.data["by_employee"])


class ConsaltingPaidIncomeTests(TestCase):
    """Факт оплаты: register-payment + подписка → аналитика продаж и общий дашборд."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.main.models import Client

        self.owner = User.objects.create(email="o6@x.com", first_name="O", last_name="W", role="owner")
        self.company = Company.objects.create(name="Acme6", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.funnel = FunnelConsalting.objects.create(company=self.company, name="F")
        self.new = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="New", order=0, stage_type=T.NEW_LEAD)
        self.client_obj = Client.objects.create(
            company=self.company, full_name="Иван", phone="+700", salesperson=self.owner)

        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _paid_income(self):
        resp = self.client.get("/api/consalting/sales/analytics/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.data["totals"]["paid_income"]

    def test_register_payment_cash_counts_as_paid(self):
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.new, owner=self.owner,
            title="Лид", client=self.client_obj, estimated_value=Decimal("10000"),
            stage_entered_at=timezone.now())
        resp = self.client.post(
            f"/api/consalting/leads/{lead.id}/register-payment/",
            {"payment_mode": "cash", "amount": "10000"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        lead.refresh_from_db()
        self.assertIsNotNone(lead.payment_deal_id)  # сделка привязана к лиду
        self.assertEqual(self._paid_income(), 10000.0)

    def test_subscription_installment_counts_as_paid(self):
        from apps.consalting.models import SaleConsalting
        sale = SaleConsalting.objects.create(
            company=self.company, client=self.client_obj,
            subscription_amount=Decimal("5000"), subscription_period="month",
            subscription_started_at=timezone.now())
        # GET расписания создаёт подложку-сделку + взносы
        sch = self.client.get(
            f"/api/main/clients/{self.client_obj.id}/subscription-schedule/")
        item = sch.data["items"][0]
        # факт оплаты ещё ноль
        self.assertEqual(self._paid_income(), 0.0)
        # оплачиваем один взнос
        import uuid as _uuid
        pay = self.client.post(
            f"/api/main/clients/{self.client_obj.id}/deals/{item['deal']}/pay/",
            {"installment_id": item["installment_id"], "amount": "5000",
             "idempotency_key": str(_uuid.uuid4())}, format="json")
        self.assertEqual(pay.status_code, 200, pay.content)
        self.assertEqual(self._paid_income(), 5000.0)

    def test_paid_income_helper_total_and_daily(self):
        # источник данных для общего дашборда (build_dashboard_payload зовёт этот же хелпер)
        from apps.consalting.funnel.analytics import consalting_paid_income
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel, stage=self.new, owner=self.owner,
            title="Лид", client=self.client_obj, estimated_value=Decimal("7000"),
            stage_entered_at=timezone.now())
        self.client.post(
            f"/api/consalting/leads/{lead.id}/register-payment/",
            {"payment_mode": "transfer", "amount": "7000"}, format="json")

        total, by_day = consalting_paid_income(self.company, with_daily=True)
        self.assertEqual(total, Decimal("7000"))
        today = timezone.localdate()
        self.assertEqual(by_day.get(today), Decimal("7000"))

    def test_paid_income_excludes_other_company_deals(self):
        # чужая компания со своей оплатой не попадает в наш paid_income
        from apps.consalting.funnel.analytics import consalting_paid_income
        from apps.main.models import Client
        other_owner = User.objects.create(email="o6b@x.com")
        other_co = Company.objects.create(name="Other6", owner=other_owner)
        other_owner.company = other_co
        other_owner.save()
        other_funnel = FunnelConsalting.objects.create(company=other_co, name="OF")
        other_new = FunnelStageConsalting.objects.create(
            company=other_co, funnel=other_funnel, name="N", order=0, stage_type=T.NEW_LEAD)
        other_client = Client.objects.create(
            company=other_co, full_name="Чужой", phone="+701", salesperson=other_owner)
        oc = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient()
        oc.force_authenticate(other_owner)
        olead = LeadConsalting.objects.create(
            company=other_co, funnel=other_funnel, stage=other_new, owner=other_owner,
            title="L", client=other_client, estimated_value=Decimal("9999"),
            stage_entered_at=timezone.now())
        oc.post(f"/api/consalting/leads/{olead.id}/register-payment/",
                {"payment_mode": "cash", "amount": "9999"}, format="json")
        # в нашей компании дохода нет
        self.assertEqual(consalting_paid_income(self.company), Decimal("0.00"))
