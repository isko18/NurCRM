from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.serializers.brand import BrandSerializer
from apps.warehouse.filters import BrandFilter
from apps.warehouse.models import WarehouseProductBrand

from rest_framework import generics

from django_filters.rest_framework import DjangoFilterBackend



class BrandView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    
    serializer_class = BrandSerializer
    queryset = WarehouseProductBrand.objects.select_related("company","branch").all()
    
    filter_backends = [DjangoFilterBackend]
    filterset_class = (BrandFilter)


class BrandDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    
    serializer_class = BrandSerializer
    queryset = WarehouseProductBrand.objects.select_related("company","branch").all()



