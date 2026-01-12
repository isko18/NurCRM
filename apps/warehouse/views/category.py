from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.serializers.category import CategorySerializer
from apps.warehouse.filters import CategoryFilter
from apps.warehouse.models import WarehouseProductCategory

from rest_framework import generics

from django_filters.rest_framework import DjangoFilterBackend



class CategoryView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    
    serializer_class = CategorySerializer
    queryset = WarehouseProductCategory.objects.select_related("company","branch").all()
    
    filter_backends = [DjangoFilterBackend]
    filterset_class = (CategoryFilter)


class CategoryDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    
    serializer_class = CategorySerializer
    queryset = WarehouseProductCategory.objects.select_related("company","branch").all()


