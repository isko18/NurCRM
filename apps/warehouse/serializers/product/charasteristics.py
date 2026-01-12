
from rest_framework import serializers
from apps.warehouse.models import WarehouseProductCharasteristics
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

class ProductCharacteristicsSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = WarehouseProductCharasteristics
        fields = (
            "height_cm", 
            "width_cm", 
            "depth_cm", 
            "factual_weight_kg", 
            "description"
        )



