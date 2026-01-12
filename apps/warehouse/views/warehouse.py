from rest_framework import generics

from django_filters.rest_framework import DjangoFilterBackend

from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.serializers.warehouse import WarehouseSerializer

from apps.warehouse.models import Warehouse

from apps.warehouse.filters import WarehouseFilter




class WarehouseView(CompanyBranchRestrictedMixin,generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company","branch").all()    
    
    filter_backends = [DjangoFilterBackend]
    filteret_class = WarehouseFilter


class WarehouseDetailView(CompanyBranchRestrictedMixin,generics.RetrieveUpdateDestroyAPIView)):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company","branch").all()    











