from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client, Branch
from apps.consalting.models import RequestsConsalting


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class RequestsConsaltingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner_req@test.com", is_staff=True, is_superuser=True
        )
        self.owner.set_password("password123")
        self.owner.save()
        self.company = Company.objects.create(name="Req Company", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(company=self.company, name="Main Branch")
        self.owner.branch = self.branch
        self.owner.save()

        self.client_entity = Client.objects.create(
            company=self.company, full_name="Тестовый Клиент"
        )

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_create_request_success(self):
        response = self.api_client.post(
            "/api/consalting/requests/",
            {
                "name": "Консультация по налогам",
                "description": "Срочный вопрос",
                "client": str(self.client_entity.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        data = response.data
        self.assertEqual(data["name"], "Консультация по налогам")
        self.assertEqual(data["company"], str(self.company.id))

        # Check DB
        obj = RequestsConsalting.objects.get(id=data["id"])
        self.assertEqual(obj.company_id, self.company.id)
        self.assertEqual(obj.branch_id, self.branch.id)

    def test_list_requests_success(self):
        RequestsConsalting.objects.create(
            company=self.company,
            name="Заявка 1",
            client=self.client_entity,
        )
        response = self.api_client.get("/api/consalting/requests/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
