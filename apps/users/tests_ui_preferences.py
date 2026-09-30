import uuid
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from django.core.cache import cache

from apps.users.models import User, Company, UserUiPreferences


class UserUiPreferencesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.user1 = User.objects.create_user(
            email=f"user1_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            role="cashier",
        )
        self.company = Company.objects.create(
            name="Test UI Co",
            owner=self.user1,
        )
        self.user1.company = self.company
        self.user1.save()

        self.user2 = User.objects.create_user(
            email=f"user2_{uuid.uuid4().hex[:8]}@test.kg",
            password="password123",
            company=self.company,
            role="admin",
        )

    def tearDown(self):
        cache.clear()

    def test_unauthenticated_forbidden(self):
        res = self.client.get("/users/ui-preferences/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

        res = self.client.patch("/users/ui-preferences/", {"sidebar_auto_close": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_ui_preferences_default_lazy_creation(self):
        self.assertFalse(UserUiPreferences.objects.filter(user=self.user1).exists())

        self.client.force_authenticate(user=self.user1)
        res = self.client.get("/users/ui-preferences/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            res.data,
            {
                "sidebar_auto_close": False,
                "cashier_desktop_download": True,
                "theme_mode": "light",
                "theme_dark_palette": "soft",
            },
        )

        # Verified DB record created lazily
        self.assertTrue(UserUiPreferences.objects.filter(user=self.user1).exists())
        prefs = UserUiPreferences.objects.get(user=self.user1)
        self.assertFalse(prefs.sidebar_auto_close)
        self.assertTrue(prefs.cashier_desktop_download)
        self.assertEqual(prefs.theme_mode, "light")
        self.assertEqual(prefs.theme_dark_palette, "soft")

    def test_patch_ui_preferences(self):
        self.client.force_authenticate(user=self.user1)

        # Update sidebar_auto_close
        res = self.client.patch("/users/ui-preferences/", {"sidebar_auto_close": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["sidebar_auto_close"])
        self.assertTrue(res.data["cashier_desktop_download"])

        # Update cashier_desktop_download
        res = self.client.patch("/users/ui-preferences/", {"cashier_desktop_download": False}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["cashier_desktop_download"])

        # Update theme_mode and theme_dark_palette
        res = self.client.patch(
            "/users/ui-preferences/",
            {"theme_mode": "dark", "theme_dark_palette": "classic"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["theme_mode"], "dark")
        self.assertEqual(res.data["theme_dark_palette"], "classic")

        # Verify DB values
        prefs = UserUiPreferences.objects.get(user=self.user1)
        self.assertTrue(prefs.sidebar_auto_close)
        self.assertFalse(prefs.cashier_desktop_download)
        self.assertEqual(prefs.theme_mode, "dark")
        self.assertEqual(prefs.theme_dark_palette, "classic")

    def test_patch_validation_errors(self):
        self.client.force_authenticate(user=self.user1)

        # sidebar_auto_close
        res = self.client.patch("/users/ui-preferences/", {"sidebar_auto_close": "invalid"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("sidebar_auto_close"), ["Must be a valid boolean."])

        # cashier_desktop_download
        res = self.client.patch("/users/ui-preferences/", {"cashier_desktop_download": "invalid"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("cashier_desktop_download"), ["Must be a valid boolean."])

        # theme_mode
        res = self.client.patch("/users/ui-preferences/", {"theme_mode": "neon"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("theme_mode"), ["Допустимы light или dark"])

        # theme_dark_palette
        res = self.client.patch("/users/ui-preferences/", {"theme_dark_palette": "ultra"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get("theme_dark_palette"), ["Допустимы soft или classic"])

    def test_user_isolation(self):
        # User 1 sets dark mode
        self.client.force_authenticate(user=self.user1)
        self.client.patch("/users/ui-preferences/", {"theme_mode": "dark", "sidebar_auto_close": True}, format="json")

        # User 2 reads their own preferences
        self.client.force_authenticate(user=self.user2)
        res = self.client.get("/users/ui-preferences/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["theme_mode"], "light")
        self.assertEqual(res.data["sidebar_auto_close"], False)
