from django.shortcuts import get_object_or_404

from rest_framework import generics
from django_filters.rest_framework import DjangoFilterBackend

from .mixins import CompanyBranchRestrictedMixin

from apps.warehouse.models import (
    Warehouse,
    WarehouseProduct,
    WarehouseProductPackage,
    WarehouseProductImage,
)

from apps.warehouse.serializers.product.product import WarehouseProductSerializer
from apps.warehouse.serializers.product.image import WarehouseProductImageSerializer
from apps.warehouse.serializers.product.package import WarehouseProductPackageSerializer


from apps.warehouse.filters import ProductFilter


class ProductView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductSerializer
    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend]

    def _get_warehouse(self):
        return get_object_or_404(Warehouse, id=self.kwargs.get("warehouse_uuid"))

    def get_queryset(self):
        wh = self._get_warehouse()
        return (
            WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
            .filter(warehouse=wh)
        )

    def perform_create(self, serializer):
        wh = self._get_warehouse()

        # company/branch берём строго со склада (чтобы не было NULL и подмен)
        serializer.save(
            warehouse=wh,
            company=wh.company,
            branch=wh.branch,
        )


class ProductDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductSerializer
    lookup_field = "id"
    lookup_url_kwarg = "product_uuid"

    def get_queryset(self):
        qs = (
            WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch")
            .select_related("characteristics")
            .prefetch_related("images", "packages")
        )

        # Если у тебя URL для detail тоже под складом — можно дополнительно зажать по warehouse_uuid:
        warehouse_uuid = self.kwargs.get("warehouse_uuid")
        if warehouse_uuid:
            qs = qs.filter(warehouse_id=warehouse_uuid)

        return qs

    def perform_update(self, serializer):
        """
        Запрещаем менять склад через PUT/PATCH.
        Если detail-роут не содержит warehouse_uuid, просто не даём менять warehouse вообще.
        """
        if "warehouse" in serializer.validated_data:
            serializer.validated_data.pop("warehouse", None)
        serializer.save()


class ProductImagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductImageSerializer

    def get_queryset(self):
        product_uuid = self.kwargs.get("product_uuid")
        return WarehouseProductImage.objects.filter(product_id=product_uuid)

    def perform_create(self, serializer):
        product_uuid = self.kwargs.get("product_uuid")
        serializer.save(product_id=product_uuid)


class ProductImageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductImageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "image_uuid"
    queryset = WarehouseProductImage.objects.all()


class ProductPackagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductPackageSerializer

    def get_queryset(self):
        product_uuid = self.kwargs.get("product_uuid")
        return WarehouseProductPackage.objects.filter(product_id=product_uuid)

    def perform_create(self, serializer):
        product_uuid = self.kwargs.get("product_uuid")
        serializer.save(product_id=product_uuid)


class ProductPackageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductPackageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "package_uuid"
    queryset = WarehouseProductPackage.objects.all()
