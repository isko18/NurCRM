from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.audit import create_platform_admin_audit_log
from apps.users.models import (
    Branch,
    Company,
    CustomRole,
    PlatformAdminAuditLog,
    Sector,
    SubscriptionPlan,
    User,
)


class PlatformAdminAccessTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="client@example.com",
            password="password123",
            first_name="Client",
            last_name="User",
        )
        self.platform_admin = User.objects.create_user(
            email="support@nurcrm.kg",
            password="password123",
            first_name="Support",
            last_name="Nur",
            is_platform_admin=True,
        )

    def test_profile_includes_platform_admin_flag_as_read_only(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/users/profile/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_platform_admin"])

        response = self.client.patch("/users/profile/", {"is_platform_admin": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_platform_admin)
        self.assertFalse(response.data["is_platform_admin"])

    def test_profile_includes_can_view_leads_inbox(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/users/profile/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("can_view_leads_inbox", response.data)
        self.assertFalse(response.data["can_view_leads_inbox"])

    def test_platform_admin_meta_requires_platform_admin_flag(self):
        response = self.client.get("/platform-admin/meta/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(self.user)
        response = self.client.get("/platform-admin/meta/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        sector = Sector.objects.create(name="Кафе")
        plan = SubscriptionPlan.objects.create(
            name="Старт",
            price=Decimal("1000.00"),
            description="",
        )

        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/platform-admin/meta/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sectors"][0]["id"], str(sector.id))
        self.assertEqual(response.data["plans"][0]["id"], str(plan.id))
        self.assertTrue(any(role["code"] == "admin" for role in response.data["roles"]))

    def test_platform_admin_meta_endpoint_via_api_prefix(self):
        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/api/platform-admin/meta/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("sectors", response.data)
        self.assertIn("plans", response.data)
        self.assertIn("roles", response.data)

    def test_login_jwt_returns_is_platform_admin_flag(self):
        response_client = self.client.post(
            "/users/auth/login/",
            {"email": "client@example.com", "password": "password123"},
            format="json",
        )
        self.assertEqual(response_client.status_code, status.HTTP_200_OK)
        self.assertIn("is_platform_admin", response_client.data)
        self.assertFalse(response_client.data["is_platform_admin"])

        response_admin = self.client.post(
            "/users/auth/login/",
            {"email": "support@nurcrm.kg", "password": "password123"},
            format="json",
        )
        self.assertEqual(response_admin.status_code, status.HTTP_200_OK)
        self.assertIn("is_platform_admin", response_admin.data)
        self.assertTrue(response_admin.data["is_platform_admin"])

    def test_create_superuser_sets_is_platform_admin_flag(self):
        superuser = User.objects.create_superuser(
            email="superuser@nurcrm.kg",
            password="password123",
            first_name="Super",
            last_name="User",
        )
        self.assertTrue(superuser.is_superuser)
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_platform_admin)

    def test_audit_log_creation_and_payload_sanitization(self):
        log = create_platform_admin_audit_log(
            actor=self.platform_admin,
            action=PlatformAdminAuditLog.Action.USER_CREATE,
            object_type="user",
            object_id="123",
            company_id="10",
            payload={
                "email": "test@example.com",
                "password": "plain_secret_password",
                "refresh_token": "secret_refresh_token",
                "nested": {"token_value": "nested_secret", "name": "Nur"},
            },
        )
        self.assertEqual(log.actor, self.platform_admin)
        self.assertEqual(log.action, "user.create")
        self.assertEqual(log.object_type, "user")
        self.assertEqual(log.object_id, "123")
        self.assertEqual(log.company_id, "10")
        self.assertEqual(log.payload["email"], "test@example.com")
        self.assertEqual(log.payload["password"], "[redacted]")
        self.assertEqual(log.payload["refresh_token"], "[redacted]")
        self.assertEqual(log.payload["nested"]["token_value"], "[redacted]")
        self.assertEqual(log.payload["nested"]["name"], "Nur")


class PlatformAdminCompanyTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.users.models import Company, Branch, CustomRole

        self.timedelta = timedelta
        self.timezone = timezone

        self.owner = User.objects.create_user(
            email="owner@romashka.kg",
            password="password123",
            first_name="Owner",
            last_name="Romashka",
            role="owner",
        )
        self.sector = Sector.objects.create(name="Кафе")
        self.plan = SubscriptionPlan.objects.create(
            name="Старт",
            price=Decimal("1000.00"),
            description="",
        )
        self.company = Company.objects.create(
            name="Кафе Ромашка",
            slug="romashka",
            owner=self.owner,
            sector=self.sector,
            subscription_plan=self.plan,
            inn="12345678901234",
            is_active=True,
            end_date=self.timezone.now() + self.timedelta(days=30),
            support_note="Оплатили до конца месяца",
        )
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(
            company=self.company,
            name="Центр",
            code="center",
        )
        self.role = CustomRole.objects.create(
            company=self.company,
            name="Официант",
        )

        self.platform_admin = User.objects.create_user(
            email="support@nurcrm.kg",
            password="password123",
            first_name="Support",
            last_name="Nur",
            is_platform_admin=True,
        )
        self.regular_user = User.objects.create_user(
            email="regular@example.com",
            password="password123",
            first_name="Regular",
            last_name="User",
        )

    def test_companies_list_requires_platform_admin(self):
        response = self.client.get("/platform-admin/companies/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(self.regular_user)
        response = self.client.get("/platform-admin/companies/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.platform_admin)
        response = self.client.get("/platform-admin/companies/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        item = response.data["results"][0]
        self.assertEqual(item["name"], "Кафе Ромашка")
        self.assertEqual(item["slug"], "romashka")
        self.assertEqual(item["sector"]["name"], "Кафе")
        self.assertEqual(item["subscription_plan"]["name"], "Старт")

    def test_companies_list_filters(self):
        self.client.force_authenticate(self.platform_admin)

        # Search filter
        resp = self.client.get("/platform-admin/companies/?search=romashka")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?search=12345678901234")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?search=owner@romashka.kg")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?search=nonexistent")
        self.assertEqual(resp.data["count"], 0)

        # Sector filter
        resp = self.client.get(f"/platform-admin/companies/?sector={self.sector.id}")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?sector=Кафе")
        self.assertEqual(resp.data["count"], 1)

        # Plan filter
        resp = self.client.get(f"/platform-admin/companies/?plan={self.plan.id}")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?plan=Старт")
        self.assertEqual(resp.data["count"], 1)

        # Status: active
        resp = self.client.get("/platform-admin/companies/?status=active")
        self.assertEqual(resp.data["count"], 1)

        # Status: expired
        self.company.end_date = self.timezone.now() - self.timedelta(days=5)
        self.company.save()
        resp = self.client.get("/platform-admin/companies/?status=expired")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get("/platform-admin/companies/?status=active")
        self.assertEqual(resp.data["count"], 0)

        # Status: blocked
        self.company.is_active = False
        self.company.save()
        resp = self.client.get("/platform-admin/companies/?status=blocked")
        self.assertEqual(resp.data["count"], 1)

        # Status: missing_date
        self.company.is_active = True
        self.company.end_date = None
        self.company.save()
        resp = self.client.get("/platform-admin/companies/?status=missing_date")
        self.assertEqual(resp.data["count"], 1)

    def test_company_detail_endpoint(self):
        self.client.force_authenticate(self.platform_admin)
        response = self.client.get(f"/platform-admin/companies/{self.company.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Кафе Ромашка")
        self.assertEqual(response.data["slug"], "romashka")
        self.assertEqual(response.data["support_note"], "Оплатили до конца месяца")
        self.assertEqual(len(response.data["branches"]), 1)
        self.assertEqual(response.data["branches"][0]["name"], "Центр")
        self.assertEqual(len(response.data["custom_roles"]), 1)
        self.assertEqual(response.data["custom_roles"][0]["name"], "Официант")

    def test_company_patch_and_audit(self):
        self.client.force_authenticate(self.platform_admin)
        payload = {
            "name": "Новая Ромашка",
            "llc": "ОсОО Новая Ромашка",
            "inn": "98765432101234",
            "slug": "new-romashka",
            "support_note": "Обновлено поддержкой",
            "is_active": True,
        }
        response = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Новая Ромашка")
        self.assertEqual(self.company.slug, "new-romashka")
        self.assertEqual(self.company.llc, "ОсОО Новая Ромашка")

        # Verify audit log
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.COMPANY_PATCH,
            object_id=str(self.company.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertIn("name", audit.payload)
        self.assertEqual(audit.payload["name"]["new"], "Новая Ромашка")

    def test_company_patch_slug_conflict(self):
        other_owner = User.objects.create_user(
            email="other@example.com",
            password="password123",
            first_name="Other",
            last_name="Owner",
        )
        Company.objects.create(
            name="Другая",
            slug="taken-slug",
            owner=other_owner,
        )

        self.client.force_authenticate(self.platform_admin)
        response = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/",
            {"slug": "taken-slug"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data)

    def test_blocked_company_blocks_login_and_refresh(self):
        # 1. Successful login while active
        resp = self.client.post(
            "/users/auth/login/",
            {"email": "owner@romashka.kg", "password": "password123"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        refresh_token = resp.data["refresh"]

        # 2. Block company
        self.company.is_active = False
        self.company.save()

        # 3. Login fails with 403
        resp = self.client.post(
            "/users/auth/login/",
            {"email": "owner@romashka.kg", "password": "password123"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["detail"], "Компания заблокирована. Обратитесь в поддержку NUR.")

        # 4. Token refresh fails with 403
        resp = self.client.post(
            "/users/auth/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["detail"], "Компания заблокирована. Обратитесь в поддержку NUR.")

        # 5. Platform admin is NOT blocked even if linked to a blocked company
        self.platform_admin.company = self.company
        self.platform_admin.save()

        resp = self.client.post(
            "/users/auth/login/",
            {"email": "support@nurcrm.kg", "password": "password123"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_company_subscription_patch_success(self):
        self.client.force_authenticate(self.platform_admin)
        new_plan = SubscriptionPlan.objects.create(
            name="Бизнес",
            price=Decimal("3000.00"),
            description="",
        )
        payload = {
            "subscription_plan_id": str(new_plan.id),
            "end_date": "2027-01-31",
            "support_note": "Продлили на год, счёт №123",
        }
        response = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/subscription/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.company.refresh_from_db()
        self.assertEqual(self.company.subscription_plan, new_plan)
        self.assertEqual(self.company.end_date.strftime("%Y-%m-%d"), "2027-01-31")
        self.assertEqual(self.company.support_note, "Продлили на год, счёт №123")
        self.assertEqual(response.data["subscription_plan"]["name"], "Бизнес")
        self.assertEqual(response.data["end_date"], "2027-01-31")

        # Verify audit log
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.COMPANY_SUBSCRIPTION,
            object_id=str(self.company.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertIn("subscription_plan_id", audit.payload)
        self.assertEqual(audit.payload["subscription_plan_id"]["new"], str(new_plan.id))
        self.assertIn("end_date", audit.payload)
        self.assertEqual(audit.payload["end_date"]["new"], "2027-01-31")

    def test_company_subscription_patch_validation_errors(self):
        self.client.force_authenticate(self.platform_admin)

        # Invalid plan id
        resp = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/subscription/",
            {"subscription_plan_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("subscription_plan_id", resp.data)

        # Invalid date format
        resp = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/subscription/",
            {"end_date": "invalid-date-format"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", resp.data)

    def test_company_subscription_patch_permissions(self):
        response = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/subscription/",
            {"end_date": "2027-01-31"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(self.regular_user)
        response = self.client.patch(
            f"/platform-admin/companies/{self.company.id}/subscription/",
            {"end_date": "2027-01-31"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PlatformAdminUserManagementTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@company.kg",
            password="password123",
            first_name="Owner",
            last_name="User",
            role="owner",
        )
        self.company = Company.objects.create(
            name="Тестовая Компания",
            slug="test-co",
            owner=self.owner,
            is_active=True,
        )
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(
            company=self.company,
            name="Главный филиал",
            code="main",
        )
        self.custom_role = CustomRole.objects.create(
            company=self.company,
            name="Бармен",
        )

        self.platform_admin = User.objects.create_user(
            email="support@nurcrm.kg",
            password="password123",
            first_name="Support",
            last_name="Admin",
            is_platform_admin=True,
        )
        self.regular_user = User.objects.create_user(
            email="regular@example.com",
            password="password123",
            first_name="Regular",
            last_name="User",
        )

    def test_company_users_list_and_search(self):
        self.client.force_authenticate(self.platform_admin)

        # 1. List users
        response = self.client.get(f"/platform-admin/companies/{self.company.id}/users/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["email"], "owner@company.kg")

        # 2. Search
        resp = self.client.get(f"/platform-admin/companies/{self.company.id}/users/?search=owner")
        self.assertEqual(resp.data["count"], 1)
        resp = self.client.get(f"/platform-admin/companies/{self.company.id}/users/?search=notfound")
        self.assertEqual(resp.data["count"], 0)

    def test_create_user_in_company_success_and_audit(self):
        self.client.force_authenticate(self.platform_admin)
        payload = {
            "email": "cashier@company.kg",
            "first_name": "Иван",
            "last_name": "Иванов",
            "phone_number": "+996700111222",
            "role": "admin",
            "branches": [str(self.branch.id)],
            "is_active": True,
            "can_view_cashbox": True,
            "can_view_sale": True,
        }
        response = self.client.post(
            f"/platform-admin/companies/{self.company.id}/users/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("generated_password", response.data)
        self.assertTrue(bool(response.data["generated_password"]))
        self.assertEqual(response.data["email"], "cashier@company.kg")
        self.assertTrue(response.data["can_view_cashbox"])
        self.assertTrue(response.data["can_view_sale"])

        # Verify DB
        created_user = User.objects.get(email="cashier@company.kg")
        self.assertEqual(created_user.company, self.company)
        self.assertTrue(created_user.check_password(response.data["generated_password"]))
        self.assertEqual(created_user.branch_memberships.count(), 1)
        self.assertEqual(created_user.branch_memberships.first().branch, self.branch)

        # Verify audit
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.USER_CREATE,
            object_id=str(created_user.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertEqual(audit.company_id, str(self.company.id))

    def test_create_user_email_conflict(self):
        self.client.force_authenticate(self.platform_admin)
        payload = {
            "email": "owner@company.kg",
            "first_name": "Duplicate",
            "last_name": "User",
        }
        response = self.client.post(
            f"/platform-admin/companies/{self.company.id}/users/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_user_detail_patch_and_delete(self):
        self.client.force_authenticate(self.platform_admin)

        # 1. GET user detail
        resp = self.client.get(f"/platform-admin/users/{self.owner.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["email"], "owner@company.kg")
        self.assertEqual(resp.data["company"]["name"], "Тестовая Компания")

        # 2. PATCH user
        patch_payload = {
            "first_name": "Айгуль",
            "last_name": "Асанова",
            "can_view_dashboard": True,
        }
        resp = self.client.patch(
            f"/platform-admin/users/{self.owner.id}/",
            patch_payload,
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Айгуль")
        self.assertEqual(self.owner.last_name, "Асанова")
        self.assertTrue(self.owner.can_view_dashboard)

        # Verify audit log
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.USER_PATCH,
            object_id=str(self.owner.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertIn("first_name", audit.payload)
        self.assertEqual(audit.payload["first_name"]["new"], "Айгуль")

        # 3. Last owner protection
        resp = self.client.patch(
            f"/platform-admin/users/{self.owner.id}/",
            {"role": "admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)

        # 4. DELETE user
        employee = User.objects.create_user(
            email="emp@company.kg",
            password="password123",
            company=self.company,
            role="admin",
        )
        resp = self.client.delete(f"/platform-admin/users/{employee.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        employee.refresh_from_db()
        self.assertFalse(employee.is_active)
        self.assertIsNotNone(employee.deleted_at)

        # Verify delete audit log
        delete_audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.USER_DELETE,
            object_id=str(employee.id),
        ).first()
        self.assertIsNotNone(delete_audit)
        self.assertEqual(delete_audit.actor, self.platform_admin)

        # 5. GET deleted user returns 404
        resp = self.client.get(f"/platform-admin/users/{employee.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_reset_password_success_and_audit(self):
        self.client.force_authenticate(self.platform_admin)
        response = self.client.post(f"/platform-admin/users/{self.owner.id}/reset-password/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_pwd = response.data.get("generated_password")
        self.assertTrue(bool(new_pwd))

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(new_pwd))

        # Verify audit log (password should NOT be present in payload)
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.USER_RESET_PASSWORD,
            object_id=str(self.owner.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertNotIn("password", audit.payload)
        self.assertNotIn("generated_password", audit.payload)

    def test_impersonate_success_and_protection(self):
        self.client.force_authenticate(self.platform_admin)

        # 1. Impersonate normal user succeeds
        response = self.client.post(f"/platform-admin/users/{self.owner.id}/impersonate/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["email"], "owner@company.kg")
        self.assertEqual(response.data["role"], "Владелец")

        # Verify audit log (tokens not in payload)
        audit = PlatformAdminAuditLog.objects.filter(
            action=PlatformAdminAuditLog.Action.USER_IMPERSONATE,
            object_id=str(self.owner.id),
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertNotIn("access", audit.payload)
        self.assertNotIn("refresh", audit.payload)

        # 2. Impersonate another platform admin is FORBIDDEN
        other_admin = User.objects.create_user(
            email="other_admin@nurcrm.kg",
            password="password123",
            is_platform_admin=True,
        )
        resp = self.client.post(f"/platform-admin/users/{other_admin.id}/impersonate/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["detail"], "Нельзя войти от имени платформенного администратора.")





