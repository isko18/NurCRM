from apps.warehouse.models import WarehouseProductPackage
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

from rest_framework import serializers


class ProductPackage(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    
    class Meta:
        model = WarehouseProductPackage
        fields = ("__all__")
        read_only_fields = ("product") 






