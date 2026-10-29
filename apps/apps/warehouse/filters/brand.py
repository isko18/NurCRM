





import django_filters
from apps.warehouse.models import WarehouseProductBrand

class BrandFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    


    class Meta:

        model = WarehouseProductBrand
        fields = ['name']

