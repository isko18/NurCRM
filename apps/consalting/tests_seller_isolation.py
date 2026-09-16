from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting, LeadConsalting,
    ServicesConsalting, SaleConsalting, InboundLeadConsalting
)


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class SellerAccessIsolationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            email="owner@isolation.com", password="password123", is_staff=True, is_superuser=True
        )
        self.company = Company.objects.create(name="Isolation Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.seller_a = User.objects.create(
            email="seller_a@isolation.com", password="password123", company=self.company,
            role="salesperson"
        )
        self.seller_b = User.objects.create(
            email="seller_b@isolation.com", password="password123", company=self.company,
            role="salesperson"
        )

        self.service = ServicesConsalting.objects.create(
            company=self.company, name="CRM Внедрение", price=Decimal("100000.00")
        )
        self.funnel = FunnelConsalting.objects.create(
            company=self.company, name="Воронка Продаж", is_final=True, is_main=True
        )
        self.stage = FunnelStageConsalting.objects.create(
            company=self.company, funnel=self.funnel, name="Новый", order=1
        )

        self.client_a = Client.objects.create(company=self.company, full_name="Клиент А")
        self.client_b = Client.objects.create(company=self.company, full_name="Клиент Б")

        # Продажа продавца А
        self.sale_a = SaleConsalting.objects.create(
            company=self.company, user=self.seller_a, client=self.client_a,
            services=self.service, total=Decimal("100000.00"), status="completed"
        )
        # Продажа продавца Б
        self.sale_b = SaleConsalting.objects.create(
            company=self.company, user=self.seller_b, client=self.client_b,
            services=self.service, total=Decimal("80000.00"), status="completed"
        )

        self.client_owner = APIClient()
        self.client_owner.defaults["SERVER_NAME"] = "app.nurcrm.kg"
        self.client_owner.defaults["HTTP_HOST"] = "app.nurcrm.kg"
        self.client_owner.defaults["HTTPS"] = "on"
        self.client_owner.defaults["wsgi.url_scheme"] = "https"
        self.client_owner.force_authenticate(user=self.owner)

        self.client_seller_a = APIClient()
        self.client_seller_a.defaults["SERVER_NAME"] = "app.nurcrm.kg"
        self.client_seller_a.defaults["HTTP_HOST"] = "app.nurcrm.kg"
        self.client_seller_a.defaults["HTTPS"] = "on"
        self.client_seller_a.defaults["wsgi.url_scheme"] = "https"
        self.client_seller_a.force_authenticate(user=self.seller_a)

    def test_seller_only_sees_own_sales(self):
        """Продавец видит только свои продажи; руководитель видит все."""
        # Продавец А запрашивает продажи
        res_a = self.client_seller_a.get("/api/consalting/sales/")
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        ids_a = [s["id"] for s in res_a.data["results"]]
        self.assertIn(str(self.sale_a.id), ids_a)
        self.assertNotIn(str(self.sale_b.id), ids_a)

        # Руководитель видит обе
        res_owner = self.client_owner.get("/api/consalting/sales/")
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        ids_owner = [s["id"] for s in res_owner.data["results"]]
        self.assertIn(str(self.sale_a.id), ids_owner)
        self.assertIn(str(self.sale_b.id), ids_owner)

    def test_seller_cannot_view_other_sale_detail(self):
        """Продавец А получает 404 при попытке открыть продажу продавца Б."""
        res = self.client_seller_a.get(f"/api/consalting/sales/{self.sale_b.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_seller_inbound_leads_403_without_permission(self):
        """Продавец без права can_view_leads_inbox получает 403 на входящих лидах."""
        InboundLeadConsalting.objects.create(
            company=self.company, full_name="Входящий звонок", phone="+996555000111"
        )
        res = self.client_seller_a.get("/api/consalting/inbound-leads/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
