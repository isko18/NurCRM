
import django_filters
from apps.warehouse.models import WarehouseProduct

class ProductFilter(django_filters.FilterSet):
    # Поиск по имени или артикулу (частичное совпадение)
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    article = django_filters.CharFilter(field_name="article", lookup_expr="icontains")
    
    # Фильтрация по диапазону цены
    price_min = django_filters.NumberFilter(field_name="price", lookup_expr="gte")
    price_max = django_filters.NumberFilter(field_name="price", lookup_expr="lte")

    # Фильтрация по закупочной цене (опционально)
    purchase_price_min = django_filters.NumberFilter(field_name="purchase_price", lookup_expr="gte")
    purchase_price_max = django_filters.NumberFilter(field_name="purchase_price", lookup_expr="lte")
    
    # Фильтрация по наценке
    markup_min = django_filters.NumberFilter(field_name="markup_percent", lookup_expr="gte")
    markup_max = django_filters.NumberFilter(field_name="markup_percent", lookup_expr="lte")
    
    # FK поля
    brand = django_filters.ModelChoiceFilter(queryset=WarehouseProduct.objects.all())
    category = django_filters.ModelChoiceFilter(queryset=WarehouseProduct.objects.all())
    warehouse = django_filters.ModelChoiceFilter(queryset=WarehouseProduct.objects.all())
    product_group = django_filters.UUIDFilter(field_name="product_group_id")
    
    # Прочее
    status = django_filters.ChoiceFilter(choices=WarehouseProduct.Status.choices)
    stock = django_filters.BooleanFilter(field_name="stock")

    # По дате создания (опционально)
    created_after = django_filters.DateFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.DateFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = WarehouseProduct
        fields = [
            "brand",
            "category",
            "warehouse",
            "product_group",
            "status",
            "stock",
            "name",
            "article",
            "price_min",
            "price_max",
            "purchase_price_min",
            "purchase_price_max",
            "markup_min",
            "markup_max",
            "created_after",
            "created_before",
        ]
