from decimal import Decimal
from datetime import datetime, timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import Service, Client, Appointment, AppointmentService
from apps.barber.views import compute_appointment_expected_price


class AppointmentSummaryTests(APITestCase):

    def setUp(self):
        # Создаем пользователей и компанию
        self.owner = User.objects.create(email="owner@testbarber.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Barber Summary Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create(email="admin@testbarber.com", first_name="Admin", role="admin", company=self.company)
        self.master = User.objects.create(email="master@testbarber.com", first_name="Master", role="barber", company=self.company)
        self.other_master = User.objects.create(email="other@testbarber.com", first_name="Other Master", role="barber", company=self.company)
        self.employee = User.objects.create(email="emp@testbarber.com", first_name="Emp", role="employee", company=self.company)

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        # Создаем услуги
        self.service1 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Стрижка",
            price=Decimal("1500.00")
        )
        self.service2 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Борода",
            price=Decimal("1000.00")
        )

        self.target_date = datetime(2026, 3, 5, 10, 0, tzinfo=timezone.get_current_timezone())
        self.target_date_str = "2026-03-05"

    def test_compute_appointment_expected_price_explicit_price(self):
        """Если appointment.price > 0, берется он."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=self.target_date,
            end_at=self.target_date + timedelta(hours=1),
            price=Decimal("2500.00"),
            discount=Decimal("10.00"),
            status=Appointment.Status.BOOKED,
        )
        expected = compute_appointment_expected_price(appt)
        self.assertEqual(expected, Decimal("2500.00"))

    def test_compute_appointment_expected_price_from_services_with_discount(self):
        """Если appointment.price == 0, считается сумма услуг с учетом скидки."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=self.target_date,
            end_at=self.target_date + timedelta(hours=1),
            price=Decimal("0.00"),
            discount=Decimal("20.00"), # 20% скидка на 1500 + 1000 = 2500 -> 2000
            status=Appointment.Status.CONFIRMED,
        )
        AppointmentService.objects.create(appointment=appt, service=self.service1, position=0)
        AppointmentService.objects.create(appointment=appt, service=self.service2, position=1)

        expected = compute_appointment_expected_price(appt)
        self.assertEqual(expected, Decimal("2000.00"))

    def test_summary_day_scope_requires_date(self):
        """Scope=day без date возвращает 400."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("appointment-summary")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Параметр 'date' обязателен", response.data.get("detail", ""))

    def test_summary_day_scope_invalid_date(self):
        """Scope=day с невалидной датой возвращает 400."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("appointment-summary")
        response = self.client.get(url, {"date": "invalid-date"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Неверный формат", response.data.get("detail", ""))

    def test_summary_day_scope_calculation(self):
        """Проверяет корректный подсчет ожидаемой суммы и количества за день."""
        self.client.force_authenticate(user=self.owner)

        client_a = Client.objects.create(company=self.company, branch=self.branch, full_name="Айгуль")
        client_b = Client.objects.create(company=self.company, branch=self.branch, full_name="Бектур")

        # 1) Запись 1: booked (1500)
        appt1 = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_a,
            barber=self.master,
            start_at=self.target_date,
            end_at=self.target_date + timedelta(hours=1),
            price=Decimal("1500.00"),
            status=Appointment.Status.BOOKED,
        )
        # 2) Запись 2: confirmed (2000)
        appt2 = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_a,
            barber=self.master,
            start_at=self.target_date + timedelta(hours=2),
            end_at=self.target_date + timedelta(hours=3),
            price=Decimal("2000.00"),
            status=Appointment.Status.CONFIRMED,
        )
        # 3) Запись 3: completed (3000)
        appt3 = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_b,
            barber=self.other_master,
            start_at=self.target_date + timedelta(hours=4),
            end_at=self.target_date + timedelta(hours=5),
            price=Decimal("3000.00"),
            status=Appointment.Status.COMPLETED,
        )
        # 4) Запись 4: canceled (не должна войти!)
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_a,
            barber=self.master,
            start_at=self.target_date + timedelta(hours=6),
            end_at=self.target_date + timedelta(hours=7),
            price=Decimal("5000.00"),
            status=Appointment.Status.CANCELED,
        )
        # 5) Запись 5: deleted (не должна войти!)
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_b,
            barber=self.master,
            start_at=self.target_date + timedelta(hours=8),
            end_at=self.target_date + timedelta(hours=9),
            price=Decimal("4000.00"),
            status=Appointment.Status.DELETED,
        )
        # 6) Запись на другой день (не должна войти!)
        other_day = self.target_date + timedelta(days=1)
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=client_a,
            barber=self.master,
            start_at=other_day,
            end_at=other_day + timedelta(hours=1),
            price=Decimal("10000.00"),
            status=Appointment.Status.CONFIRMED,
        )

        url = reverse("appointment-summary")
        response = self.client.get(url, {"date": self.target_date_str})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["date"], self.target_date_str)
        self.assertEqual(response.data["scope"], "day")
        self.assertEqual(response.data["expected_count"], 3)
        self.assertEqual(response.data["expected_total"], "6500.00")

        # Проверка разбивки by_client
        self.assertIn("by_client", response.data)
        by_client_list = response.data["by_client"]
        self.assertEqual(len(by_client_list), 2)
        client_a_summary = next(c for c in by_client_list if c["client_id"] == str(client_a.id))
        self.assertEqual(client_a_summary["client_name"], "Айгуль")
        self.assertEqual(client_a_summary["records_count"], 2)
        self.assertEqual(client_a_summary["expected_total"], "3500.00")

        # Фильтр по мастеру
        response_master = self.client.get(url, {"date": self.target_date_str, "barber": str(self.master.id)})
        self.assertEqual(response_master.status_code, status.HTTP_200_OK)
        self.assertEqual(response_master.data["expected_count"], 2)
        self.assertEqual(response_master.data["expected_total"], "3500.00")

        # Фильтр по статусу completed
        response_completed = self.client.get(url, {"date": self.target_date_str, "status": "completed"})
        self.assertEqual(response_completed.status_code, status.HTTP_200_OK)
        self.assertEqual(response_completed.data["expected_count"], 1)
        self.assertEqual(response_completed.data["expected_total"], "3000.00")

    def test_summary_deleted_scope_permissions_and_calculation(self):
        """Проверяет права доступа и расчет для scope=deleted."""
        # Создаем удаленные записи
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=self.target_date,
            end_at=self.target_date + timedelta(hours=1),
            price=Decimal("1200.00"),
            status=Appointment.Status.DELETED,
        )
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.other_master,
            start_at=self.target_date + timedelta(days=2),
            end_at=self.target_date + timedelta(days=2, hours=1),
            price=Decimal("1800.00"),
            status=Appointment.Status.DELETED,
        )

        url = reverse("appointment-summary")

        # 1. Обычный сотрудник без прав -> 403 Forbidden
        self.client.force_authenticate(user=self.employee)
        response_forbidden = self.client.get(url, {"scope": "deleted"})
        self.assertEqual(response_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Владелец -> 200 OK
        self.client.force_authenticate(user=self.owner)
        response_owner = self.client.get(url, {"scope": "deleted"})
        self.assertEqual(response_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(response_owner.data["scope"], "deleted")
        self.assertEqual(response_owner.data["records_count"], 2)
        self.assertEqual(response_owner.data["expected_total"], "3000.00")

        # 3. Фильтр по мастеру в scope=deleted
        response_barber = self.client.get(url, {"scope": "deleted", "barber": str(self.master.id)})
        self.assertEqual(response_barber.status_code, status.HTTP_200_OK)
        self.assertEqual(response_barber.data["records_count"], 1)
        self.assertEqual(response_barber.data["expected_total"], "1200.00")
