# apps/cafe/serializers_public.py
from rest_framework import serializers
from apps.users.models import Company, Branch
from ..models import Category, MenuItem, Kitchen


class PublicCafeCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "slug", "phone", "phones_howcase"]

    
class PublicBranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "code", "address", "phone", "email"]


class PublicKitchenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Kitchen
        fields = ["id", "title", "number"]


class PublicMenuItemSerializer(serializers.ModelSerializer):
    category_title = serializers.CharField(source="category.title", read_only=True)
    kitchen_title = serializers.CharField(source="kitchen.title", read_only=True)
    kitchen_number = serializers.IntegerField(source="kitchen.number", read_only=True)

    image_url = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id",
            "title",
            "price",
            "is_active",
            "category",
            "category_title",
            "kitchen",
            "kitchen_title",
            "kitchen_number",
            "image_url",
            "created_at",
        ]

    def get_image_url(self, obj: MenuItem):
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class PublicCategorySerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "title", "items"]

    def get_items(self, obj: Category):
        # items_prefetched будет если во view сделаем prefetch
        qs = getattr(obj, "items_prefetched", None)
        if qs is None:
            qs = obj.items.all()
        return PublicMenuItemSerializer(qs, many=True, context=self.context).data
