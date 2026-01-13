from apps.warehouse.models import WarehouseProductBrand 
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin
from apps.warehouse.utils import _restrict_pk_queryset_strict
from rest_framework import serializers


class BrandSerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=WarehouseProductBrand.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = WarehouseProductBrand
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), WarehouseProductBrand.objects.all(), comp, br)








