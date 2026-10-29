import io
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.main.models import (
    Product,
    ProductCategory,
    ProductPromotionTier,
    ShowcaseBanner,
    ShowcaseDesign,
    ShowcaseDesignVersion,
    ShowcaseMedia,
    ShowcaseOrder,
    ShowcaseOrderItem,
    ShowcasePromoBlock,
    ShowcaseStats,
)
from apps.users.models import Company, SubscriptionPlan
from apps.users.serializers import company_feature_codes

User = get_user_model()


def _make_dummy_image(width=800, height=600, color="red", fmt="JPEG") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class ShowcaseEditorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="owner@nurmarket.test",
            password="password123",
            role="owner",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Nur Market Test",
            slug="nur-market-test",
            owner=self.owner,
            can_view_showcase=True,
        )
        self.owner.company = self.company
        self.owner.owned_company = self.company
        self.owner.save()

        self.cashier = User.objects.create_user(
            email="cashier@nurmarket.test",
            password="password123",
            role="cashier",
            company=self.company,
        )
        self.category = ProductCategory.objects.create(
            company=self.company,
            name="Фрукты",
        )
        self.product = Product.objects.create(
            company=self.company,
            category=self.category,
            name="Яблоки Голден",
            price=Decimal("100.00"),
            stock=True,
        )

    # ------------------------------------------------------------------
    # 2.4 & 2.5: Rights and Features
    # ------------------------------------------------------------------
    def test_2_5_company_feature_codes_contains_showcase_editor(self):
        codes = company_feature_codes(self.company)
        self.assertIn("showcase_editor", codes)

        self.company.can_view_showcase = False
        self.company.save()
        codes_disabled = company_feature_codes(self.company)
        self.assertNotIn("showcase_editor", codes_disabled)

    def test_2_4_cashier_forbidden_from_editing_design(self):
        self.client.force_authenticate(user=self.cashier)
        res = self.client.patch("/api/main/showcase/design/draft/", {"theme": {"background": "#FFFFFF"}}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data.get("code"), "permission_denied")

    # ------------------------------------------------------------------
    # SC-01: Theme, Colors, Contrast warnings
    # ------------------------------------------------------------------
    def test_sc_01_theme_valid_and_contrast_warning(self):
        self.client.force_authenticate(user=self.owner)
        # Low contrast: text #888888 on bg #FFFFFF
        payload = {
            "theme": {
                "background": "#FFFFFF",
                "text": "#888888",
                "accent": "#F5CD15",
                "header_bg": "#111827",
                "header_text": "#FFFFFF",
                "radius": 12,
                "mode": "light",
            }
        }
        res = self.client.patch("/api/main/showcase/design/draft/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        draft = res.data["draft"]
        # ТЗ-BE-2026-05: старый плоский theme переводится в theme.colors (документ нового формата).
        self.assertEqual(draft["theme"]["colors"]["background"], "#FFFFFF")
        self.assertEqual(draft["theme"]["colors"]["text"], "#888888")
        warnings = res.data.get("warnings", [])
        self.assertTrue(any(w["field"] == "theme.colors.text" and w["code"] == "low_contrast" for w in warnings))

    def test_sc_01_theme_invalid_color_returns_400(self):
        self.client.force_authenticate(user=self.owner)
        payload = {"theme": {"background": "invalid-color"}}
        res = self.client.patch("/api/main/showcase/design/draft/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # SC-02: Layout & Columns & Product order
    # ------------------------------------------------------------------
    def test_sc_02_layout_columns_validation(self):
        self.client.force_authenticate(user=self.owner)
        # Valid
        res = self.client.patch(
            "/api/main/showcase/design/draft/",
            {"layout": {"columns": {"desktop": 4, "mobile": 2}, "default_sort": "new"}},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Invalid desktop columns (desktop must be 2..6)
        res_inv = self.client.patch(
            "/api/main/showcase/design/draft/",
            {"layout": {"columns": {"desktop": 8, "mobile": 2}}},
            format="json",
        )
        self.assertEqual(res_inv.status_code, status.HTTP_400_BAD_REQUEST)

        # Invalid mobile columns (mobile must be 1..3)
        res_inv_m = self.client.patch(
            "/api/main/showcase/design/draft/",
            {"layout": {"columns": {"desktop": 3, "mobile": 5}}},
            format="json",
        )
        self.assertEqual(res_inv_m.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sc_02_product_order_endpoint(self):
        self.client.force_authenticate(user=self.owner)
        order_list = [str(self.product.id)]
        res = self.client.patch(
            "/api/main/showcase/design/draft/product-order/",
            {"order": order_list},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["order"], order_list)

    # ------------------------------------------------------------------
    # SC-03: Cards templates
    # ------------------------------------------------------------------
    def test_sc_03_cards_template_validation(self):
        self.client.force_authenticate(user=self.owner)
        # Valid
        res = self.client.patch(
            "/api/main/showcase/design/draft/",
            {"cards": {"template": "compact", "photo_ratio": "4:3", "shadow": True}},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["draft"]["card"]["template"], "compact")  # cards → card (ТЗ-BE-2026-05)

        # Invalid template
        res_inv = self.client.patch(
            "/api/main/showcase/design/draft/",
            {"cards": {"template": "unknown_style"}},
            format="json",
        )
        self.assertEqual(res_inv.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # SC-04: Banners CRUD & Reorder
    # ------------------------------------------------------------------
    def test_sc_04_banners_crud_and_reorder(self):
        self.client.force_authenticate(user=self.owner)
        b1 = ShowcaseBanner.objects.create(
            company=self.company,
            title="Баннер 1",
            place="hero",
            position=0,
        )
        b2 = ShowcaseBanner.objects.create(
            company=self.company,
            title="Баннер 2",
            place="hero",
            position=1,
        )

        res_list = self.client.get("/api/main/showcase/banners/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_list.data), 2)

        # Reorder
        res_reorder = self.client.post(
            "/api/main/showcase/banners/reorder/",
            {"order": [str(b2.id), str(b1.id)]},
            format="json",
        )
        self.assertEqual(res_reorder.status_code, status.HTTP_200_OK)
        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertEqual(b2.position, 0)
        self.assertEqual(b1.position, 1)

    # ------------------------------------------------------------------
    # SC-05: Promo Blocks CRUD
    # ------------------------------------------------------------------
    def test_sc_05_promo_blocks_crud(self):
        self.client.force_authenticate(user=self.owner)
        res_create = self.client.post(
            "/api/main/showcase/promo-blocks/",
            {
                "title": "Акция недели",
                "source": {"type": "promotions", "ids": []},
                "style": "carousel",
                "show_timer": True,
                "max_items": 10,
                "position": 1,
            },
            format="json",
        )
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        block_id = res_create.data["id"]

        res_get = self.client.get(f"/api/main/showcase/promo-blocks/{block_id}/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_get.data["title"], {"ru": "Акция недели"})  # тексты по языкам (3.11)

    # ------------------------------------------------------------------
    # SC-06: Preview Link, Publish, Discard, Versions, Restore
    # ------------------------------------------------------------------
    def test_sc_06_lifecycle(self):
        self.client.force_authenticate(user=self.owner)

        # 1. Preview link
        res_prev = self.client.post("/api/main/showcase/design/preview-link/")
        self.assertEqual(res_prev.status_code, status.HTTP_200_OK)
        self.assertIn("preview=", res_prev.data["url"])
        token = res_prev.data["url"].split("preview=")[1]

        # 2. Modify draft
        self.client.patch(
            "/api/main/showcase/design/draft/",
            {"theme": {"background": "#000000", "text": "#FFFFFF"}},
            format="json",
        )

        # 3. Publish
        res_pub = self.client.post(
            "/api/main/showcase/design/publish/",
            HTTP_IDEMPOTENCY_KEY="pub-key-1",
        )
        self.assertEqual(res_pub.status_code, status.HTTP_200_OK)
        v = res_pub.data["version"]
        self.assertEqual(v, 2)

        # 4. Check versions
        res_vers = self.client.get("/api/main/showcase/design/versions/")
        self.assertEqual(res_vers.status_code, status.HTTP_200_OK)
        self.assertTrue(any(x["version"] == 2 for x in res_vers.data))

        # 5. Modify draft again
        self.client.patch(
            "/api/main/showcase/design/draft/",
            {"theme": {"background": "#FF0000"}},
            format="json",
        )

        # 6. Discard -> resets to published (#000000)
        res_disc = self.client.post("/api/main/showcase/design/discard/")
        self.assertEqual(res_disc.status_code, status.HTTP_200_OK)
        self.assertEqual(res_disc.data["draft"]["theme"]["colors"]["background"], "#000000")

        # 7. Restore version 2
        res_rest = self.client.post(f"/api/main/showcase/design/versions/{v}/restore/")
        self.assertEqual(res_rest.status_code, status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # SC-07: Media upload and delete
    # ------------------------------------------------------------------
    def test_sc_07_media_upload_and_delete(self):
        self.client.force_authenticate(user=self.owner)
        img_bytes = _make_dummy_image(1200, 800)
        upload_file = SimpleUploadedFile("banner.jpg", img_bytes, content_type="image/jpeg")

        # Upload
        res = self.client.post(
            "/api/main/showcase/media/",
            {"file": upload_file},
            format="multipart",
            HTTP_IDEMPOTENCY_KEY="upload-1",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        media_id = res.data["id"]
        urls = res.data["urls"]
        self.assertIn("1920", urls)
        self.assertIn("1080", urls)
        self.assertIn("640", urls)

        # Create banner using this media
        banner = ShowcaseBanner.objects.create(
            company=self.company,
            title="Баннер с медиа",
            image_id=media_id,
            active=True,
        )

        # Try to delete media -> 409 Conflict
        res_del_conflict = self.client.delete(f"/api/main/showcase/media/{media_id}/")
        self.assertEqual(res_del_conflict.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_del_conflict.data.get("code"), "media_in_use")

        # Delete banner, then delete media -> 204
        banner.delete()
        res_del_ok = self.client.delete(f"/api/main/showcase/media/{media_id}/")
        self.assertEqual(res_del_ok.status_code, status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # SC-09: Public Showcase Design & Hidden items
    # ------------------------------------------------------------------
    def test_sc_09_public_design_etag_and_preview(self):
        # Publish some design
        self.client.force_authenticate(user=self.owner)
        self.client.patch(
            "/api/main/showcase/design/draft/",
            {"theme": {"background": "#F0F0F0", "text": "#111827"}},
            format="json",
        )
        self.client.post("/api/main/showcase/design/publish/")

        # Public client
        pub_client = APIClient()
        res = pub_client.get(f"/api/main/public/companies/{self.company.slug}/showcase/design/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        etag = res["ETag"]

        # If-None-Match -> 304
        res_304 = pub_client.get(
            f"/api/main/public/companies/{self.company.slug}/showcase/design/",
            HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(res_304.status_code, status.HTTP_304_NOT_MODIFIED)

        # Preview token
        self.client.force_authenticate(user=self.owner)
        prev_url = self.client.post("/api/main/showcase/design/preview-link/").data["url"]
        token = prev_url.split("preview=")[1]

        res_preview = pub_client.get(
            f"/api/main/public/companies/{self.company.slug}/showcase/design/?preview={token}"
        )
        self.assertEqual(res_preview.status_code, status.HTTP_200_OK)
        self.assertTrue(res_preview.data.get("preview"))

    def test_sc_09_hidden_products_not_shown_in_public_showcase(self):
        # Hide product
        self.client.force_authenticate(user=self.owner)
        self.client.patch(
            "/api/main/showcase/design/draft/",
            {"layout": {"hidden_products": [str(self.product.id)]}},
            format="json",
        )
        self.client.post("/api/main/showcase/design/publish/")

        pub_client = APIClient()
        # List should exclude hidden product
        res_list = pub_client.get(f"/api/main/public/companies/{self.company.slug}/showcase/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        product_ids = [p["id"] for p in res_list.data.get("results", [])]
        self.assertNotIn(str(self.product.id), product_ids)

        # Detail should return 404
        res_detail = pub_client.get(
            f"/api/main/public/companies/{self.company.slug}/showcase/{self.product.id}/"
        )
        self.assertEqual(res_detail.status_code, status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------------
    # SC-10: Orders from showcase
    # ------------------------------------------------------------------
    def test_sc_10_public_order_creation_and_promotions(self):
        # Promotion tier: buy >= 200 som -> 10% discount
        ProductPromotionTier.objects.create(
            product=self.product,
            min_amount=Decimal("150.00"),
            discount_percent=Decimal("10.00"),
        )

        pub_client = APIClient()
        order_payload = {
            "customer": {"name": "Айбек", "phone": "+996555123456"},
            "items": [{"product": str(self.product.id), "qty": "2.000"}],  # 2 x 100 = 200 -> 10% off -> 180 som
            "delivery": {"type": "pickup"},
            "comment": "Тестовый заказ",
        }
        res = pub_client.post(
            f"/api/main/public/companies/{self.company.slug}/orders/",
            order_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="order-key-1",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["number"], 1)
        self.assertEqual(res.data["status"], "new")
        self.assertEqual(Decimal(res.data["total"]), Decimal("180.00"))
        order_id = res.data["id"]

        # Staff can view and update order
        self.client.force_authenticate(user=self.cashier)
        res_orders = self.client.get("/api/main/showcase/orders/?status=new")
        self.assertEqual(res_orders.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_orders.data), 1)

        # Accept order
        res_patch = self.client.patch(
            f"/api/main/showcase/orders/{order_id}/",
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_patch.data["status"], "accepted")

    # ------------------------------------------------------------------
    # SC-11: Showcase Statistics & Tracking
    # ------------------------------------------------------------------
    def test_sc_11_statistics_and_tracking(self):
        pub_client = APIClient()
        banner = ShowcaseBanner.objects.create(
            company=self.company,
            title="Баннер скидок",
            active=True,
        )

        # Track view, add_to_cart, and banner click
        pub_client.post(
            f"/api/main/public/companies/{self.company.slug}/showcase/track/",
            {"event": "view"},
            format="json",
        )
        pub_client.post(
            f"/api/main/public/companies/{self.company.slug}/showcase/track/",
            {"event": "add_to_cart"},
            format="json",
        )
        pub_client.post(
            f"/api/main/public/companies/{self.company.slug}/showcase/track/",
            {"event": "banner_click", "banner_id": str(banner.id), "session_id": "sess_123"},
            format="json",
        )
        # Duplicate click in same session -> deduplicated
        res_dup = pub_client.post(
            f"/api/main/public/companies/{self.company.slug}/showcase/track/",
            {"event": "banner_click", "banner_id": str(banner.id), "session_id": "sess_123"},
            format="json",
        )
        self.assertEqual(res_dup.data.get("status"), "already_tracked")

        # Owner gets stats
        self.client.force_authenticate(user=self.owner)
        res_stats = self.client.get("/api/main/showcase/stats/")
        self.assertEqual(res_stats.status_code, status.HTTP_200_OK)
        self.assertEqual(res_stats.data["views"], 1)
        self.assertEqual(res_stats.data["add_to_cart"], 1)
        clicks = res_stats.data["banner_clicks"]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0]["clicks"], 1)
