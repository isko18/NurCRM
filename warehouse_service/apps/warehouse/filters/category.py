
import django_filters
from apps.warehouse.models import WarehouseProductCategory


class CategoryFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:

        model = WarehouseProductCategory
        fields = ['name']



