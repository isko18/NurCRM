# apps/products/serializers_public.py
from decimal import Decimal
from rest_framework import serializers

from apps.users.models import Company
from ..models import Product, ProductCharacteristics, ProductPackage


class PublicCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "slug", "phones_howcase"]


class PublicProductPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductPackage
        fields = ["id", "name", "quantity_in_package", "unit", "piece_unit_price"]


class PublicProductCharacteristicsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCharacteristics
        fields = [
            "height_cm",
            "width_cm",
            "depth_cm",
            "factual_weight_kg",
            "description",
        ]


class PublicProductSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="name", read_only=True)
    category_title = serializers.CharField(source="category.name", read_only=True)
    brand_title = serializers.CharField(source="brand.name", read_only=True)

    image_url = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    final_price = serializers.SerializerMethodField()
    is_new = serializers.SerializerMethodField()

    characteristics = PublicProductCharacteristicsSerializer(read_only=True)
    packages = PublicProductPackageSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "kind",
            "name",
            "title",
            "description",
            "unit",
            "is_weight",
            "stock",
            "is_new",
            "country",
            "barcode",
            "article",

            "price",
            "discount_percent",
            "final_price",

            "category",
            "category_title",
            "brand",
            "brand_title",

            "expiration_date",
            "created_at",

            "image_url",
            "images",
            "characteristics",
            "packages",
        ]

    def _get_sorted_images(self, obj: Product):
        # Используем предзагруженные связанные объекты obj.images.all() без .filter(),
        # чтобы избежать N+1 запросов к базе данных.
        imgs = [img for img in obj.images.all() if getattr(img, "image", None)]
        return sorted(
            imgs,
            key=lambda x: (
                not bool(getattr(x, "is_primary", False)),
                getattr(x, "created_at", None) or "",
            ),
        )

    def get_images(self, obj: Product):
        request = self.context.get("request")
        sorted_imgs = self._get_sorted_images(obj)
        result = []
        for img in sorted_imgs:
            url = request.build_absolute_uri(img.image.url) if request else img.image.url
            result.append(
                {
                    "id": str(img.id),
                    "image": url,
                    "image_url": url,
                    "alt": getattr(img, "alt", "") or "",
                    "is_primary": bool(getattr(img, "is_primary", False)),
                    "created_at": img.created_at.isoformat() if getattr(img, "created_at", None) else None,
                }
            )
        return result

    def get_image_url(self, obj: Product):
        sorted_imgs = self._get_sorted_images(obj)
        if not sorted_imgs:
            return None
        primary_img = sorted_imgs[0]
        request = self.context.get("request")
        return request.build_absolute_uri(primary_img.image.url) if request else primary_img.image.url

    def get_final_price(self, obj: Product):
        price = obj.price or Decimal("0")
        disc = obj.discount_percent or Decimal("0")
        if disc <= 0:
            return price.quantize(Decimal("0.01")) if hasattr(price, "quantize") else price
        return (price * (Decimal("1") - disc / Decimal("100"))).quantize(Decimal("0.01"))

    def get_is_new(self, obj: Product) -> bool:
        if not getattr(obj, "created_at", None):
            return False
        from django.utils import timezone
        from datetime import timedelta
        return (timezone.now() - obj.created_at) <= timedelta(days=14)

