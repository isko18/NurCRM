from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.models import (
    WarehouseProduct,
    WarehouseProductPackage,
    WarehouseProductImage
)


from apps.warehouse.serializers.product import (
    WarehouseProductSerializer,
    WarehouseProductPackageSerializer,
    WarehouseProductImageSerializer
)




from rest_framework import generics
from django_filters.rest_framework import DjangoFilterBackend

from apps.warehouse.filters import ProductFilter



class ProductView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductSerializer

    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend]
    

    def get_queryset(self):
        warehouse_uuid = self.kwargs.get("warehouse_uuid")  # берем UUID из URL
        qs = (
            WarehouseProduct.objects
            .select_related('brand', 'category', 'warehouse')
            .prefetch_related('images', 'packages', 'characteristics')
        )
        if warehouse_uuid:
            qs = qs.filter(warehouse__id=warehouse_uuid)  # фильтруем по складу
        return qs


class ProductDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductSerializer

    queryset = (
        WarehouseProduct.objects
        .select_related('brand', 'category', 'warehouse')
        .prefetch_related('images', 'packages', 'characteristics')
        .all()
    )






class ProductImagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductImageSerializer

    def get_queryset(self):
        product_uuid = self.kwargs.get("product_uuid")
        return WarehouseProductImage.objects.filter(product__id=product_uuid)

    def perform_create(self, serializer):
        product_uuid = self.kwargs.get("product_uuid")
        serializer.save(product_id=product_uuid)


class ProductImageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductImageSerializer
    queryset = WarehouseProductImage.objects.all()






class ProductPackagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductPackageSerializer

    def get_queryset(self):
        product_uuid = self.kwargs.get("product_uuid")
        return WarehouseProductPackage.objects.filter(product__id=product_uuid)

    def perform_create(self, serializer):
        product_uuid = self.kwargs.get("product_uuid")
        serializer.save(product_id=product_uuid)


class ProductPackageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductPackageSerializer
    queryset = WarehouseProductPackage.objects.all()










