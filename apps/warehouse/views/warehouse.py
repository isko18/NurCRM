from rest_framework import generics

from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.serializers.warehouse import WarehouseSerializer

from apps.warehouse.models import Warehouse


class WarehouseView(CompanyBranchRestrictedMixin,generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company","branch").all()    



class WarehouseRetrieveView(CompanyBranchRestrictedMixin,generics.RetrieveUpdateDestroyAPIView)):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company","branch").all()    











