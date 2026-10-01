from rest_framework.test import APITestCase
from rest_framework import status
from apps.users.models import User, Company, Branch
from apps.consalting.models import RequestsConsalting
from apps.main.models import Client
from unittest.mock import patch

class RequestsAssignedTestCase(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.other_company = Company.objects.create(name="Other Company")
        
        self.owner = User.objects.create_user(
            username="owner_user", email="owner@test.com", password="password",
            company=self.company, role="owner"
        )
        self.emp1 = User.objects.create_user(
            username="emp1_user", email="emp1@test.com", password="password",
            first_name="Иван", last_name="Иванов", company=self.company, role="salesperson"
        )
        self.emp2 = User.objects.create_user(
            username="emp2_user", email="emp2@test.com", password="password",
            first_name="Петр", last_name="Петров", company=self.company, role="salesperson"
        )
        self.other_emp = User.objects.create_user(
            username="other_emp", email="other@test.com", password="password",
            company=self.other_company, role="salesperson"
        )
        self.client_obj = Client.objects.create(
            company=self.company, full_name="Тестовый Клиент", phone="+996555111222"
        )
        self.client.force_authenticate(user=self.owner)

    @patch('apps.consalting.funnel.realtime.notify_user')
    def test_create_request_with_assigned_to(self, mock_notify):
        payload = {
            "name": "Консультация по визе",
            "client": str(self.client_obj.id),
            "assigned_to": str(self.emp1.id)
        }
        response = self.client.post("/api/consalting/requests/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["assigned_to"], str(self.emp1.id))
        self.assertEqual(response.data["assigned_to_display"], "Иван Иванов")
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args[0][0], str(self.emp1.id))
        self.assertEqual(mock_notify.call_args[0][1], "request.assigned")

    @patch('apps.consalting.funnel.realtime.notify_user')
    def test_patch_change_assigned_to(self, mock_notify):
        req = RequestsConsalting.objects.create(
            company=self.company, name="Заявка 1", client=self.client_obj, assigned_to=self.emp1
        )
        mock_notify.reset_mock()

        # PATCH без смены assigned_to
        response = self.client.patch(f"/api/consalting/requests/{req.id}/", {"name": "Новое имя"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_notify.assert_not_called()

        # PATCH со сменой assigned_to
        response = self.client.patch(f"/api/consalting/requests/{req.id}/", {"assigned_to": str(self.emp2.id)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["assigned_to"], str(self.emp2.id))
        self.assertEqual(response.data["assigned_to_display"], "Петр Петров")
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args[0][0], str(self.emp2.id))

        # PATCH assigned_to=null
        mock_notify.reset_mock()
        response = self.client.patch(f"/api/consalting/requests/{req.id}/", {"assigned_to": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["assigned_to"])
        self.assertIsNone(response.data["assigned_to_display"])
        mock_notify.assert_not_called()

    def test_assigned_to_validation_other_company(self):
        payload = {
            "name": "Заявка с чужим сотрудником",
            "assigned_to": str(self.other_emp.id)
        }
        response = self.client.post("/api/consalting/requests/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assigned_to", response.data)

    def test_filter_assigned_to(self):
        req1 = RequestsConsalting.objects.create(
            company=self.company, name="Заявка emp1", assigned_to=self.emp1
        )
        req2 = RequestsConsalting.objects.create(
            company=self.company, name="Заявка без сотрудника", assigned_to=None
        )

        # Фильтр по uuid
        res = self.client.get(f"/api/consalting/requests/?assigned_to={self.emp1.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data)
        self.assertTrue(any(item["id"] == str(req1.id) for item in results))
        self.assertFalse(any(item["id"] == str(req2.id) for item in results))

        # Фильтр none
        res_none = self.client.get("/api/consalting/requests/?assigned_to=none")
        self.assertEqual(res_none.status_code, status.HTTP_200_OK)
        results_none = res_none.data.get("results", res_none.data)
        self.assertTrue(any(item["id"] == str(req2.id) for item in results_none))
        self.assertFalse(any(item["id"] == str(req1.id) for item in results_none))
