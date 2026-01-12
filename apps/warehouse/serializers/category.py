from apps.warehouse.models import WarehouseProductCategory
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin

from rest_framework import serializers


from apps.warehouse.utils import _restrict_pk_queryset_strict


class CategorySerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=WarehouseProductCategory.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = WarehouseProductCategory
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), ProductBrand.objects.all(), comp, br)



