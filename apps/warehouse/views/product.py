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
        warehouse_uuid = self.kwargs.get("warehouse_uuid")
        if not warehouse_uuid:
            return None
        return get_object_or_404(Warehouse, id=warehouse_uuid)

    def get_queryset(self):
        wh = self._get_warehouse()

        qs = (
            WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch")
            # characteristics обычно OneToOne -> select_related, если реально M2M/Reverse FK — тогда верни prefetch
            .select_related("characteristics")
            .prefetch_related("images", "packages")
        )

        if wh:
            qs = qs.filter(warehouse=wh)

        # ВАЖНО: CompanyBranchRestrictedMixin обычно сам режет по company/branch.
        # Если он НЕ режет — надо добавить фильтрацию здесь.
        return qs

    def perform_create(self, serializer):
        """
        Критично: warehouse берём только из URL, а не из body.
        Это полностью убивает подмену склада.
        """
        wh = self._get_warehouse()

        # Если у тебя миксин проставляет company/branch в request — ок.
        # Иначе можно сделать так:
        # company = getattr(self.request.user, "company", None) or getattr(self.request.user, "owned_company", None)
        # branch = getattr(self.request, "branch", None) or getattr(self.request.user, "branch", None)
        # serializer.save(company=company, branch=branch, warehouse=wh)

        serializer.save(warehouse=wh)


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
