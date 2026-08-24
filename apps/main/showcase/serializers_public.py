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
            "characteristics",
            "packages",
        ]

    def get_image_url(self, obj: Product):
        img = obj.images.filter(is_primary=True).first() or obj.images.first()
        if not img or not img.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(img.image.url) if request else img.image.url

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

