from decimal import Decimal
from datetime import datetime, timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import (
    Service, Client, Appointment, AppointmentService,
    ServiceSalaryRate, MasterSalaryAccrual
)


class AppointmentSoftDeleteTests(APITestCase):

    def setUp(self):
        self.owner = User.objects.create(email="owner@softdel.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Soft Delete Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create(email="admin@softdel.com", first_name="Admin", role="admin", company=self.company)
        self.barber = User.objects.create(email="barber@softdel.com", first_name="Barber", role="barber", company=self.company)
        self.employee = User.objects.create(email="employee@softdel.com", first_name="Employee", role="employee", company=self.company)

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)
        self.client_user = Client.objects.create(company=self.company, branch=self.branch, full_name="Тест Клиент")

        self.service = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Стрижка",
            price=Decimal("1000.00")
        )

        self.start_dt = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.get_current_timezone())
        self.end_dt = self.start_dt + timedelta(hours=1)

    def test_soft_delete_by_admin_success(self):
        """Admin может перевести запись в status=deleted через PATCH."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.BOOKED,
        )

        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-detail", kwargs={"pk": appt.id})
        response = self.client.patch(url, {"status": "deleted"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "deleted")
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.DELETED)

    def test_soft_delete_by_non_admin_forbidden(self):
        """Обычный сотрудник не может перевести запись в status=deleted (403)."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.BOOKED,
        )

        self.client.force_authenticate(user=self.employee)
        url = reverse("appointment-detail", kwargs={"pk": appt.id})
        response = self.client.patch(url, {"status": "deleted"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.BOOKED)

    def test_appointments_list_visibility_by_role(self):
        """Admin видит deleted в списке, а обычный сотрудник не видит."""
        active_appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.CONFIRMED,
        )
        deleted_appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt + timedelta(hours=2),
            end_at=self.end_dt + timedelta(hours=2),
            price=Decimal("1500.00"),
            status=Appointment.Status.DELETED,
        )

        url = reverse("appointment-list")

        # 1. Admin
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)
        admin_ids = [a["id"] for a in res_admin.data["results"]]
        self.assertIn(str(deleted_appt.id), admin_ids)
        self.assertIn(str(active_appt.id), admin_ids)

        # 2. Employee
        self.client.force_authenticate(user=self.employee)
        res_emp = self.client.get(url)
        self.assertEqual(res_emp.status_code, status.HTTP_200_OK)
        emp_ids = [a["id"] for a in res_emp.data["results"]]
        self.assertNotIn(str(deleted_appt.id), emp_ids)
        self.assertIn(str(active_appt.id), emp_ids)

    def test_my_appointments_excludes_deleted(self):
        """Эндпоинт 'мои записи' исключает deleted."""
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.DELETED,
        )
        self.client.force_authenticate(user=self.barber)
        url = reverse("my-appointment-list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["results"]), 0)

    def test_delete_completed_with_paid_accrual_returns_409(self):
        """Попытка удалить запись с выплаченным (PAID) начислением возвращает 409."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("2000.00"),
            status=Appointment.Status.COMPLETED,
        )
        MasterSalaryAccrual.objects.create(
            company=self.company,
            master=self.barber,
            appointment=appt,
            service=self.service,
            service_amount=Decimal("2000.00"),
            percent=Decimal("50.00"),
            amount=Decimal("1000.00"),
            status=MasterSalaryAccrual.Status.PAID,
        )

        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-detail", kwargs={"pk": appt.id})
        response = self.client.patch(url, {"status": "deleted"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("выплаченным начислением", response.data.get("detail", ""))

    def test_restore_deleted_appointment_by_admin(self):
        """Admin может восстановить запись, изменив status с deleted на booked."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.DELETED,
        )

        self.client.force_authenticate(user=self.admin)
        url = reverse("appointment-detail", kwargs={"pk": appt.id})
        response = self.client.patch(url, {"status": "booked"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "booked")
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.BOOKED)

    def test_physical_delete_by_non_admin_forbidden(self):
        """Физическое удаление (DELETE) запрещено для не-админов (403)."""
        appt = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            client=self.client_user,
            barber=self.barber,
            start_at=self.start_dt,
            end_at=self.end_dt,
            price=Decimal("1000.00"),
            status=Appointment.Status.BOOKED,
        )

        self.client.force_authenticate(user=self.employee)
        url = reverse("appointment-detail", kwargs={"pk": appt.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Appointment.objects.filter(pk=appt.pk).exists())
