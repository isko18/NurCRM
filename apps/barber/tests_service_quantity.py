from decimal import Decimal
from datetime import datetime
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import Service, Appointment, AppointmentService, MasterSalaryAccrual, ServiceSalaryRate


class AppointmentServiceQuantityTests(APITestCase):

    def setUp(self):
        self.owner = User.objects.create(email="owner@qty.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Qty Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create(email="admin@qty.com", first_name="Admin", role="admin", company=self.company)
        self.barber = User.objects.create(email="barber@qty.com", first_name="Barber", role="barber", company=self.company)
        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        self.service1 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Укол Сайгандар",
            price=Decimal("500.00")
        )
        self.service2 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Консультация",
            price=Decimal("1000.00")
        )

        ServiceSalaryRate.objects.create(
            company=self.company,
            service=self.service1,
            percent=Decimal("50.00"),
            updated_by=self.owner
        )

    def test_create_appointment_with_repeated_service_ids(self):
        """services принимает массив с повторяющимися id (4 раза одна услуга)."""
        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-list")

        payload = {
            "barber": str(self.barber.id),
            "start_at": "2026-09-10T10:00:00Z",
            "end_at": "2026-09-10T11:00:00Z",
            "services": [
                str(self.service1.id),
                str(self.service1.id),
                str(self.service1.id),
                str(self.service1.id),
            ],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data["services"]), 4)
        self.assertEqual(len(res.data["services_names"]), 4)
        self.assertEqual(res.data["services_names"], ["Укол Сайгандар"] * 4)
        # 4 * 500 = 2000
        self.assertEqual(Decimal(res.data["price"]), Decimal("2000.00"))

        appt_id = res.data["id"]
        self.assertEqual(AppointmentService.objects.filter(appointment_id=appt_id).count(), 4)

    def test_create_appointment_with_qty_objects(self):
        """services принимает массив объектов с qty: [{service_id: ..., qty: 3}]."""
        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-list")

        payload = {
            "barber": str(self.barber.id),
            "start_at": "2026-09-10T11:00:00Z",
            "end_at": "2026-09-10T12:00:00Z",
            "services": [
                {"service_id": str(self.service1.id), "qty": 3},
                {"service_id": str(self.service2.id), "qty": 1},
            ],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data["services"]), 4)
        # 3 * 500 + 1000 = 2500
        self.assertEqual(Decimal(res.data["price"]), Decimal("2500.00"))

    def test_salary_accrual_per_position_on_completed(self):
        """При completed начисляется зарплата на каждую позицию услуги."""
        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-list")

        payload = {
            "barber": str(self.barber.id),
            "start_at": "2026-09-10T12:00:00Z",
            "end_at": "2026-09-10T13:00:00Z",
            "services": [str(self.service1.id), str(self.service1.id)],
            "price": "1000.00",
            "status": "booked"
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        appt_id = res.data["id"]

        detail_url = reverse("appointment-detail", kwargs={"pk": appt_id})
        patch_res = self.client.patch(detail_url, {"status": "completed"}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        # Создается 1 начисление на услугу с общей суммой позиций (service_amount=1000, amount=500)
        accruals = MasterSalaryAccrual.objects.filter(appointment_id=appt_id, status=MasterSalaryAccrual.Status.ACCRUED)
        self.assertEqual(accruals.count(), 1)
        acc = accruals.first()
        self.assertEqual(acc.service_amount, Decimal("1000.00"))
        self.assertEqual(acc.amount, Decimal("500.00"))
