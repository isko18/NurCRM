from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import (
    Service, Appointment, AppointmentService, ServiceSalaryRate,
    MasterSalaryAccrual, MasterSalaryPayout
)


class BarberSalaryTests(APITestCase):

    def setUp(self):
        # Создаем пользователей и компанию
        self.owner = User.objects.create(email="owner@barber.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Barber Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create(email="admin@barber.com", first_name="Admin", role="admin", company=self.company)
        self.master = User.objects.create(email="master@barber.com", first_name="Master", role="barber", company=self.company, can_view_salary=True)
        self.master_no_permission = User.objects.create(email="master_no@barber.com", first_name="Master No Perm", role="barber", company=self.company, can_view_salary=False)

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        # Создаем услуги
        self.service1 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Мужская стрижка",
            price=Decimal("1000.00")
        )
        self.service2 = Service.objects.create(
            company=self.company,
            branch=self.branch,
            name="Бритьё опасной бритвой",
            price=Decimal("2000.00")
        )

        # Назначаем процентные ставки
        self.rate1 = ServiceSalaryRate.objects.create(
            company=self.company,
            service=self.service1,
            percent=Decimal("10.00"),
            updated_by=self.owner
        )
        self.rate2 = ServiceSalaryRate.objects.create(
            company=self.company,
            service=self.service2,
            percent=Decimal("20.00"),
            updated_by=self.owner
        )

    def test_salary_rates_list_and_update(self):
        """Проверяет получение списка ставок и обновление ставки услуги."""
        url_list = reverse("salary-rates")
        url_update = reverse("salary-rate-update", kwargs={"service_id": self.service1.id})

        # Попытка запроса обычным мастером без прав -> 403
        self.client.force_authenticate(user=self.master)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Запрос от админа -> 200
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # В ответе должны быть обе услуги
        self.assertEqual(response.data["count"], 2)

        # Обновление ставки админом
        payload = {"percent": "35.50"}
        response = self.client.put(url_update, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data["percent"]), Decimal("35.50"))

        self.service1.refresh_from_db()
        self.assertEqual(self.service1.salary_rate.percent, Decimal("35.50"))

    def test_salary_accrual_on_completed_appointment(self):
        """Проверяет создание начислений при переводе записи вcompleted."""
        # Создаем запись на услуги с общей ценой 1500 (скидка/ручное переопределение)
        appointment = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=timezone.now(),
            end_at=timezone.now() + timezone.timedelta(hours=1),
            price=Decimal("1500.00"),
            status=Appointment.Status.BOOKED
        )
        AppointmentService.objects.create(appointment=appointment, service=self.service1, position=0)
        AppointmentService.objects.create(appointment=appointment, service=self.service2, position=1)

        # Переводим статус в completed
        appointment.status = Appointment.Status.COMPLETED
        appointment.save()

        # Базовая цена: Service1 (1000) + Service2 (2000) = 3000
        # Коэффициенты: 1/3 и 2/3.
        # Распределенные базы: 1500 * (1/3) = 500 и 1500 * (2/3) = 1000
        # Начисления:
        # Service1: 500 * 10% = 50
        # Service2: 1000 * 20% = 200

        accruals = MasterSalaryAccrual.objects.filter(appointment=appointment, status=MasterSalaryAccrual.Status.ACCRUED)
        self.assertEqual(accruals.count(), 2)

        acc1 = accruals.get(service=self.service1)
        self.assertEqual(acc1.service_amount, Decimal("500.00"))
        self.assertEqual(acc1.amount, Decimal("50.00"))

        acc2 = accruals.get(service=self.service2)
        self.assertEqual(acc2.service_amount, Decimal("1000.00"))
        self.assertEqual(acc2.amount, Decimal("200.00"))

    def test_revert_accrual_on_status_change(self):
        """Проверяет отмену начислений при смене статуса из completed."""
        appointment = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=timezone.now(),
            end_at=timezone.now() + timezone.timedelta(hours=1),
            price=Decimal("1500.00"),
            status=Appointment.Status.BOOKED
        )
        AppointmentService.objects.create(appointment=appointment, service=self.service1, position=0)

        # Переводим в completed
        appointment.status = Appointment.Status.COMPLETED
        appointment.save()

        self.assertTrue(MasterSalaryAccrual.objects.filter(appointment=appointment, status=MasterSalaryAccrual.Status.ACCRUED).exists())

        # Откатываем статус назад в booked
        appointment.status = Appointment.Status.BOOKED
        appointment.save()

        # Начисления должны перейти в статус canceled
        self.assertFalse(MasterSalaryAccrual.objects.filter(appointment=appointment, status=MasterSalaryAccrual.Status.ACCRUED).exists())
        self.assertTrue(MasterSalaryAccrual.objects.filter(appointment=appointment, status=MasterSalaryAccrual.Status.CANCELED).exists())

    def test_revert_paid_accrual_creates_corrective_negative_accrual(self):
        """Проверяет, что отмена выплаченного начисления создает отрицательное начисление."""
        appointment = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=timezone.now(),
            end_at=timezone.now() + timezone.timedelta(hours=1),
            price=Decimal("1000.00"),
            status=Appointment.Status.BOOKED
        )
        AppointmentService.objects.create(appointment=appointment, service=self.service1, position=0)

        # Переводим в completed
        appointment.status = Appointment.Status.COMPLETED
        appointment.save()

        accrual = MasterSalaryAccrual.objects.get(appointment=appointment)
        # Сумма начисления: 1000 * 10% = 100

        # Выплачиваем эту сумму мастеру (переводим начисление в paid)
        payout = MasterSalaryPayout.objects.create(
            company=self.company,
            master=self.master,
            amount=Decimal("100.00"),
            created_by=self.owner
        )
        accrual.status = MasterSalaryAccrual.Status.PAID
        accrual.payout = payout
        accrual.save()

        # Откатываем завершение записи
        appointment.status = Appointment.Status.BOOKED
        appointment.save()

        # Исходное начисление остается PAID
        accrual.refresh_from_db()
        self.assertEqual(accrual.status, MasterSalaryAccrual.Status.PAID)

        # Должно быть создано компенсирующее начисление с отрицательной суммой -100 и статусом ACCRUED
        corr = MasterSalaryAccrual.objects.filter(appointment=appointment, status=MasterSalaryAccrual.Status.ACCRUED).first()
        self.assertIsNotNone(corr)
        self.assertEqual(corr.amount, Decimal("-100.00"))

    def test_salary_summary_endpoint(self):
        """Проверяет работу эндпоинта сводки."""
        # Создаем начисление
        appointment = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=timezone.now(),
            end_at=timezone.now() + timezone.timedelta(hours=1),
            price=Decimal("1000.00"),
            status=Appointment.Status.BOOKED
        )
        AppointmentService.objects.create(appointment=appointment, service=self.service1, position=0)

        # Переводим в completed
        appointment.status = Appointment.Status.COMPLETED
        appointment.save()

        url = reverse("salary-summary")

        # 1. Мастер без прав -> 403
        self.client.force_authenticate(user=self.master_no_permission)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Мастер с правами -> видит только себя
        self.client.force_authenticate(user=self.master)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["by_master"]), 1)
        self.assertEqual(response.data["by_master"][0]["master"], str(self.master.id))
        self.assertEqual(Decimal(response.data["totals"]["balance"]), Decimal("100.00"))

        # 3. Владелец -> видит сводку по всем
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data["by_master"]) >= 1)

    def test_payout_fifo_split_accruals(self):
        """Проверяет FIFO закрытие и разделение начислений при выплате."""
        # Создаем несколько начислений для мастера
        appointment = Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.master,
            start_at=timezone.now(),
            end_at=timezone.now() + timezone.timedelta(hours=1),
            price=Decimal("1000.00")
        )
        
        # Начисление 1: 100.00 (создано раньше)
        acc1 = MasterSalaryAccrual.objects.create(
            company=self.company,
            master=self.master,
            appointment=appointment,
            service=self.service1,
            service_amount=Decimal("1000.00"),
            percent=Decimal("10.00"),
            amount=Decimal("100.00"),
            status=MasterSalaryAccrual.Status.ACCRUED
        )

        # Начисление 2: 200.00 (создано позже, на другую услугу, чтобы не нарушать UNIQUE constraint)
        acc2 = MasterSalaryAccrual.objects.create(
            company=self.company,
            master=self.master,
            appointment=appointment,
            service=self.service2,
            service_amount=Decimal("2000.00"),
            percent=Decimal("10.00"),
            amount=Decimal("200.00"),
            status=MasterSalaryAccrual.Status.ACCRUED
        )

        url = reverse("salary-payouts")
        self.client.force_authenticate(user=self.owner)

        # Сумма выплаты = 150 (покрывает полностью acc1 и частично acc2)
        payload = {
            "master": str(self.master.id),
            "amount": "150.00",
            "comment": "Тестовая выплата"
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Проверяем состояние начислений
        acc1.refresh_from_db()
        self.assertEqual(acc1.status, MasterSalaryAccrual.Status.PAID)
        self.assertEqual(acc1.amount, Decimal("100.00"))

        # acc2 должно быть разбито:
        # Первоначальный объект acc2 изменяется на оплаченную часть: 50.00 (PAID)
        acc2.refresh_from_db()
        self.assertEqual(acc2.status, MasterSalaryAccrual.Status.PAID)
        self.assertEqual(acc2.amount, Decimal("50.00"))

        # И должно быть создано новое начисление на оставшиеся 150.00 (ACCRUED)
        new_acc = MasterSalaryAccrual.objects.get(
            appointment=appointment,
            status=MasterSalaryAccrual.Status.ACCRUED
        )
        self.assertEqual(new_acc.amount, Decimal("150.00"))
