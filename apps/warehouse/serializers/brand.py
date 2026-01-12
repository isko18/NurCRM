from apps.warehouse.models import WarehouseProductBrand 
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

from rest_framework import serializers


class BrandSerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    

    class Meta:
        model = WarehouseProductBrand
        fields = ("__all__")
        







