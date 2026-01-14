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
        return (
            WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
        )

    def perform_update(self, serializer):
        # не даём менять склад/компанию/филиал через update
        serializer.validated_data.pop("warehouse", None)
        serializer.validated_data.pop("company", None)
        serializer.validated_data.pop("branch", None)
        serializer.save()


class ProductImagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductImageSerializer

    def _get_product(self):
        return get_object_or_404(WarehouseProduct, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return WarehouseProductImage.objects.filter(product=product)

    def perform_create(self, serializer):
        product = self._get_product()
        serializer.save(product=product)


class ProductImageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductImageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "image_uuid"

    def get_queryset(self):
        # важно: ограничиваем только изображениями нужного продукта
        return WarehouseProductImage.objects.filter(product_id=self.kwargs.get("product_uuid"))


class ProductPackagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductPackageSerializer

    def _get_product(self):
        return get_object_or_404(WarehouseProduct, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return WarehouseProductPackage.objects.filter(product=product)

    def perform_create(self, serializer):
        product = self._get_product()
        serializer.save(product=product)


class ProductPackageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductPackageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "package_uuid"

    def get_queryset(self):
        return WarehouseProductPackage.objects.filter(product_id=self.kwargs.get("product_uuid"))
