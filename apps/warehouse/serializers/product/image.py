from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin
from rest_framework import serializers
from apps.warehouse.models import WarehouseProductImage


class WarehouseProductImageSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True)
    image_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WarehouseProductImage
        fields = ("id","product","image","image_url","is_primary","alt","created_at")
        read_only_fields = ("product",)
        

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not obj.image:
            return None
        url = obj.image.url
        if request:
            return request.build_absolute_uri(url)
        return url


