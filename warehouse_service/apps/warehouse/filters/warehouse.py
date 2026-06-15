from apps.warehouse.models import Warehouse 

import django_filters



class WarehouseFilter(django_filters.FilterSet):
    # фильтр по имени (частичное совпадение, ignore case)
    name = django_filters.CharFilter(field_name='name', lookup_expr='icontains')
    
    # фильтр по статусу
    status = django_filters.ChoiceFilter(field_name='status', choices=Warehouse.Status.choices)
    
    # фильтр по компании
    company = django_filters.NumberFilter(field_name='company__id')
    
    # фильтр по филиалу (branch)
    branch = django_filters.NumberFilter(field_name='branch__id')
    
    # Фильтр по дате (после)
    created_after = django_filters.DateTimeFilter(field_name='created_date', lookup_expr='gte')
    
    # Фильтр по дате (после)
    created_before = django_filters.DateTimeFilter(field_name='created_date', lookup_expr='lte')

    class Meta:
        model = Warehouse
        fields = ['name', 'status', 'company', 'branch']


