from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    InboundLeadConsalting, RegionalFunnelRoutingConsalting,
    RegionalFunnelRuleConsalting, EmployeeFunnelGrant
)
from apps.consalting.funnel.regional_routing import pick_region_balanced, redistribute_leads


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class RegionalSupervisorRBACTests(TestCase):
    def setUp(self):
        # 1. Company and Owner
        self.owner = User.objects.create(
            email="owner@consulting.com",
            role="owner",
            is_staff=True,
            is_superuser=True
        )
        self.company = Company.objects.create(name="Consulting Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)

        # 2. Main Funnel (for incoming unassigned)
        self.funnel_main = FunnelConsalting.objects.create(
            company=self.company, name="Главная воронка", is_main=True
        )
        self.stage_main = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_main, name="Новый", order=1
        )

        # 3. Regional Funnels: Bishkek, Osh, Jalal-Abad
        self.funnel_bishkek = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Бишкек", is_main=False
        )
        self.stage_bishkek = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_bishkek, name="Первый контакт", order=1
        )

        self.funnel_osh = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Ош", is_main=False
        )
        self.stage_osh = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, name="Первый контакт", order=1
        )

        self.funnel_jalal = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Джалал-Абад", is_main=False
        )
        self.stage_jalal = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel_jalal, name="Первый контакт", order=1
        )

        # 4. Routing and Rules
        self.routing = RegionalFunnelRoutingConsalting.objects.create(
            company=self.company, enabled=True, balance_strategy="least_loaded"
        )
        self.rule_bishkek = RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing, funnel=self.funnel_bishkek, region_code="bishkek",
            label="Бишкек", is_active=True, order=1
        )
        self.rule_osh = RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing, funnel=self.funnel_osh, region_code="osh",
            label="Ош", is_active=True, order=2
        )
        self.rule_jalal = RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing, funnel=self.funnel_jalal, region_code="jalal_abad",
            label="Джалал-Абад", is_active=True, order=3
        )

        # 5. Regional Supervisors:
        # Supervisor Osh (single region)
        self.sup_osh = User.objects.create(
            email="sup_osh@consulting.com",
            role="supervisor",
            company=self.company,
            consulting_region_codes=["osh"]
        )
        self.sup_osh_client = APIClient()
        self.sup_osh_client.force_authenticate(user=self.sup_osh)

        # Supervisor Multi (Osh + Jalal-Abad)
        self.sup_multi = User.objects.create(
            email="sup_multi@consulting.com",
            role="supervisor",
            company=self.company,
            consulting_region_codes=["osh", "jalal_abad"]
        )
        self.sup_multi_client = APIClient()
        self.sup_multi_client.force_authenticate(user=self.sup_multi)

        # 6. Salespeople:
        # Salesperson Osh
        self.sales_osh = User.objects.create(
            email="sales_osh@consulting.com",
            role="salesperson",
            company=self.company,
            consulting_region_codes=["osh"]
        )
        self.sales_osh_client = APIClient()
        self.sales_osh_client.force_authenticate(user=self.sales_osh)
        EmployeeFunnelGrant.objects.create(
            employee=self.sales_osh, funnel=self.funnel_osh, can_manage_leads=True
        )

        # Salesperson Bishkek
        self.sales_bishkek = User.objects.create(
            email="sales_bishkek@consulting.com",
            role="salesperson",
            company=self.company,
            consulting_region_codes=["bishkek"]
        )
        self.sales_bishkek_client = APIClient()
        self.sales_bishkek_client.force_authenticate(user=self.sales_bishkek)
        EmployeeFunnelGrant.objects.create(
            employee=self.sales_bishkek, funnel=self.funnel_bishkek, can_manage_leads=True
        )

    # ==========================================
    # 1. Role supervisor & funnel visibility
    # ==========================================

    def test_supervisor_sees_only_own_region_funnels(self):
        res = self.sup_osh_client.get("/api/consalting/funnels/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data) if isinstance(res.data, dict) else res.data
        funnel_ids = [str(f["id"]) for f in results]
        self.assertIn(str(self.funnel_osh.id), funnel_ids)
        self.assertNotIn(str(self.funnel_bishkek.id), funnel_ids)
        self.assertNotIn(str(self.funnel_jalal.id), funnel_ids)

    def test_supervisor_board_access_permitted_and_forbidden(self):
        # Access Osh board -> 200
        res_osh = self.sup_osh_client.get(f"/api/consalting/funnels/{self.funnel_osh.id}/board/")
        self.assertEqual(res_osh.status_code, status.HTTP_200_OK)

        # Access Bishkek board -> 403
        res_bishkek = self.sup_osh_client.get(f"/api/consalting/funnels/{self.funnel_bishkek.id}/board/")
        self.assertEqual(res_bishkek.status_code, status.HTTP_403_FORBIDDEN)

    def test_supervisor_sees_all_leads_in_own_region_board(self):
        # Create 2 leads in Osh: one owned by sales_osh, one unassigned
        l1 = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="Lead 1", region_code="osh", owner=self.sales_osh
        )
        l2 = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="Lead 2", region_code="osh", owner=None
        )

        res = self.sup_osh_client.get(f"/api/consalting/funnels/{self.funnel_osh.id}/board/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        leads_in_board = []
        for col in res.data.get("columns", []):
            for card in col.get("leads", []):
                leads_in_board.append(card["id"])
        self.assertIn(str(l1.id), leads_in_board)
        self.assertIn(str(l2.id), leads_in_board)

    def test_supervisor_inbound_leads_filtered_to_region(self):
        ib_osh = InboundLeadConsalting.objects.create(
            company=self.company, full_name="Inbound Osh", phone="+996555111111", region_code="osh"
        )
        ib_bishkek = InboundLeadConsalting.objects.create(
            company=self.company, full_name="Inbound Bishkek", phone="+996555222222", region_code="bishkek"
        )

        res = self.sup_osh_client.get("/api/consalting/inbound-leads/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data)
        ids = [item["id"] for item in results]
        self.assertIn(str(ib_osh.id), ids)
        self.assertNotIn(str(ib_bishkek.id), ids)

        # Attempting ?region=bishkek does not expand access
        res_tamper = self.sup_osh_client.get("/api/consalting/inbound-leads/?region=bishkek")
        self.assertEqual(res_tamper.status_code, status.HTTP_200_OK)
        tamper_ids = [item["id"] for item in res_tamper.data.get("results", res_tamper.data)]
        self.assertNotIn(str(ib_bishkek.id), tamper_ids)

    def test_supervisor_routing_access_readonly(self):
        # GET -> 200
        res_get = self.sup_osh_client.get("/api/consalting/regional-funnel-routing/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)

        # PUT -> 403
        res_put = self.sup_osh_client.put("/api/consalting/regional-funnel-routing/", {"enabled": False})
        self.assertEqual(res_put.status_code, status.HTTP_403_FORBIDDEN)

    # ==========================================
    # 2. Employee creation and management
    # ==========================================

    def test_supervisor_creates_employee_with_forced_clamping(self):
        # Supervisor Osh creates employee, sending role="admin" and can_view_all_sales=True
        payload = {
            "email": "new_emp_osh@consulting.com",
            "first_name": "New",
            "last_name": "Worker",
            "role": "admin",
            "can_view_dashboard": True,
            "can_view_cashbox": True,
        }
        res = self.sup_osh_client.post("/api/users/employees/create/", payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        created_user = User.objects.get(email="new_emp_osh@consulting.com")
        # Enforced: role="salesperson", consulting_region_codes=["osh"], can_view_*=False
        self.assertEqual(created_user.role, "salesperson")
        self.assertEqual(created_user.get_consulting_region_codes(), ["osh"])
        self.assertFalse(created_user.can_view_cashbox)
        self.assertFalse(created_user.can_view_dashboard)

        # Automatic grant to Osh funnel
        grant = EmployeeFunnelGrant.objects.filter(employee=created_user, funnel=self.funnel_osh).first()
        self.assertIsNotNone(grant)
        self.assertTrue(grant.can_manage_leads)

    def test_supervisor_multi_region_requires_region_code_choice(self):
        # Supervisor with 2 regions without region_code -> 400
        payload = {
            "email": "need_region@consulting.com",
            "first_name": "No",
            "last_name": "Region",
        }
        res = self.sup_multi_client.post("/api/users/employees/create/", payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Specifying allowed region "jalal_abad" -> 201
        payload["region_code"] = "jalal_abad"
        res_ok = self.sup_multi_client.post("/api/users/employees/create/", payload)
        self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)
        u = User.objects.get(email="need_region@consulting.com")
        self.assertEqual(u.get_consulting_region_codes(), ["jalal_abad"])

    def test_supervisor_employee_list_isolation(self):
        res = self.sup_osh_client.get("/api/users/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data) if isinstance(res.data, dict) else res.data
        emails = [u["email"] for u in results]
        self.assertIn("sup_osh@consulting.com", emails)
        self.assertIn("sales_osh@consulting.com", emails)
        self.assertNotIn("sales_bishkek@consulting.com", emails)

    def test_salesperson_sees_only_own_leads_on_board(self):
        l_own = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="My Lead", region_code="osh", owner=self.sales_osh
        )
        l_pool = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="Pool Lead", region_code="osh", owner=None
        )

        res = self.sales_osh_client.get(f"/api/consalting/funnels/{self.funnel_osh.id}/board/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        card_ids = []
        for col in res.data.get("columns", []):
            for card in col.get("leads", []):
                card_ids.append(card["id"])
        self.assertIn(str(l_own.id), card_ids)
        self.assertNotIn(str(l_pool.id), card_ids)

    def test_supervisor_assign_within_region_and_cross_region_forbidden(self):
        lead_osh = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="Osh Lead", region_code="osh", owner=None
        )

        # Supervisor assigns Osh lead to Osh salesperson -> 200
        res_ok = self.sup_osh_client.post(
            f"/api/consalting/leads/{lead_osh.id}/assign/",
            {"owner": str(self.sales_osh.id)}
        )
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        lead_osh.refresh_from_db()
        self.assertEqual(lead_osh.owner, self.sales_osh)

        # Supervisor tries to assign Osh lead to Bishkek salesperson -> 403
        res_cross = self.sup_osh_client.post(
            f"/api/consalting/leads/{lead_osh.id}/assign/",
            {"owner": str(self.sales_bishkek.id)}
        )
        self.assertEqual(res_cross.status_code, status.HTTP_403_FORBIDDEN)

        # Salesperson tries to assign -> 403
        res_sales = self.sales_osh_client.post(
            f"/api/consalting/leads/{lead_osh.id}/assign/",
            {"owner": str(self.sales_osh.id)}
        )
        self.assertEqual(res_sales.status_code, status.HTTP_403_FORBIDDEN)

    def test_salesperson_claim_within_region_and_cross_region_forbidden(self):
        pool_osh = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
            title="Pool Osh", region_code="osh", owner=None
        )
        pool_bishkek = LeadConsalting.objects.create(
            company=self.company, funnel=self.funnel_bishkek, stage=self.stage_bishkek,
            title="Pool Bishkek", region_code="bishkek", owner=None
        )

        # Salesperson claims Osh lead -> 200
        res_claim = self.sales_osh_client.post(f"/api/consalting/leads/{pool_osh.id}/claim/")
        self.assertEqual(res_claim.status_code, status.HTTP_200_OK)
        pool_osh.refresh_from_db()
        self.assertEqual(pool_osh.owner, self.sales_osh)

        # Salesperson claims Bishkek lead -> 403 / 404 (not accessible)
        res_forbidden = self.sales_osh_client.post(f"/api/consalting/leads/{pool_bishkek.id}/claim/")
        self.assertIn(res_forbidden.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    # ==========================================
    # 3. Balanced distribution & Redistribution
    # ==========================================

    def test_redistribute_math_and_dry_run(self):
        # Create 100 unassigned leads on main funnel
        leads = [
            LeadConsalting(
                company=self.company, funnel=self.funnel_main, stage=self.stage_main,
                title=f"Unassigned {i}", owner=None, status="new"
            )
            for i in range(100)
        ]
        LeadConsalting.objects.bulk_create(leads)

        # Dry run: POST /api/consalting/regional-funnel-routing/redistribute/
        res_dry = self.owner_client.post(
            "/api/consalting/regional-funnel-routing/redistribute/",
            {"scope": "main_unassigned", "dry_run": True},
            format="json"
        )
        self.assertEqual(res_dry.status_code, status.HTTP_200_OK)
        planned = res_dry.data["planned"]
        total = res_dry.data["total"]
        self.assertEqual(total, 100)
        self.assertEqual(sum(planned.values()), 100)
        # Check spread <= 1
        self.assertLessEqual(max(planned.values()) - min(planned.values()), 1)

        # Real run
        res_real = self.owner_client.post(
            "/api/consalting/regional-funnel-routing/redistribute/",
            {"scope": "main_unassigned", "dry_run": False},
            format="json"
        )
        self.assertEqual(res_real.status_code, status.HTTP_200_OK)
        self.assertEqual(res_real.data["moved"], 100)

        # Repeated run -> moved: 0
        res_repeat = self.owner_client.post(
            "/api/consalting/regional-funnel-routing/redistribute/",
            {"scope": "main_unassigned", "dry_run": False},
            format="json"
        )
        self.assertEqual(res_repeat.data["moved"], 0)

    def test_pick_region_balanced_least_loaded_and_round_robin(self):
        # Least loaded: Osh has 10 leads, Bishkek has 2, Jalal has 5
        for _ in range(2):
            LeadConsalting.objects.create(
                company=self.company, funnel=self.funnel_bishkek, stage=self.stage_bishkek,
                region_code="bishkek", status="new"
            )
        for _ in range(10):
            LeadConsalting.objects.create(
                company=self.company, funnel=self.funnel_osh, stage=self.stage_osh,
                region_code="osh", status="new"
            )
        for _ in range(5):
            LeadConsalting.objects.create(
                company=self.company, funnel=self.funnel_jalal, stage=self.stage_jalal,
                region_code="jalal_abad", status="new"
            )

        self.routing.balance_strategy = "least_loaded"
        self.routing.save()
        chosen = pick_region_balanced(self.company, self.routing)
        self.assertEqual(chosen.region_code, "bishkek")

        # Round Robin
        self.routing.balance_strategy = "round_robin"
        self.routing._rr_cursor = 0
        self.routing.save()
        c1 = pick_region_balanced(self.company, self.routing)
        c2 = pick_region_balanced(self.company, self.routing)
        c3 = pick_region_balanced(self.company, self.routing)
        codes = [c1.region_code, c2.region_code, c3.region_code]
        self.assertEqual(codes, ["bishkek", "osh", "jalal_abad"])

    def test_regions_endpoint(self):
        # GET /api/consalting/regions/ as owner
        res_owner = self.owner_client.get("/api/consalting/regions/")
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_owner.data), 3)
        region_codes = [r["code"] for r in res_owner.data]
        self.assertIn("bishkek", region_codes)
        self.assertIn("osh", region_codes)
        self.assertIn("jalal_abad", region_codes)

        # GET /api/consalting/regions/ as supervisor Osh
        res_sup = self.sup_osh_client.get("/api/consalting/regions/")
        self.assertEqual(res_sup.status_code, status.HTTP_200_OK)
        sup_codes = [r["code"] for r in res_sup.data]
        self.assertEqual(sup_codes, ["osh"])
