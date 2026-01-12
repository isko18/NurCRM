from apps.warehouse.models import WarehouseProductCategory
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

from rest_framework import serializers

class CategorySerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    
    class Meta:
        model = WarehouseProdutCategory
        fields = ("__all__")




