import json
from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company, CustomRole
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    RegionalFunnelRuleConsalting, EmployeeFunnelGrant
)


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class EmployeeRegionSubfunnelsTests(TestCase):
    def setUp(self):
        # 1. Setup Company and Owner
        self.owner = User.objects.create(
            email="owner@subfunnels.test", password="password123", is_staff=True, is_superuser=True, role="owner"
        )
        self.company = Company.objects.create(name="Subfunnels Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)

        # 2. Setup Regional Funnels: Bishkek and Osh
        from apps.consalting.models import RegionalFunnelRoutingConsalting
        self.routing = RegionalFunnelRoutingConsalting.objects.create(company=self.company)

        self.funnel_bishkek = FunnelConsalting.objects.create(
            company=self.company, name="Бишкек", funnel_kind=FunnelConsalting.FunnelKind.REGION,
            region_code="bishkek", is_final=False
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing, funnel=self.funnel_bishkek, region_code="bishkek"
        )

        self.funnel_osh = FunnelConsalting.objects.create(
            company=self.company, name="Ош", funnel_kind=FunnelConsalting.FunnelKind.REGION,
            region_code="osh", is_final=False
        )
        RegionalFunnelRuleConsalting.objects.create(
            routing=self.routing, funnel=self.funnel_osh, region_code="osh"
        )

        # 3. Setup Employees:
        # - emp_osh: Salesperson in Osh with can_create_funnel=True
        self.emp_osh = User.objects.create(
            email="emp_osh@subfunnels.test", password="password123", company=self.company,
            role="salesperson", can_create_funnel=True, consulting_region_codes=["osh"]
        )
        self.emp_osh_client = APIClient()
        self.emp_osh_client.force_authenticate(user=self.emp_osh)

        # - emp_osh2: Another salesperson in Osh
        self.emp_osh2 = User.objects.create(
            email="emp_osh2@subfunnels.test", password="password123", company=self.company,
            role="salesperson", can_create_funnel=True, consulting_region_codes=["osh"]
        )
        self.emp_osh2_client = APIClient()
        self.emp_osh2_client.force_authenticate(user=self.emp_osh2)

        # - emp_no_perm: Salesperson in Osh with can_create_funnel=False
        self.emp_no_perm = User.objects.create(
            email="no_perm@subfunnels.test", password="password123", company=self.company,
            role="salesperson", can_create_funnel=False, consulting_region_codes=["osh"]
        )
        self.no_perm_client = APIClient()
        self.no_perm_client.force_authenticate(user=self.emp_no_perm)

        # - sup_osh: Supervisor of Osh
        self.sup_osh = User.objects.create(
            email="sup_osh@subfunnels.test", password="password123", company=self.company,
            role="supervisor", consulting_region_codes=["osh"]
        )
        self.sup_osh_client = APIClient()
        self.sup_osh_client.force_authenticate(user=self.sup_osh)

    def test_employee_without_can_create_funnel_403(self):
        """Сотрудник без can_create_funnel получает 403."""
        resp = self.no_perm_client.post("/consalting/funnels/", {
            "name": "Моя воронка",
            "description": "Тест"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_creates_subfunnel_auto_binds_to_region(self):
        """Сотрудник из Оша создаёт воронку -> подшивается к Ошу, 3 стадии, гранты."""
        resp = self.emp_osh_client.post("/consalting/funnels/", {
            "name": "Тёплые лиды — доп. обзвон",
            "description": "Моя подворонка"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        funnel_id = resp.data["id"]

        funnel = FunnelConsalting.objects.get(id=funnel_id)
        self.assertEqual(funnel.parent_funnel, self.funnel_osh)
        self.assertEqual(funnel.region_code, "osh")
        self.assertEqual(funnel.funnel_kind, FunnelConsalting.FunnelKind.EMPLOYEE)
        self.assertEqual(funnel.owner_user, self.emp_osh)

        # 3 системные стадии
        stages = list(funnel.stages.all().order_by("order"))
        self.assertEqual(len(stages), 3)
        self.assertEqual(stages[0].system_key, "intake")
        self.assertEqual(stages[1].system_key, "in_progress")
        self.assertEqual(stages[2].system_key, "completed")

        # FunnelGrant для автора
        grant = EmployeeFunnelGrant.objects.filter(employee=self.emp_osh, funnel=funnel).first()
        self.assertIsNotNone(grant)
        self.assertTrue(grant.can_manage_leads)
        self.assertTrue(grant.can_manage_stages)

    def test_user_profile_and_employee_update_permission(self):
        """Выдача права can_create_funnel в карточке сотрудника и профиле."""
        # 1. Проверяем профиль без права
        resp = self.no_perm_client.get("/users/profile/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("can_create_funnel", resp.data)
        self.assertFalse(resp.data["can_create_funnel"])

        # 2. Руководитель выдаёт право
        patch_resp = self.owner_client.patch(f"/users/employees/{self.emp_no_perm.id}/", {
            "can_create_funnel": True
        }, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)

        # 3. В профиле обновилось
        resp = self.no_perm_client.get("/users/profile/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["can_create_funnel"])

    def test_supervisor_restrictions(self):
        """Supervisor может создать воронку в своём регионе, но не в чужом."""
        # 1. В своём регионе (Ош) -> 201
        resp = self.sup_osh_client.post("/consalting/funnels/", {
            "name": "Ош спецворонка",
            "parent_funnel": str(self.funnel_osh.id)
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        # 2. В чужом регионе (Бишкек) -> 403
        resp_alien = self.sup_osh_client.post("/consalting/funnels/", {
            "name": "Бишкек попытка",
            "parent_funnel": str(self.funnel_bishkek.id)
        }, format="json")
        self.assertEqual(resp_alien.status_code, status.HTTP_403_FORBIDDEN)

    def test_visibility_and_isolation(self):
        """Изоляция: продавец не видит чужие подворонки, supervisor видит подворонки своего региона."""
        # emp_osh создаёт подворонку
        f_osh1 = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Ош 1", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh
        )
        # emp_osh2 создаёт подворонку
        f_osh2 = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Ош 2", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh2
        )

        # 1. emp_osh видит f_osh1, но НЕ видит f_osh2
        resp_osh1 = self.emp_osh_client.get("/consalting/funnels/")
        data_osh1 = resp_osh1.data.get("results", resp_osh1.data) if isinstance(resp_osh1.data, dict) else resp_osh1.data
        ids_osh1 = [item["id"] for item in data_osh1]
        self.assertIn(str(f_osh1.id), ids_osh1)
        self.assertNotIn(str(f_osh2.id), ids_osh1)

        # 2. sup_osh видит обе подворонки Оша
        resp_sup = self.sup_osh_client.get("/consalting/funnels/")
        data_sup = resp_sup.data.get("results", resp_sup.data) if isinstance(resp_sup.data, dict) else resp_sup.data
        ids_sup = [item["id"] for item in data_sup]
        self.assertIn(str(f_osh1.id), ids_sup)
        self.assertIn(str(f_osh2.id), ids_sup)

        # 3. owner видит все воронки
        resp_owner = self.owner_client.get("/consalting/funnels/")
        data_owner = resp_owner.data.get("results", resp_owner.data) if isinstance(resp_owner.data, dict) else resp_owner.data
        ids_owner = [item["id"] for item in data_owner]
        self.assertIn(str(f_osh1.id), ids_owner)
        self.assertIn(str(f_osh2.id), ids_owner)
        self.assertIn(str(self.funnel_bishkek.id), ids_owner)
        self.assertIn(str(self.funnel_osh.id), ids_owner)

    def test_invariants_validation(self):
        """Проверка инвариантов: глубина дерева max 1, parent должен быть региональной, is_main не совместим с parent."""
        # 1. Подворонка не может быть родителем (глубина max 1)
        subfunnel = FunnelConsalting.objects.create(
            company=self.company, name="Подворонка 1", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh
        )
        resp = self.owner_client.post("/consalting/funnels/", {
            "name": "Вложенная в подворонку",
            "parent_funnel": str(subfunnel.id)
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent_funnel", resp.data)

        # 2. Нельзя сделать воронку родителем самой себя
        resp_self = self.owner_client.patch(f"/consalting/funnels/{self.funnel_osh.id}/", {
            "parent_funnel": str(self.funnel_osh.id)
        }, format="json")
        self.assertEqual(resp_self.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. is_main не совместим с parent_funnel
        resp_main = self.owner_client.post("/consalting/funnels/", {
            "name": "Главная подворонка",
            "is_main": True,
            "parent_funnel": str(self.funnel_osh.id)
        }, format="json")
        self.assertEqual(resp_main.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_parent_funnel_restrictions(self):
        """Автор не может менять parent_funnel (игнорируется), owner может менять регион."""
        subfunnel = FunnelConsalting.objects.create(
            company=self.company, name="Подворонка", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh
        )

        # 1. Автор пытается сменить parent на Бишкек -> поле игнорируется
        resp_author = self.emp_osh_client.patch(f"/consalting/funnels/{subfunnel.id}/", {
            "name": "Новое имя",
            "parent_funnel": str(self.funnel_bishkek.id)
        }, format="json")
        self.assertEqual(resp_author.status_code, status.HTTP_200_OK)
        subfunnel.refresh_from_db()
        self.assertEqual(subfunnel.name, "Новое имя")
        self.assertEqual(subfunnel.parent_funnel_id, self.funnel_osh.id)
        self.assertEqual(subfunnel.region_code, "osh")

        # 2. Владелец меняет родителя на Бишкек -> пересчитывается region_code
        resp_owner = self.owner_client.patch(f"/consalting/funnels/{subfunnel.id}/", {
            "parent_funnel": str(self.funnel_bishkek.id)
        }, format="json")
        self.assertEqual(resp_owner.status_code, status.HTTP_200_OK)
        subfunnel.refresh_from_db()
        self.assertEqual(subfunnel.parent_funnel_id, self.funnel_bishkek.id)
        self.assertEqual(subfunnel.region_code, "bishkek")

    def test_delete_subfunnel_with_leads_409(self):
        """Удаление подворонки с незакрытыми лидами -> 409, без лидов -> 204."""
        subfunnel = FunnelConsalting.objects.create(
            company=self.company, name="Удаляемая подворонка", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh
        )
        stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=subfunnel, name="В работе", order=1, is_final=False
        )

        # Создаём открытый лид
        lead = LeadConsalting.objects.create(
            company=self.company, funnel=subfunnel, stage=stage, title="Открытый лид"
        )

        # Попытка удалить автором -> 409 с числом открытых лидов.
        resp = self.emp_osh_client.delete(f"/consalting/funnels/{subfunnel.id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            resp.data["detail"],
            "В воронке есть 1 незакрытых лид(ов). Перенесите или закройте их перед удалением.",
        )

        # Закрытый queue_status больше не блокирует удаление.
        lead.queue_status = LeadConsalting.QueueStatus.CONVERTED
        lead.save(update_fields=["queue_status"])

        # Повторная попытка -> 204
        resp_ok = self.emp_osh_client.delete(f"/consalting/funnels/{subfunnel.id}/")
        self.assertEqual(resp_ok.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(FunnelConsalting.objects.filter(id=subfunnel.id).exists())

    def test_delete_subfunnel_rejects_foreign_and_protected_funnels(self):
        foreign = FunnelConsalting.objects.create(
            company=self.company, name="Чужая подворонка", parent_funnel=self.funnel_osh,
            region_code="osh", funnel_kind=FunnelConsalting.FunnelKind.EMPLOYEE, owner_user=self.emp_osh2,
        )

        foreign_response = self.emp_osh_client.delete(f"/consalting/funnels/{foreign.id}/")
        self.assertEqual(foreign_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(foreign_response.data["detail"], "Нет прав на удаление этой воронки.")

        protected = FunnelConsalting.objects.create(
            company=self.company, name="Защищённая", is_static=True,
        )
        protected_response = self.owner_client.delete(f"/consalting/funnels/{protected.id}/")
        self.assertEqual(protected_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(protected_response.data["detail"], "Нет прав на удаление этой воронки.")

    def test_only_management_can_delete_role_funnel(self):
        role_funnel = FunnelConsalting.objects.create(
            company=self.company,
            name="Ролевая воронка",
            funnel_kind=FunnelConsalting.FunnelKind.ROLE,
        )

        employee_response = self.emp_osh_client.delete(f"/consalting/funnels/{role_funnel.id}/")
        self.assertEqual(employee_response.status_code, status.HTTP_403_FORBIDDEN)

        owner_response = self.owner_client.delete(f"/consalting/funnels/{role_funnel.id}/")
        self.assertEqual(owner_response.status_code, status.HTTP_204_NO_CONTENT)
