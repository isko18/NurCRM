

from apps.warehouse.models import Warehouse

from rest_framework import serializers


from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin


class WarehouseSerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')


    class Meta:
        model = Warehouse 
        fields = ("id","name","location","status","company","branch")








