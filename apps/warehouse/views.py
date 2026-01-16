from typing import Optional

from rest_framework import generics, permissions
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404

from apps.users.models import Branch

from .serializers import (
    WarehouseSerializer,
    BrandSerializer,
    CategorySerializer,
    WarehouseProductSerializer,
    WarehouseProductImageSerializer,
    WarehouseProductPackageSerializer,
)

from apps.warehouse import models as m
from apps.warehouse.filters import (
    WarehouseFilter,
    BrandFilter,
    ProductFilter,
)


# ---- Company/branch mixin (copied/adapted) ----
class CompanyBranchRestrictedMixin:
    permission_classes = [permissions.IsAuthenticated]

    def _request(self):
        return getattr(self, "request", None)

    def _user(self):
        req = self._request()
        return getattr(req, "user", None) if req else None

    def _company(self):
        u = self._user()
        if not u or not getattr(u, "is_authenticated", False):
            return None
        if getattr(u, "is_superuser", False):
            return None

        company = getattr(u, "owned_company", None) or getattr(u, "company", None)
        if company:
            return company

        br = getattr(u, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _fixed_branch_from_user(self, company) -> Optional[Branch]:
        req = self._request()
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        try:
            if hasattr(user, "branches"):
                qs = user.branches.all()
                if company_id:
                    qs = qs.filter(company_id=company_id)
                b = qs.first()
                if b:
                    return b
        except Exception:
            pass

        try:
            if hasattr(user, "branch_memberships"):
                ms = user.branch_memberships.select_related("branch")
                if company_id:
                    ms = ms.filter(branch__company_id=company_id)
                mobj = ms.first()
                if mobj and getattr(mobj, "branch", None):
                    return mobj.branch
        except Exception:
            pass

        branch_ids = getattr(user, "branch_ids", None)
        if branch_ids:
            try:
                b = Branch.objects.filter(id__in=list(branch_ids), company_id=company_id).first()
                if b:
                    return b
            except Exception:
                pass

        if req and hasattr(req, "branch"):
            b = getattr(req, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        return None

    def _auto_branch(self) -> Optional[Branch]:
        req = self._request()
        user = self._user()
        if not req or not user or not getattr(user, "is_authenticated", False):
            return None

        cached = getattr(req, "_cached_auto_branch", None)
        if cached is not None:
            return cached

        company = self._company()
        company_id = getattr(company, "id", None)

        fixed_branch = self._fixed_branch_from_user(company)
        if fixed_branch is not None:
            setattr(req, "branch", fixed_branch)
            setattr(req, "_cached_auto_branch", fixed_branch)
            return fixed_branch

        branch_id = None
        if hasattr(req, "query_params"):
            branch_id = req.query_params.get("branch")
        elif hasattr(req, "GET"):
            branch_id = req.GET.get("branch")

        if branch_id and company_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(req, "branch", br)
                setattr(req, "_cached_auto_branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                pass

        setattr(req, "_cached_auto_branch", None)
        return None

    @staticmethod
    def _model_has_field(model, field_name: str) -> bool:
        try:
            return any(f.name == field_name for f in model._meta.get_fields())
        except Exception:
            return False

    def _filter_qs_company_branch(self, qs, company_field: Optional[str] = None, branch_field: Optional[str] = None):
        company = self._company()
        branch = self._auto_branch()
        model = qs.model

        if company is not None:
            if company_field:
                qs = qs.filter(**{company_field: company})
            elif self._model_has_field(model, "company"):
                qs = qs.filter(company=company)

        if branch_field:
            if branch is not None:
                qs = qs.filter(**{branch_field: branch})
            else:
                qs = qs.filter(**{f"{branch_field}__isnull": True})
        elif self._model_has_field(model, "branch"):
            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

        return qs

    def get_queryset(self):
        assert hasattr(self, "queryset") and self.queryset is not None, (
            f"{self.__class__.__name__} must define .queryset or override get_queryset()."
        )
        return self._filter_qs_company_branch(self.queryset.all())

    def get_serializer_context(self):
        ctx = super().get_serializer_context() if hasattr(super(), "get_serializer_context") else {}
        ctx["request"] = self.request
        return ctx

    def _save_with_company_branch(self, serializer, **extra):
        model = serializer.Meta.model
        kwargs = dict(extra)

        company = self._company()
        if self._model_has_field(model, "company") and company is not None:
            kwargs.setdefault("company", company)

        if self._model_has_field(model, "branch"):
            branch = self._auto_branch()
            if branch is not None:
                kwargs["branch"] = branch

        serializer.save(**kwargs)

    def perform_create(self, serializer):
        self._save_with_company_branch(serializer)

    def perform_update(self, serializer):
        self._save_with_company_branch(serializer)


# ==== Warehouse views ====
class WarehouseView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = m.Warehouse.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = WarehouseFilter


class WarehouseDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = m.Warehouse.objects.select_related("company", "branch").all()


# ==== Brand ====
class BrandView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = m.WarehouseProductBrand.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = BrandFilter


class BrandDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BrandSerializer
    queryset = m.WarehouseProductBrand.objects.select_related("company", "branch").all()


# ==== Category ====
class CategoryView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    queryset = m.WarehouseProductCategory.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = None


class CategoryDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CategorySerializer
    queryset = m.WarehouseProductCategory.objects.select_related("company", "branch").all()


# ==== Products ====
class ProductView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductSerializer
    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend]

    def _get_warehouse(self):
        return get_object_or_404(m.Warehouse, id=self.kwargs.get("warehouse_uuid"))

    def get_queryset(self):
        wh = self._get_warehouse()
        return (
            m.WarehouseProduct.objects
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
            m.WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
        )

    def perform_update(self, serializer):
        serializer.validated_data.pop("warehouse", None)
        serializer.validated_data.pop("company", None)
        serializer.validated_data.pop("branch", None)
        serializer.save()


class ProductImagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductImageSerializer

    def _get_product(self):
        return get_object_or_404(m.WarehouseProduct, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return m.WarehouseProductImage.objects.filter(product=product)

    def perform_create(self, serializer):
        product = self._get_product()
        serializer.save(product=product)


class ProductImageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductImageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "image_uuid"

    def get_queryset(self):
        return m.WarehouseProductImage.objects.filter(product_id=self.kwargs.get("product_uuid"))


class ProductPackagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductPackageSerializer

    def _get_product(self):
        return get_object_or_404(m.WarehouseProduct, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return m.WarehouseProductPackage.objects.filter(product=product)

    def perform_create(self, serializer):
        product = self._get_product()
        serializer.save(product=product)


class ProductPackageDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductPackageSerializer
    lookup_field = "id"
    lookup_url_kwarg = "package_uuid"

    def get_queryset(self):
        return m.WarehouseProductPackage.objects.filter(product_id=self.kwargs.get("product_uuid"))
