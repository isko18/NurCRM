from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company, CustomRole
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, RegionalFunnelRoutingConsalting, RegionalFunnelRuleConsalting,
    SaleConsalting
)
from apps.consalting.funnel.regional_routing import resolve_funnel_and_assignee


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class RegionalFunnelsDistributionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@regional.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Regional Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.mgr_client = APIClient()
        self.mgr_client.force_authenticate(user=self.owner)

        self.emp = User.objects.create(
            email="manager@regional.com", password="password123", company=self.company
        )
        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.emp)

        # 3 региональные воронки продаж + «Внедрение»
        self.funnel_bishkek = FunnelConsalting.objects.create(
            company=self.company, name="Бишкек", is_final=False
        )
        self.stage_bishkek = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_bishkek, name="Новый", order=1
        )

        self.funnel_osh = FunnelConsalting.objects.create(
            company=self.company, name="Ош", is_final=False
        )
        self.stage_osh = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, name="Новый", order=1
        )

        self.funnel_jalal = FunnelConsalting.objects.create(
            company=self.company, name="Джалал-Абад", is_final=False
        )
        self.stage_jalal = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_jalal, name="Новый", order=1
        )

        self.funnel_vnedrenie = FunnelConsalting.objects.create(
            company=self.company, name="Внедрение", is_final=True
        )
        self.stage_vnedrenie = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_vnedrenie, name="Договор", order=1
        )

        # Цепочка: Бишкек -> Внедрение
        self.funnel_bishkek.next_funnel = self.funnel_vnedrenie
        self.funnel_bishkek.next_stage = self.stage_vnedrenie
        self.funnel_bishkek.next_assign = FunnelConsalting.NextAssign.KEEP
        self.funnel_bishkek.is_final = False
        self.funnel_bishkek.save()

    def test_regional_funnel_routing_api(self):
        # 1. GET settings
        res = self.mgr_client.get("/api/consalting/regional-funnel-routing/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["enabled"])

        # 2. PUT settings (non-owner forbidden)
        res_forbidden = self.emp_client.put("/api/consalting/regional-funnel-routing/", {"enabled": True})
        self.assertEqual(res_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        # 3. PUT settings as owner
        payload = {
            "enabled": True,
            "fallback_strategy": "round_robin",
            "default_funnel_id": str(self.funnel_bishkek.id),
            "rules": [
                {
                    "funnel_id": str(self.funnel_bishkek.id),
                    "region_code": "bishkek",
                    "phone_prefixes": ["+996312", "+996555"],
                    "source_channels": ["whatsapp"],
                    "assign_strategy": "round_robin",
                    "order": 0,
                },
                {
                    "funnel_id": str(self.funnel_osh.id),
                    "region_code": "osh",
                    "phone_prefixes": ["+996322"],
                    "wazzup_account_ids": ["acc-osh-1"],
                    "source_channels": ["whatsapp"],
                    "assign_strategy": "round_robin",
                    "order": 1,
                },
                {
                    "funnel_id": str(self.funnel_jalal.id),
                    "region_code": "jalal_abad",
                    "phone_prefixes": ["+996772"],
                    "source_channels": ["whatsapp"],
                    "assign_strategy": "round_robin",
                    "order": 2,
                }
            ]
        }
        res_put = self.mgr_client.put("/api/consalting/regional-funnel-routing/", payload, format="json")
        self.assertEqual(res_put.status_code, status.HTTP_200_OK)
        self.assertTrue(res_put.data["enabled"])
        self.assertEqual(len(res_put.data["rules"]), 3)
        self.assertEqual(res_put.data["rules"][0]["region_label"], "Бишкек")

    def test_phone_prefix_matching(self):
        routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True, fallback_strategy="round_robin"
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_bishkek, region_code="bishkek",
            phone_prefixes=["+996312", "+996555"]
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_osh, region_code="osh",
            phone_prefixes=["+996322"]
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_jalal, region_code="jalal_abad",
            phone_prefixes=["+996772"]
        )

        # Бишкек
        f, s, r, u = resolve_funnel_and_assignee(self.company, phone="+996555123456")
        self.assertEqual(f.id, self.funnel_bishkek.id)
        self.assertEqual(s.id, self.stage_bishkek.id)

        # Ош
        f, s, r, u = resolve_funnel_and_assignee(self.company, phone="+996322998877")
        self.assertEqual(f.id, self.funnel_osh.id)

        # Джалал-Абад
        f, s, r, u = resolve_funnel_and_assignee(self.company, phone="+996772445566")
        self.assertEqual(f.id, self.funnel_jalal.id)

    def test_unknown_phone_fallback_round_robin(self):
        routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True, fallback_strategy="round_robin"
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_bishkek, region_code="bishkek", order=0
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_osh, region_code="osh", order=1
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_jalal, region_code="jalal_abad", order=2
        )

        # Номер без совпадения распределяется по Round-Robin между правилами
        f1, _, _, _ = resolve_funnel_and_assignee(self.company, phone="+79991112233")
        f2, _, _, _ = resolve_funnel_and_assignee(self.company, phone="+79991112233")
        f3, _, _, _ = resolve_funnel_and_assignee(self.company, phone="+79991112233")
        f4, _, _, _ = resolve_funnel_and_assignee(self.company, phone="+79991112233")

        self.assertEqual(f1.id, self.funnel_bishkek.id)
        self.assertEqual(f2.id, self.funnel_osh.id)
        self.assertEqual(f3.id, self.funnel_jalal.id)
        self.assertEqual(f4.id, self.funnel_bishkek.id)

    def test_wazzup_account_id_routing(self):
        routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True, fallback_strategy="round_robin"
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_osh, region_code="osh",
            wazzup_account_ids=["acc-osh-uuid"]
        )

        f, s, r, u = resolve_funnel_and_assignee(self.company, phone="+70000000000", wazzup_account_id="acc-osh-uuid")
        self.assertEqual(f.id, self.funnel_osh.id)

    def test_role_based_owner_assignment_within_region(self):
        role_bishkek = CustomRole.objects.create(company=self.company, name="Продажи Бишкек")
        user1 = User.objects.create(email="bishkek1@reg.com", password="pwd", company=self.company, custom_role=role_bishkek, consulting_region_codes=["bishkek"])
        user2 = User.objects.create(email="bishkek2@reg.com", password="pwd", company=self.company, custom_role=role_bishkek, consulting_region_codes=["bishkek"])

        routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True
        )
        rule = RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_bishkek, region_code="bishkek",
            phone_prefixes=["+996555"],
            assign_role_ids=[str(role_bishkek.id)],
            assign_strategy="round_robin"
        )

        _, _, _, u1 = resolve_funnel_and_assignee(self.company, phone="+996555111111")
        _, _, _, u2 = resolve_funnel_and_assignee(self.company, phone="+996555222222")
        _, _, _, u3 = resolve_funnel_and_assignee(self.company, phone="+996555333333")

        self.assertEqual(u1.id, user1.id)
        self.assertEqual(u2.id, user2.id)
        self.assertEqual(u3.id, user1.id)

    def test_shared_role_never_assigns_another_region(self):
        shared_role = CustomRole.objects.create(company=self.company, name="Продавец")
        bishkek_seller = User.objects.create(
            email="bishkek@regional.com", password="pwd", company=self.company,
            custom_role=shared_role, consulting_region_codes=["bishkek"],
        )
        osh_seller = User.objects.create(
            email="osh@regional.com", password="pwd", company=self.company,
            custom_role=shared_role, consulting_region_codes=["osh"],
        )
        routing = RegionalFunnelRoutingConsalting.objects.create(company=self.company, enabled=True)
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_bishkek, region_code="bishkek",
            phone_prefixes=["+996312"], assign_role_ids=[str(shared_role.id)],
            assign_strategy="round_robin",
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_osh, region_code="osh",
            phone_prefixes=["+996322"], assign_role_ids=[str(shared_role.id)],
            assign_strategy="round_robin",
        )

        _, _, _, bishkek_owner_1 = resolve_funnel_and_assignee(self.company, phone="+996312111111")
        _, _, _, osh_owner = resolve_funnel_and_assignee(self.company, phone="+996322111111")
        _, _, _, bishkek_owner_2 = resolve_funnel_and_assignee(self.company, phone="+996312222222")

        self.assertEqual(bishkek_owner_1.id, bishkek_seller.id)
        self.assertEqual(osh_owner.id, osh_seller.id)
        self.assertEqual(bishkek_owner_2.id, bishkek_seller.id)

    def test_empty_regional_pool_leaves_owner_unassigned(self):
        shared_role = CustomRole.objects.create(company=self.company, name="Продавец")
        User.objects.create(
            email="only-osh@regional.com", password="pwd", company=self.company,
            custom_role=shared_role, consulting_region_codes=["osh"],
        )
        routing = RegionalFunnelRoutingConsalting.objects.create(company=self.company, enabled=True)
        RegionalFunnelRuleConsalting.objects.create(
            routing=routing, funnel=self.funnel_bishkek, region_code="bishkek",
            phone_prefixes=["+996312"], assign_role_ids=[str(shared_role.id)],
            assign_strategy="least_loaded",
        )

        funnel, _, _, owner = resolve_funnel_and_assignee(self.company, phone="+996312111111")

        self.assertEqual(funnel.id, self.funnel_bishkek.id)
        self.assertIsNone(owner)

    def test_register_payment_moves_to_vnedrenie_chain(self):
        client = Client.objects.create(company=self.company, full_name="ООО Внедрение")
        service = ServicesConsalting.objects.create(company=self.company, name="CRM Внедрение", price=Decimal("100000.00"))

        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel_bishkek,
            stage=self.stage_bishkek,
            client=client,
            service=service,
            title="Сделка Бишкек",
            estimated_value=Decimal("100000.00"),
            owner=self.emp
        )

        # Оформление оплаты
        res = self.mgr_client.post(f"/api/consalting/leads/{lead.id}/register-payment/", {
            "payment_mode": "cash",
            "amount": "100000.00",
            "note": "Оплата получена"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Проверяем, что лид перенесён в воронку «Внедрение»
        lead.refresh_from_db()
        self.assertEqual(lead.funnel_id, self.funnel_vnedrenie.id)
        self.assertEqual(lead.stage_id, self.stage_vnedrenie.id)

        # В ответе API воронка и стадия обновлены
        self.assertEqual(res.data["lead"]["funnel"], self.funnel_vnedrenie.id)
        self.assertEqual(res.data["lead"]["stage"], self.stage_vnedrenie.id)
