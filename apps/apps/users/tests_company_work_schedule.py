from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company


class CompanyWorkScheduleTests(APITestCase):

    def setUp(self):
        self.owner = User.objects.create(email="owner@schedule.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Schedule Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

    def test_get_company_work_schedule_defaults(self):
        """GET /api/users/company/ возвращает поля appointment_work_start и appointment_work_end."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("company-detail")
        res = self.client.get(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("appointment_work_start", res.data)
        self.assertIn("appointment_work_end", res.data)
        self.assertEqual(res.data["appointment_work_start"], "09:00")
        self.assertEqual(res.data["appointment_work_end"], "21:00")

    def test_patch_company_work_schedule_success(self):
        """PATCH /api/users/settings/company/ обновляет appointment_work_start и appointment_work_end."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("company-update")
        payload = {
            "appointment_work_start": "08:00",
            "appointment_work_end": "20:00",
        }
        res = self.client.patch(url, payload, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["appointment_work_start"], "08:00")
        self.assertEqual(res.data["appointment_work_end"], "20:00")

        self.company.refresh_from_db()
        self.assertEqual(self.company.appointment_work_start, "08:00")
        self.assertEqual(self.company.appointment_work_end, "20:00")

    def test_patch_company_work_schedule_invalid_format(self):
        """PATCH /api/users/settings/company/ с некорректным форматом времени возвращает 400."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("company-update")
        payload = {
            "appointment_work_start": "25:00",
        }
        res = self.client.patch(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("appointment_work_start", res.data)
