from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.users.models import Company, Branch
from apps.construction.models import Cashbox, CashShift
from apps.construction.serializers import CashShiftOpenSerializer
from rest_framework.test import APIRequestFactory

User = get_user_model()

class CashShiftOpenSerializerTestCase(TestCase):
    def setUp(self):
        # Create an owner who owns a company but whose user.company field is None
        self.owner = User.objects.create(
            email="owner@test.com",
            first_name="Owner",
            last_name="Test",
        )
        self.company = Company.objects.create(
            name="Test Company",
            owner=self.owner,
        )
        # Note: self.owner.company is None (not set)

        # Create an employee who has user.company set
        self.employee = User.objects.create(
            email="employee@test.com",
            first_name="Employee",
            last_name="Test",
            company=self.company,
        )

        # Create a branch and a cashbox (with branch=None to match default active branch query)
        self.branch = Branch.objects.create(
            name="Test Branch",
            company=self.company,
        )
        self.cashbox = Cashbox.objects.create(
            name="Test Cashbox",
            company=self.company,
            branch=None,
        )

    def test_owner_can_be_cashier_in_shift_open(self):
        factory = APIRequestFactory()
        # Mock request with owner as user
        request = factory.post('/api/construction/shifts/open/')
        request.user = self.owner
        
        # Test serializer initialization
        serializer = CashShiftOpenSerializer(
            data={
                "cashbox": str(self.cashbox.id),
                "cashier": str(self.owner.id),
                "opening_cash": "0.00"
            },
            context={"request": request}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        shift = serializer.save()
        self.assertEqual(shift.cashier, self.owner)
