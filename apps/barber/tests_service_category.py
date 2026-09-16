from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.users.models import User, Company, Branch
from apps.barber.models import Service, ServiceCategory


class ServiceCategoryUniquenessTests(APITestCase):

    def setUp(self):
        self.owner = User.objects.create(email="owner@servicecat.com", first_name="Owner", role="owner")
        self.company = Company.objects.create(name="Service Category Corp", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main Branch", company=self.company)

        self.other_owner = User.objects.create(email="other_owner@servicecat.com", first_name="OtherOwner", role="owner")
        self.other_company = Company.objects.create(name="Other Corp", owner=self.other_owner)
        self.other_owner.company = self.other_company
        self.other_owner.save()
        self.other_cat = ServiceCategory.objects.create(name="Чужая категория", company=self.other_company)

        # Категории компании
        self.cat_men = ServiceCategory.objects.create(name="Мужские", company=self.company, branch=self.branch)
        self.cat_kids = ServiceCategory.objects.create(name="Детские", company=self.company, branch=self.branch)

    def test_create_same_name_different_categories_success(self):
        """Создание услуги с одинаковым именем в разных категориях разрешено (201)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("service-list")

        # 1. Стрижка в Мужские
        res1 = self.client.post(url, {
            "name": "Стрижка",
            "price": "1000.00",
            "category": str(self.cat_men.id),
        }, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # 2. Стрижка в Детские (разрешено!)
        res2 = self.client.post(url, {
            "name": "Стрижка",
            "price": "800.00",
            "category": str(self.cat_kids.id),
        }, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

    def test_create_same_name_same_category_duplicate_rejected(self):
        """Создание услуги с тем же именем в той же категории запрещено (400)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("service-list")

        # 1. Первая стрижка
        self.client.post(url, {
            "name": "Стрижка",
            "price": "1000.00",
            "category": str(self.cat_men.id),
        }, format="json")

        # 2. Дубликат с разным регистром и пробелами
        res2 = self.client.post(url, {
            "name": "  стрижка  ",
            "price": "1200.00",
            "category": str(self.cat_men.id),
        }, format="json")
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("уже есть в этой категории", str(res2.data.get("name", "")))

    def test_create_same_name_null_category_general_duplicate_rejected(self):
        """Две услуги с одинаковым именем в категории 'Общее' (category=null) запрещены."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("service-list")

        # 1. Стрижка без категории
        res1 = self.client.post(url, {
            "name": "Стрижка",
            "price": "1000.00",
            "category": None,
        }, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # 2. Вторая стрижка без категории
        res2 = self.client.post(url, {
            "name": "СТРИЖКА",
            "price": "1500.00",
            "category": None,
        }, format="json")
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_same_name_null_category_and_named_category_allowed(self):
        """Одна стрижка в 'Общее' (null), а вторая в 'Мужские' — разрешено."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("service-list")

        res1 = self.client.post(url, {
            "name": "Стрижка",
            "price": "1000.00",
            "category": None,
        }, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = self.client.post(url, {
            "name": "Стрижка",
            "price": "1200.00",
            "category": str(self.cat_men.id),
        }, format="json")
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

    def test_patch_transfer_to_conflicting_category_rejected(self):
        """PATCH: перенос услуги в категорию, где уже есть услуга с таким именем, возвращает 400."""
        self.client.force_authenticate(user=self.owner)

        # Создаем 'Стрижка' в Мужские
        svc_men = Service.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.cat_men,
            name="Стрижка",
            price=Decimal("1000.00")
        )
        # Создаем 'Стрижка' в Детские
        svc_kids = Service.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.cat_kids,
            name="Стрижка",
            price=Decimal("800.00")
        )

        # Пытаемся перенести svc_kids в Мужские (где уже есть Стрижка)
        url = reverse("service-detail", kwargs={"pk": svc_kids.id})
        res = self.client.patch(url, {"category": str(self.cat_men.id)}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("уже есть в этой категории", str(res.data.get("name", "")))

    def test_patch_price_only_same_category_success(self):
        """PATCH: изменение только цены без смены имени/категории проходит успешно (200)."""
        self.client.force_authenticate(user=self.owner)

        svc = Service.objects.create(
            company=self.company,
            branch=self.branch,
            category=self.cat_men,
            name="Стрижка",
            price=Decimal("1000.00")
        )

        url = reverse("service-detail", kwargs={"pk": svc.id})
        res = self.client.patch(url, {"price": "1300.00"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        svc.refresh_from_db()
        self.assertEqual(svc.price, Decimal("1300.00"))

    def test_foreign_company_category_rejected(self):
        """Категория другой компании отклоняется (400)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("service-list")

        res = self.client.post(url, {
            "name": "Стрижка",
            "price": "1000.00",
            "category": str(self.other_cat.id),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
