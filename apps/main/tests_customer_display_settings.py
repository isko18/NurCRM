import uuid
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from django.core.cache import cache

from apps.users.models import Company, User
from apps.main.models import MarketCustomerDisplaySettings


class CustomerDisplaySettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.owner = User.objects.create_user(
            email=f"owner_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            role="owner",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Test Display Co",
            owner=self.owner,
        )
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create_user(
            email=f"admin_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            company=self.company,
            role="admin",
        )

        self.cashier = User.objects.create_user(
            email=f"cashier_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            company=self.company,
            role="cashier",
        )

        # Company B for isolation test
        self.owner_b = User.objects.create_user(
            email=f"owner_b_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            role="owner",
        )
        self.company_b = Company.objects.create(
            name="Test Display Co B",
            owner=self.owner_b,
        )
        self.owner_b.company = self.company_b
        self.owner_b.save()

    def tearDown(self):
        cache.clear()

    def test_get_settings_default_lazy_creation(self):
        # Settings do not exist initially
        self.assertFalse(
            MarketCustomerDisplaySettings.objects.filter(company=self.company).exists()
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.get("/main/pos/customer-display-settings/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            res.data,
            {
                "enabled": False,
                "welcome_text": "",
                "theme": "dark",
                "slides": [],
            },
        )

        # Verified lazy creation
        self.assertTrue(
            MarketCustomerDisplaySettings.objects.filter(company=self.company).exists()
        )

    def test_get_settings_cashier_allowed(self):
        self.client.force_authenticate(user=self.cashier)
        res = self.client.get("/main/pos/customer-display-settings/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("enabled", res.data)
        self.assertIn("theme", res.data)
        self.assertIn("welcome_text", res.data)
        self.assertIn("slides", res.data)

    def test_patch_settings_cashier_forbidden(self):
        self.client.force_authenticate(user=self.cashier)
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"enabled": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("detail"), "Недостаточно прав")

    def test_patch_settings_owner_and_admin_allowed(self):
        # Owner updates enabled
        self.client.force_authenticate(user=self.owner)
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"enabled": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["enabled"])

        db_obj = MarketCustomerDisplaySettings.objects.get(company=self.company)
        self.assertTrue(db_obj.enabled)
        self.assertEqual(db_obj.updated_by, self.owner)

        # Admin updates theme & welcome_text
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"theme": "light", "welcome_text": "Добро пожаловать в NurMarket"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["theme"], "light")
        self.assertEqual(res.data["welcome_text"], "Добро пожаловать в NurMarket")

        db_obj.refresh_from_db()
        self.assertEqual(db_obj.theme, "light")
        self.assertEqual(db_obj.welcome_text, "Добро пожаловать в NurMarket")
        self.assertEqual(db_obj.updated_by, self.admin)

        # Update slides
        slides_data = [
            {"imageUrl": "https://cdn.example.com/promo1.jpg"},
            {"imageUrl": "https://cdn.example.com/promo2.jpg", "caption": "Скидка 20%"},
        ]
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"slides": slides_data},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["slides"], slides_data)

        # Clear slides
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"slides": []},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["slides"], [])

    def test_patch_validation_errors(self):
        self.client.force_authenticate(user=self.owner)

        # enabled validation
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"enabled": "not_a_bool"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("enabled"), ["Must be a valid boolean."])

        # theme validation
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"theme": "neon-blue"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("theme"), ["Допустимы dark или light"])

        # welcome_text length validation (>200 chars)
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"welcome_text": "x" * 201},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("welcome_text"), ["Слишком длинный текст"])

        # slides not a list
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"slides": "not-a-list"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("slides"), ["Каждый слайд должен содержать imageUrl"])

        # slides item missing imageUrl
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"slides": [{"caption": "Нет imageUrl"}]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("slides"), ["Каждый слайд должен содержать imageUrl"])

        # slides item empty imageUrl
        res = self.client.patch(
            "/main/pos/customer-display-settings/",
            {"slides": [{"imageUrl": "   "}]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("slides"), ["Каждый слайд должен содержать imageUrl"])

    def test_company_isolation(self):
        # Set settings for Company A
        self.client.force_authenticate(user=self.owner)
        self.client.patch(
            "/main/pos/customer-display-settings/",
            {"welcome_text": "Company A text", "theme": "light"},
            format="json",
        )

        # Company B reads settings
        self.client.force_authenticate(user=self.owner_b)
        res_b = self.client.get("/main/pos/customer-display-settings/")
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        self.assertEqual(res_b.data["welcome_text"], "")
        self.assertEqual(res_b.data["theme"], "dark")
