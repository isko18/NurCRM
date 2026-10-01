from decimal import Decimal
from datetime import datetime
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import Appointment, Payout


class MasterPayoutCalculationTests(APITestCase):

    def setUp(self):
        self.owner = User.objects.create(email="owner@payouts.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Payout Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.admin = User.objects.create(email="admin@payouts.com", first_name="Admin", role="admin", company=self.company)
        self.barber = User.objects.create(email="barber@payouts.com", first_name="Barber", role="barber", company=self.company)
        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        # Создаем записи за сентябрь 2026: 2 completed, 1 booked, 1 canceled, 1 deleted
        # Completed 1: 1000
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            start_at=datetime(2026, 9, 5, 10, 0, tzinfo=timezone.get_current_timezone()),
            end_at=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.get_current_timezone()),
            price=Decimal("1000.00"),
            status=Appointment.Status.COMPLETED,
        )
        # Completed 2: 2000
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            start_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.get_current_timezone()),
            end_at=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.get_current_timezone()),
            price=Decimal("2000.00"),
            status=Appointment.Status.COMPLETED,
        )
        # Booked: 1500 (не завершена)
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            start_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.get_current_timezone()),
            end_at=datetime(2026, 9, 15, 13, 0, tzinfo=timezone.get_current_timezone()),
            price=Decimal("1500.00"),
            status=Appointment.Status.BOOKED,
        )
        # Canceled: 1000
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            start_at=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.get_current_timezone()),
            end_at=datetime(2026, 9, 16, 13, 0, tzinfo=timezone.get_current_timezone()),
            price=Decimal("1000.00"),
            status=Appointment.Status.CANCELED,
        )
        # Deleted: 1000
        Appointment.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            start_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.get_current_timezone()),
            end_at=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.get_current_timezone()),
            price=Decimal("1000.00"),
            status=Appointment.Status.DELETED,
        )

    def test_payout_percent_calculation(self):
        """Выплата 40% от выручки (выручка completed: 3000 -> 1200)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("payout-list-create")

        payload = {
            "barber": str(self.barber.id),
            "period": "2026-09",
            "percent": "40.00",
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["appointments_count"], 2)
        self.assertEqual(Decimal(res.data["total_revenue"]), Decimal("3000.00"))
        self.assertEqual(Decimal(res.data["payout_amount"]), Decimal("1200.00"))

    def test_payout_per_record_calculation(self):
        """Выплата 200 сом за каждую завершённую запись (2 completed -> 400)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("payout-list-create")

        payload = {
            "barber": str(self.barber.id),
            "period": "2026-09",
            "per_record": "200.00",
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["appointments_count"], 2)
        self.assertEqual(Decimal(res.data["payout_amount"]), Decimal("400.00"))

    def test_payout_combined_calculation(self):
        """Комбинация: 40% от выручки + 200 сом за запись + 5000 оклад (1200 + 400 + 5000 = 6600)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("payout-list-create")

        payload = {
            "barber": str(self.barber.id),
            "period": "2026-09",
            "percent": "40.00",
            "per_record": "200.00",
            "fixed": "5000.00",
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(res.data["payout_amount"]), Decimal("6600.00"))

    def test_payout_patch_recalculates(self):
        """PATCH обновляет ставки и пересчитывает сумму выплаты."""
        self.client.force_authenticate(user=self.owner)
        payout = Payout.objects.create(
            company=self.company,
            branch=self.branch,
            barber=self.barber,
            period="2026-09",
            percent=Decimal("10.00"),
            appointments_count=2,
            total_revenue=Decimal("3000.00"),
            payout_amount=Decimal("300.00")
        )

        url = reverse("payout-detail", kwargs={"pk": payout.id})
        res = self.client.patch(url, {"percent": "50.00", "fixed": "1000.00"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # 3000 * 50% = 1500 + 1000 = 2500
        self.assertEqual(Decimal(res.data["payout_amount"]), Decimal("2500.00"))
