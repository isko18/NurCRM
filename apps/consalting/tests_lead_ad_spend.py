import uuid
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.users.models import User, Company
from apps.consalting.models import LeadAdSpend, InboundLeadConsalting


@override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False)
class LeadAdSpendTests(TestCase):
    def setUp(self):
        # Компания 1
        self.owner = User.objects.create_user(
            email="owner@leadad.com", password="password123", is_staff=True, is_superuser=True, role="owner"
        )
        self.company = Company.objects.create(name="Ad Test Co", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        # Сотрудник без права
        self.emp = User.objects.create_user(
            email="emp@leadad.com", password="password123", company=self.company, role="manager",
            can_manage_lead_ad_spend=False
        )

        # Сотрудник с правом
        self.traffic_emp = User.objects.create_user(
            email="traffic@leadad.com", password="password123", company=self.company, role="manager",
            can_manage_lead_ad_spend=True
        )

        # Компания 2 (для проверки мультитенантной изоляции)
        self.owner2 = User.objects.create_user(
            email="owner2@other.com", password="password123", is_staff=True, is_superuser=True, role="owner"
        )
        self.company2 = Company.objects.create(name="Other Co", owner=self.owner2)
        self.owner2.company = self.company2
        self.owner2.save()

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)

        self.emp_client = APIClient()
        self.emp_client.force_authenticate(user=self.emp)

        self.traffic_client = APIClient()
        self.traffic_client.force_authenticate(user=self.traffic_emp)

        self.owner2_client = APIClient()
        self.owner2_client.force_authenticate(user=self.owner2)

        self.today = timezone.localdate()
        self.yesterday = self.today - timedelta(days=1)
        self.day_before = self.today - timedelta(days=2)

    def test_permission_denied_for_regular_employee(self):
        """Сотрудник без can_manage_lead_ad_spend получает 403 на список и запись."""
        res_list = self.emp_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res_list.status_code, status.HTTP_403_FORBIDDEN)

        res_create = self.emp_client.post(
            "/api/consalting/lead-ad-spend/",
            {"date": self.yesterday.isoformat(), "impressions": 100, "leads": 5, "spend": "1000.00"},
            format="json",
        )
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        res_bulk = self.emp_client.put(
            "/api/consalting/lead-ad-spend/bulk/",
            {"items": []},
            format="json",
        )
        self.assertEqual(res_bulk.status_code, status.HTTP_403_FORBIDDEN)

    def test_permission_granted_for_owner_and_privileged_employee(self):
        """Владелец и сотрудник с can_manage_lead_ad_spend=True имеют доступ."""
        res_owner = self.owner_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)

        res_traffic = self.traffic_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res_traffic.status_code, status.HTTP_200_OK)

    def test_user_profile_and_employees_serialization_and_grant(self):
        """Право can_manage_lead_ad_spend отдаётся в профиле, списке сотрудников и может изменяться."""
        # /api/users/profile/
        res_prof = self.traffic_client.get("/api/users/profile/")
        self.assertEqual(res_prof.status_code, status.HTTP_200_OK)
        self.assertTrue(res_prof.data.get("can_manage_lead_ad_spend"))

        # /api/users/employees/
        res_emps = self.owner_client.get("/api/users/employees/")
        self.assertEqual(res_emps.status_code, status.HTTP_200_OK)
        results = res_emps.data if isinstance(res_emps.data, list) else res_emps.data.get("results", [])
        emp_data = next((e for e in results if str(e["id"]) == str(self.emp.id)), None)
        self.assertIsNotNone(emp_data)
        self.assertFalse(emp_data.get("can_manage_lead_ad_spend"))

        # Выдача права через PATCH /api/users/employees/{id}/
        res_update = self.owner_client.patch(
            f"/api/users/employees/{self.emp.id}/",
            {"can_manage_lead_ad_spend": True},
            format="json",
        )
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.can_manage_lead_ad_spend)

        # Теперь сотрудник имеет доступ к lead-ad-spend
        res_check = self.emp_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res_check.status_code, status.HTTP_200_OK)

    def test_single_crud_lead_ad_spend(self):
        """Проверка CRUD для одиночных записей LeadAdSpend."""
        # 1. POST создание
        payload = {
            "date": self.yesterday.isoformat(),
            "impressions": 1000,
            "leads": 20,
            "spend": "5000.00",
            "note": "Facebook Ads",
        }
        res_create = self.traffic_client.post("/api/consalting/lead-ad-spend/", payload, format="json")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        record_id = res_create.data["id"]
        self.assertEqual(res_create.data["cost_per_lead"], "250.00")
        self.assertEqual(res_create.data["spend"], "5000.00")

        # 2. Повторный POST на ту же дату -> 400
        res_dup = self.traffic_client.post("/api/consalting/lead-ad-spend/", payload, format="json")
        self.assertEqual(res_dup.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("отчёт уже заведён", str(res_dup.data))

        # 3. GET одна строка
        res_get = self.traffic_client.get(f"/api/consalting/lead-ad-spend/{record_id}/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_get.data["id"], record_id)

        # 4. PATCH
        res_patch = self.traffic_client.patch(
            f"/api/consalting/lead-ad-spend/{record_id}/",
            {"leads": 25, "spend": "5500.00"},
            format="json",
        )
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_patch.data["leads"], 25)
        self.assertEqual(res_patch.data["cost_per_lead"], "220.00")

        # 5. GET список с пагинацией и фильтрами
        res_list = self.traffic_client.get(f"/api/consalting/lead-ad-spend/?date_from={self.yesterday.isoformat()}&page_size=10")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_list.data["count"], 1)
        self.assertEqual(res_list.data["results"][0]["id"], record_id)

        # 6. DELETE
        res_del = self.traffic_client.delete(f"/api/consalting/lead-ad-spend/{record_id}/")
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(LeadAdSpend.objects.filter(id=record_id).exists())

    def test_validation_errors(self):
        """Проверка валидации по §8.5."""
        # Дата в будущем
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        res = self.traffic_client.post(
            "/api/consalting/lead-ad-spend/",
            {"date": tomorrow, "impressions": 100, "leads": 5, "spend": 100},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Дата не может быть в будущем.", str(res.data))

        # Отрицательные значения
        res_neg = self.traffic_client.post(
            "/api/consalting/lead-ad-spend/",
            {"date": self.yesterday.isoformat(), "impressions": -10, "leads": 5, "spend": 100},
            format="json",
        )
        self.assertEqual(res_neg.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Показы и лиды не могут быть отрицательными.", str(res_neg.data))

        # Лидов больше чем показов
        res_more = self.traffic_client.post(
            "/api/consalting/lead-ad-spend/",
            {"date": self.yesterday.isoformat(), "impressions": 10, "leads": 50, "spend": 100},
            format="json",
        )
        self.assertEqual(res_more.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Лидов больше, чем показов", str(res_more.data))

        # Отрицательный spend
        res_spend = self.traffic_client.post(
            "/api/consalting/lead-ad-spend/",
            {"date": self.yesterday.isoformat(), "impressions": 100, "leads": 5, "spend": -50},
            format="json",
        )
        self.assertEqual(res_spend.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Сумма затрат указана неверно.", str(res_spend.data))

    def test_bulk_upsert_and_deletion(self):
        """PUT /bulk/ — upsert + удаление отсутствующих, дубликаты, идемпотентность (§8.4)."""
        # Создадим предварительно 3 строки
        r1 = LeadAdSpend.objects.create(
            company=self.company, date=self.day_before, impressions=500, leads=10, spend=Decimal("2000.00"), note="Старая"
        )
        r2 = LeadAdSpend.objects.create(
            company=self.company, date=self.yesterday, impressions=800, leads=20, spend=Decimal("4000.00")
        )
        to_delete = LeadAdSpend.objects.create(
            company=self.company, date=self.today - timedelta(days=5), impressions=100, leads=2, spend=Decimal("500.00")
        )

        bulk_payload = {
            "items": [
                {
                    "id": str(r1.id),
                    "date": self.day_before.isoformat(),
                    "impressions": 600,
                    "leads": 12,
                    "spend": "2400.00",
                    "note": "Обновлённая 1",
                },
                {
                    # без id -> должен обновить r2 по (company, date)
                    "date": self.yesterday.isoformat(),
                    "impressions": 900,
                    "leads": 30,
                    "spend": "4500.00",
                    "note": "Обновлённая 2 без id",
                },
                {
                    # совершенно новая строка
                    "date": self.today.isoformat(),
                    "impressions": 1200,
                    "leads": 40,
                    "spend": "6000.00",
                    "note": "Новая за сегодня",
                },
            ]
        }

        res_bulk = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res_bulk.status_code, status.HTTP_200_OK)
        results = res_bulk.data.get("results", [])
        self.assertEqual(len(results), 3)

        # Проверяем, что to_delete была удалена
        self.assertFalse(LeadAdSpend.objects.filter(id=to_delete.id).exists())

        # Проверяем обновление r1
        r1.refresh_from_db()
        self.assertEqual(r1.impressions, 600)
        self.assertEqual(r1.spend, Decimal("2400.00"))
        self.assertEqual(r1.note, "Обновлённая 1")

        # Проверяем обновление r2
        r2.refresh_from_db()
        self.assertEqual(r2.impressions, 900)
        self.assertEqual(r2.leads, 30)

        # Проверяем новую строку
        new_row = LeadAdSpend.objects.get(company=self.company, date=self.today)
        self.assertEqual(new_row.impressions, 1200)

        # Проверяем идемпотентность: повторный PUT с тем же payload
        res_bulk2 = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res_bulk2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_bulk2.data.get("results", [])), 3)
        self.assertEqual(LeadAdSpend.objects.filter(company=self.company).count(), 3)

    def test_bulk_duplicate_dates_in_items(self):
        """Дубль date внутри items -> 400 с русским detail."""
        bulk_payload = {
            "items": [
                {"date": self.yesterday.isoformat(), "impressions": 100, "leads": 5, "spend": "500.00"},
                {"date": self.yesterday.isoformat(), "impressions": 200, "leads": 10, "spend": "1000.00"},
            ]
        }
        res = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("встречается дважды", res.data.get("detail", ""))

    def test_bulk_foreign_company_id(self):
        """Попытка обновить чужую строку по id -> 400."""
        foreign_row = LeadAdSpend.objects.create(
            company=self.company2, date=self.yesterday, impressions=100, leads=5, spend=Decimal("500.00")
        )
        bulk_payload = {
            "items": [
                {
                    "id": str(foreign_row.id),
                    "date": self.yesterday.isoformat(),
                    "impressions": 200,
                    "leads": 10,
                    "spend": "1000.00",
                }
            ]
        }
        res = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("не найдена или принадлежит другой компании", res.data.get("detail", ""))

    def test_multitenancy_isolation(self):
        """Компания 1 не видит строки Компании 2."""
        row1 = LeadAdSpend.objects.create(
            company=self.company, date=self.yesterday, impressions=100, leads=5, spend=Decimal("500.00")
        )
        row2 = LeadAdSpend.objects.create(
            company=self.company2, date=self.yesterday, impressions=200, leads=10, spend=Decimal("1500.00")
        )

        res1 = self.traffic_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res1.data["count"], 1)
        self.assertEqual(res1.data["results"][0]["id"], str(row1.id))

        res2 = self.owner2_client.get("/api/consalting/lead-ad-spend/")
        self.assertEqual(res2.data["count"], 1)
        self.assertEqual(res2.data["results"][0]["id"], str(row2.id))

        # Попытка получить чужую строку через GET detail -> 404
        res_other = self.traffic_client.get(f"/api/consalting/lead-ad-spend/{row2.id}/")
        self.assertEqual(res_other.status_code, status.HTTP_404_NOT_FOUND)

    def test_analytics_enrichment_with_ad_spend(self):
        """GET /api/consalting/inbound-leads/analytics/ содержит блок ad_spend (§8.7)."""
        LeadAdSpend.objects.create(
            company=self.company, date=self.yesterday, impressions=1000, leads=20, spend=Decimal("5000.00")
        )
        LeadAdSpend.objects.create(
            company=self.company, date=self.today, impressions=2000, leads=30, spend=Decimal("7500.00")
        )
        # 1 входящий лид
        InboundLeadConsalting.objects.create(
            company=self.company, full_name="Тестовый Лид", phone="+996555112233"
        )

        res = self.owner_client.get("/api/consalting/inbound-leads/analytics/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("ad_spend", res.data)
        ad_spend = res.data["ad_spend"]
        self.assertEqual(ad_spend["total_spend"], "12500.00")
        self.assertEqual(ad_spend["total_impressions"], 3000)
        self.assertEqual(ad_spend["reported_leads"], 50)
        self.assertEqual(ad_spend["actual_leads"], 1)
        self.assertEqual(ad_spend["cost_per_lead"], "250.00")
        self.assertEqual(ad_spend["cost_per_actual_lead"], "12500.00")

    def test_bulk_scoped_by_date_range(self):
        """§8.4.1: bulk с date_from/date_to удаляет отсутствующие строки ТОЛЬКО внутри диапазона."""
        sept_date = date(2026, 9, 15)
        oct_date = date(2026, 10, 15)
        
        # Засеим сентябрь и октябрь
        sept_row = LeadAdSpend.objects.create(company=self.company, date=sept_date, impressions=100, leads=5, spend=Decimal("500.00"))
        oct_row = LeadAdSpend.objects.create(company=self.company, date=oct_date, impressions=200, leads=10, spend=Decimal("1000.00"))

        # Очищаем сентябрь (items=[]), указываем диапазон сентября
        bulk_payload = {
            "date_from": "2026-09-01",
            "date_to": "2026-09-30",
            "items": []
        }
        res = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Сентябрьская строка удалена
        self.assertFalse(LeadAdSpend.objects.filter(id=sept_row.id).exists())
        # Октябрьская строка ОСТАЛАСЬ
        self.assertTrue(LeadAdSpend.objects.filter(id=oct_row.id).exists())

    def test_bulk_item_date_out_of_range(self):
        """§8.4.1: item.date вне диапазона date_from/date_to -> 400."""
        bulk_payload = {
            "date_from": "2026-09-01",
            "date_to": "2026-09-30",
            "items": [
                {"date": "2026-08-15", "impressions": 100, "leads": 5, "spend": "500.00"}
            ]
        }
        res = self.traffic_client.put("/api/consalting/lead-ad-spend/bulk/", bulk_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("вне диапазона", res.data.get("detail", ""))
