from apps.warehouse.models import WarehouseProductPackage
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

from rest_framework import serializers


class WarehouseProductPackageSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = WarehouseProductPackage
        fields = (
                "id", "product", 
                "name", "quantity_in_package", 
                "unit", "created_at")
        
        read_only_fields = ("id", "product", "created_at")




