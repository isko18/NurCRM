from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.main.models import Product, ProductImage
from apps.main.showcase.views_public import PublicCompanyShowcaseAPIView, PublicCompanyProductDetailAPIView
from apps.users.models import Company, SubscriptionPlan

User = get_user_model()


class ShowcaseProductImagesTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_sc_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.slug = f"co-{uuid.uuid4().hex[:6]}"
        self.company = Company.objects.create(name="Showcase Co", slug=self.slug, owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        # Product with multiple images
        self.prod_with_images = Product.objects.create(
            name="Товар с фото",
            company=self.company,
            price=Decimal("1500.00"),
            discount_percent=Decimal("20.00"),
        )

        import io
        from PIL import Image

        def make_dummy_image_file(name):
            file_obj = io.BytesIO()
            image = Image.new("RGB", (10, 10), color=(255, 0, 0))
            image.save(file_obj, "JPEG")
            file_obj.seek(0)
            return SimpleUploadedFile(name, file_obj.read(), content_type="image/jpeg")

        dummy_img1 = make_dummy_image_file("photo1.jpg")
        dummy_img2 = make_dummy_image_file("photo2.jpg")
        dummy_img3 = make_dummy_image_file("photo3.jpg")

        # Create secondary first, primary second, secondary third
        self.img_sec1 = ProductImage.objects.create(
            company=self.company,
            product=self.prod_with_images,
            image=dummy_img1,
            alt="Второе фото",
            is_primary=False,
        )
        self.img_primary = ProductImage.objects.create(
            company=self.company,
            product=self.prod_with_images,
            image=dummy_img2,
            alt="Главное фото",
            is_primary=True,
        )
        self.img_sec2 = ProductImage.objects.create(
            company=self.company,
            product=self.prod_with_images,
            image=dummy_img3,
            alt="Третье фото",
            is_primary=False,
        )

        # Product without images
        self.prod_empty = Product.objects.create(
            name="Товар без фото",
            company=self.company,
            price=Decimal("500.00"),
        )

    def test_showcase_list_and_detail_returns_images_array(self):
        # 1. Test List Endpoint
        req_list = self.factory.get(f"/api/main/public/companies/{self.slug}/showcase/")
        view_list = PublicCompanyShowcaseAPIView.as_view()
        resp_list = view_list(req_list, slug=self.slug)
        self.assertEqual(resp_list.status_code, 200)

        results = resp_list.data["results"] if "results" in resp_list.data else resp_list.data
        item_with_img = next(p for p in results if p["id"] == str(self.prod_with_images.id))
        item_empty = next(p for p in results if p["id"] == str(self.prod_empty.id))

        # Check product with images
        images = item_with_img["images"]
        self.assertEqual(len(images), 3)

        # Primary must be first
        self.assertTrue(images[0]["is_primary"])
        self.assertEqual(images[0]["id"], str(self.img_primary.id))
        self.assertEqual(images[0]["alt"], "Главное фото")
        self.assertIn(self.img_primary.image.name, images[0]["image_url"])
        self.assertEqual(images[0]["image"], images[0]["image_url"])
        self.assertIsNotNone(images[0]["created_at"])

        # Root image_url matches primary image
        self.assertEqual(item_with_img["image_url"], images[0]["image_url"])

        # Remaining photos ordered by created_at
        self.assertFalse(images[1]["is_primary"])
        self.assertEqual(images[1]["id"], str(self.img_sec1.id))
        self.assertFalse(images[2]["is_primary"])
        self.assertEqual(images[2]["id"], str(self.img_sec2.id))

        # Check empty product
        self.assertEqual(item_empty["images"], [])
        self.assertIsNone(item_empty["image_url"])

        # 2. Test Detail Endpoint
        req_detail = self.factory.get(f"/api/main/public/companies/{self.slug}/showcase/{self.prod_with_images.id}/")
        view_detail = PublicCompanyProductDetailAPIView.as_view()
        resp_detail = view_detail(req_detail, slug=self.slug, product_id=self.prod_with_images.id)
        self.assertEqual(resp_detail.status_code, 200)

        self.assertEqual(len(resp_detail.data["images"]), 3)
        self.assertTrue(resp_detail.data["images"][0]["is_primary"])
        self.assertEqual(resp_detail.data["image_url"], resp_detail.data["images"][0]["image_url"])
