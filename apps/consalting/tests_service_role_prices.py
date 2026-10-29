from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company, CustomRole
from apps.consalting.models import (
    ServicesConsalting, TariffConsalting, ServiceRolePriceConsalting,
    TariffRolePriceConsalting, SaleConsalting, FunnelConsalting,
    FunnelStageConsalting, LeadConsalting
)
from apps.main.models import Client


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class ServiceRolePricesTests(TestCase):
    def setUp(self):
        # 1. Company & Owner
        self.owner = User.objects.create(
            email="owner_service_prices@consulting.com",
            role="owner",
            is_active=True,
        )
        self.company = Company.objects.create(name="Service Prices Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)

        # 2. Custom Roles
        self.role_manager = CustomRole.objects.create(
            company=self.company, name="Менеджер"
        )
        self.role_partner = CustomRole.objects.create(
            company=self.company, name="Партнёр"
        )

        # 3. Client for Sales
        self.client_obj = Client.objects.create(
            company=self.company, full_name="Иван Клиентов", phone="+996555000111"
        )

        # 4. Funnel for Leads
        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Партнёров", custom_role=self.role_partner
        )
        self.stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Новый", order=1
        )

    def test_service_create_and_get_role_prices(self):
        # Create service via POST
        payload = {
            "name": "Внедрение CRM",
            "price": "50000.00",
            "description": "Описание услуги",
            "role_prices": [
                {"custom_role": str(self.role_manager.id), "price": "45000.00"},
                {"custom_role": str(self.role_partner.id), "price": "40000.00"},
            ],
            "tariffs": [
                {
                    "name": "Стандарт",
                    "price": "30000.00",
                    "subscription_amount": "5000.00",
                    "subscription_period": "month",
                    "role_prices": [
                        {"custom_role": str(self.role_manager.id), "price": "27000.00"}
                    ]
                }
            ]
        }
        res_create = self.owner_client.post("/api/consalting/services/", payload, format="json")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        svc_id = res_create.data["id"]

        # Ensure installation_price is not in output
        self.assertNotIn("installation_price", res_create.data)

        # Check GET /api/consalting/services/{id}/
        res_get = self.owner_client.get(f"/api/consalting/services/{svc_id}/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        data = res_get.data

        # Service role_prices
        rp = data.get("role_prices", [])
        self.assertEqual(len(rp), 2)
        prices_by_role = {str(item["custom_role"]): str(item["price"]) for item in rp}
        self.assertEqual(prices_by_role[str(self.role_manager.id)], "45000.00")
        self.assertEqual(prices_by_role[str(self.role_partner.id)], "40000.00")

        # Tariff role_prices
        tariffs = data.get("tariffs", [])
        self.assertEqual(len(tariffs), 1)
        t_rp = tariffs[0].get("role_prices", [])
        self.assertEqual(len(t_rp), 1)
        self.assertEqual(str(t_rp[0]["custom_role"]), str(self.role_manager.id))
        self.assertEqual(str(t_rp[0]["price"]), "27000.00")

    def test_service_patch_role_prices_replacement_and_omission(self):
        svc = ServicesConsalting.objects.create(
            company=self.company, name="Консалтинг", price=Decimal("10000.00")
        )
        ServiceRolePriceConsalting.objects.create(
            service=svc, custom_role=self.role_manager, price=Decimal("8000.00")
        )

        # Omit role_prices in PATCH -> preserves existing role prices
        res_patch1 = self.owner_client.patch(
            f"/api/consalting/services/{svc.id}/",
            {"name": "Консалтинг Про"},
            format="json"
        )
        self.assertEqual(res_patch1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_patch1.data["role_prices"]), 1)

        # Send role_prices: [] -> clears role prices
        res_patch2 = self.owner_client.patch(
            f"/api/consalting/services/{svc.id}/",
            {"role_prices": []},
            format="json"
        )
        self.assertEqual(res_patch2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_patch2.data["role_prices"]), 0)

    def test_validation_negative_price_and_duplicate_role(self):
        # Negative price -> 400
        payload_neg = {
            "name": "Тест Негатив",
            "price": "1000.00",
            "role_prices": [
                {"custom_role": str(self.role_manager.id), "price": "-500.00"}
            ]
        }
        res_neg = self.owner_client.post("/api/consalting/services/", payload_neg, format="json")
        self.assertEqual(res_neg.status_code, status.HTTP_400_BAD_REQUEST)

        # Duplicate role -> 400
        payload_dup = {
            "name": "Тест Дубликат",
            "price": "1000.00",
            "role_prices": [
                {"custom_role": str(self.role_manager.id), "price": "900.00"},
                {"custom_role": str(self.role_manager.id), "price": "800.00"},
            ]
        }
        res_dup = self.owner_client.post("/api/consalting/services/", payload_dup, format="json")
        self.assertEqual(res_dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sale_total_uses_seller_role_price(self):
        seller = User.objects.create(
            email="seller@consulting.com",
            role="salesperson",
            company=self.company,
            custom_role=self.role_manager
        )
        seller_client = APIClient()
        seller_client.force_authenticate(user=seller)

        svc = ServicesConsalting.objects.create(
            company=self.company, name="Разработка ПО", price=Decimal("100000.00")
        )
        # Base price: 100000, role_manager price: 85000
        ServiceRolePriceConsalting.objects.create(
            service=svc, custom_role=self.role_manager, price=Decimal("85000.00")
        )

        sale_payload = {
            "client": str(self.client_obj.id),
            "services": str(svc.id),
            "discount": "5000.00",
            "markup": "1000.00",
        }
        res_sale = seller_client.post("/api/consalting/sales/", sale_payload, format="json")
        self.assertEqual(res_sale.status_code, status.HTTP_201_CREATED)
        # Total = 85000 - 5000 + 1000 = 81000.00
        self.assertEqual(Decimal(str(res_sale.data["total"])), Decimal("81000.00"))

    def test_lead_estimated_value_uses_funnel_role_price(self):
        svc = ServicesConsalting.objects.create(
            company=self.company, name="Аудит", price=Decimal("50000.00")
        )
        # Base price: 50000, role_partner price: 35000
        ServiceRolePriceConsalting.objects.create(
            service=svc, custom_role=self.role_partner, price=Decimal("35000.00")
        )

        # Funnel has custom_role = role_partner
        lead_payload = {
            "funnel": str(self.funnel.id),
            "stage": str(self.stage.id),
            "title": "Лид на аудит",
            "service": str(svc.id),
        }
        res_lead = self.owner_client.post("/api/consalting/leads/", lead_payload, format="json")
        self.assertEqual(res_lead.status_code, status.HTTP_201_CREATED)
        # estimated_value auto-populated with partner role price = 35000.00
        self.assertEqual(Decimal(str(res_lead.data["estimated_value"])), Decimal("35000.00"))
