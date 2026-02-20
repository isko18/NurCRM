from typing import Optional

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from decimal import Decimal
from django.db import IntegrityError
from django.db.models import Count, Sum, DecimalField, Value as V
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.users.models import Branch

from .serializers import (
    WarehouseSerializer,
    BrandSerializer,
    CategorySerializer,
    WarehouseProductGroupSerializer,
    WarehouseProductSerializer,
    WarehouseProductImageSerializer,
    WarehouseProductPackageSerializer,
    AgentRequestCartSerializer,
    AgentRequestItemSerializer,
    AgentRequestCartActionSerializer,
    AgentStockBalanceSerializer,
    CompanyWarehouseAgentSerializer,
)

from apps.warehouse import models as m
from apps.warehouse.filters import (
    WarehouseFilter,
    BrandFilter,
    ProductFilter,
)
from apps.utils import _is_owner_like


def _company_ids_for_warehouse_access(user):
    """
    Список id компаний, к складам которых пользователь имеет доступ:
    владелец (owned_company), сотрудник (company) или активный агент (CompanyWarehouseAgent).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    ids = set()
    owned = getattr(user, "owned_company_id", None) or (
        getattr(user, "owned_company", None) and getattr(user.owned_company, "id", None)
    )
    if owned:
        ids.add(owned)
    emp = getattr(user, "company_id", None)
    if emp:
        ids.add(emp)
    qs = m.CompanyWarehouseAgent.objects.filter(
        user=user,
        status=m.CompanyWarehouseAgent.Status.ACTIVE,
    ).values_list("company_id", flat=True)
    for cid in qs:
        ids.add(cid)
    return list(ids)


# ---- Barcode helpers ----
def _parse_scale_barcode(barcode: str):
    """
    EAN-13 весовой штрихкод формата:
    PP CCCCC WWWWW K
    """
    if not barcode or len(barcode) != 13 or not barcode.isdigit():
        return None

    prefix = barcode[0:2]
    plu_digits = barcode[2:7]
    weight_digits = barcode[7:12]

    try:
        plu_int = int(plu_digits)
        weight_raw = int(weight_digits)
    except ValueError:
        return None

    weight_kg = Decimal(weight_raw) / Decimal("1000")

    return {
        "prefix": prefix,
        "plu": plu_int,
        "weight_raw": weight_raw,
        "weight_kg": weight_kg,
    }


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

        company = getattr(u, "owned_company", None) or getattr(u, "company", None)
        if company:
            return company

        br = getattr(u, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        # Агент без своей компании: первая компания, где он активный агент (для контекста)
        first = m.CompanyWarehouseAgent.objects.filter(
            user=u,
            status=m.CompanyWarehouseAgent.Status.ACTIVE,
        ).select_related("company").first()
        if first:
            return first.company
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
        user = self._user()

        company_ids = _company_ids_for_warehouse_access(user) if user else []
        if company is None and not company_ids:
            return qs.none()

        if company is not None:
            company_ids = [company.id]
        elif not company_ids:
            return qs.none()

        if company_field:
            qs = qs.filter(**{f"{company_field}__in": company_ids})
        elif self._model_has_field(model, "company"):
            qs = qs.filter(company_id__in=company_ids)

        if branch_field:
            if branch is not None:
                qs = qs.filter(**{branch_field: branch})
        elif self._model_has_field(model, "branch") and branch is not None:
            qs = qs.filter(branch=branch)

        return qs

    def _filter_qs_company_branch_relaxed(self, qs, company_field: Optional[str] = None, branch_field: Optional[str] = None):
        """
        Как _filter_qs_company_branch, но если активного филиала нет —
        не фильтруем по branch (показываем всю компанию).
        """
        return self._filter_qs_company_branch(qs, company_field=company_field, branch_field=branch_field)

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

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            products_count=Count("products", distinct=True),
            products_qty_total=Coalesce(
                Sum("products__quantity", output_field=DecimalField(max_digits=18, decimal_places=3)),
                V(Decimal("0.000"), output_field=DecimalField(max_digits=18, decimal_places=3)),
            ),
        )


class WarehouseDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = m.Warehouse.objects.select_related("company", "branch").all()
    lookup_field = "id"
    lookup_url_kwarg = "warehouse_uuid"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            products_count=Count("products", distinct=True),
            products_qty_total=Coalesce(
                Sum("products__quantity", output_field=DecimalField(max_digits=18, decimal_places=3)),
                V(Decimal("0.000"), output_field=DecimalField(max_digits=18, decimal_places=3)),
            ),
        )


# ==== Brand ====
class BrandView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = m.WarehouseProductBrand.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = BrandFilter

    def perform_create(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_warehouse_brand_name_global_per_company" in msg
                or "uq_warehouse_brand_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Бренд с таким названием уже существует."})
            raise


class BrandDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BrandSerializer
    queryset = m.WarehouseProductBrand.objects.select_related("company", "branch").all()
    lookup_field = "id"
    lookup_url_kwarg = "brand_uuid"

    def perform_update(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_warehouse_brand_name_global_per_company" in msg
                or "uq_warehouse_brand_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Бренд с таким названием уже существует."})
            raise


# ==== Category ====
class CategoryView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    queryset = m.WarehouseProductCategory.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = None

    def perform_create(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_warehouse_category_name_global_per_company" in msg
                or "uq_warehouse_category_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Категория с таким названием уже существует."})
            raise


class CategoryDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CategorySerializer
    queryset = m.WarehouseProductCategory.objects.select_related("company", "branch").all()
    lookup_field = "id"
    lookup_url_kwarg = "category_uuid"

    def perform_update(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_warehouse_category_name_global_per_company" in msg
                or "uq_warehouse_category_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Категория с таким названием уже существует."})
            raise


# ==== Product groups (inside warehouse, like 1C) ====
class ProductGroupView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductGroupSerializer
    lookup_url_kwarg = "group_uuid"

    def _get_warehouse(self):
        qs = self._filter_qs_company_branch(m.Warehouse.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("warehouse_uuid"))

    def get_queryset(self):
        wh = self._get_warehouse()
        return (
            m.WarehouseProductGroup.objects
            .filter(warehouse=wh)
            .annotate(products_count=Count("products", distinct=True))
            .select_related("warehouse", "company", "branch", "parent")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["warehouse"] = self._get_warehouse()
        return ctx

    def perform_create(self, serializer):
        wh = self._get_warehouse()
        serializer.save(warehouse=wh, company=wh.company, branch=wh.branch)


class ProductGroupDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseProductGroupSerializer
    lookup_field = "id"
    lookup_url_kwarg = "group_uuid"

    def _get_warehouse(self):
        qs = self._filter_qs_company_branch(m.Warehouse.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("warehouse_uuid"))

    def get_queryset(self):
        wh = self._get_warehouse()
        return (
            m.WarehouseProductGroup.objects
            .filter(warehouse=wh)
            .annotate(products_count=Count("products", distinct=True))
            .select_related("warehouse", "company", "branch", "parent")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["warehouse"] = self._get_warehouse()
        return ctx

    def perform_update(self, serializer):
        serializer.validated_data.pop("warehouse", None)
        serializer.save()


# ==== Products ====
class ProductView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductSerializer
    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend]

    def _get_warehouse(self):
        qs = self._filter_qs_company_branch(m.Warehouse.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("warehouse_uuid"))

    def get_queryset(self):
        wh = self._get_warehouse()
        return (
            m.WarehouseProduct.objects
            .select_related("brand", "category", "product_group", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
            .filter(warehouse=wh)
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["warehouse"] = self._get_warehouse()
        return ctx

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
        qs = (
            m.WarehouseProduct.objects
            .select_related("brand", "category", "product_group", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
        )
        return self._filter_qs_company_branch(qs)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        obj = self.get_object()
        if obj and getattr(obj, "warehouse", None):
            ctx["warehouse"] = obj.warehouse
        return ctx

    def perform_update(self, serializer):
        serializer.validated_data.pop("warehouse", None)
        serializer.validated_data.pop("company", None)
        serializer.validated_data.pop("branch", None)
        serializer.save()


class ProductScanView(CompanyBranchRestrictedMixin, APIView):
    def _get_warehouse(self):
        qs = self._filter_qs_company_branch(m.Warehouse.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("warehouse_uuid"))

    def _base_products_qs(self):
        return (
            m.WarehouseProduct.objects
            .select_related("brand", "category", "warehouse", "company", "branch", "characteristics")
            .prefetch_related("images", "packages")
        )

    def post(self, request, *args, **kwargs):
        barcode = (request.data.get("barcode") or "").strip()
        if not barcode:
            raise ValidationError({"barcode": "Обязательное поле."})

        warehouse = self._get_warehouse()
        qs = self._base_products_qs().filter(warehouse=warehouse)

        scan_qty = None
        product = qs.filter(barcode=barcode).first()
        if not product:
            scale_data = _parse_scale_barcode(barcode)
            if scale_data:
                scan_qty = m.q_qty(Decimal(scale_data["weight_kg"]))
                product = qs.filter(plu=scale_data["plu"]).first()

        if product:
            payload = WarehouseProductSerializer(product, context={"request": request}).data
            return Response(
                {
                    "product": payload,
                    "created": False,
                    "scan_qty": str(scan_qty) if scan_qty is not None else None,
                },
                status=status.HTTP_200_OK,
            )

        data = request.data.copy()
        data["barcode"] = barcode
        ser = WarehouseProductSerializer(data=data, context={"request": request})
        ser.is_valid(raise_exception=True)
        product = ser.save(warehouse=warehouse, company=warehouse.company, branch=warehouse.branch)

        return Response(
            {
                "product": WarehouseProductSerializer(product, context={"request": request}).data,
                "created": True,
                "scan_qty": None,
            },
            status=status.HTTP_201_CREATED,
        )


class ProductImagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductImageSerializer

    def _get_product(self):
        qs = self._filter_qs_company_branch(m.WarehouseProduct.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("product_uuid"))

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

    def _get_product(self):
        qs = self._filter_qs_company_branch(m.WarehouseProduct.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return m.WarehouseProductImage.objects.filter(product=product)


class ProductPackagesView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseProductPackageSerializer

    def _get_product(self):
        qs = self._filter_qs_company_branch(m.WarehouseProduct.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("product_uuid"))

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

    def _get_product(self):
        qs = self._filter_qs_company_branch(m.WarehouseProduct.objects.all())
        return get_object_or_404(qs, id=self.kwargs.get("product_uuid"))

    def get_queryset(self):
        product = self._get_product()
        return m.WarehouseProductPackage.objects.filter(product=product)


# ----------------
# Agent requests / stock
# ----------------


class AgentRequestCartListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = AgentRequestCartSerializer

    def get_queryset(self):
        qs = m.AgentRequestCart.objects.select_related("agent", "warehouse", "approved_by")
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if _is_owner_like(user) and serializer.validated_data.get("agent"):
            agent = serializer.validated_data.get("agent")
        else:
            agent = user
        warehouse = serializer.validated_data.get("warehouse")
        if not warehouse:
            raise ValidationError({"warehouse": "Укажите склад."})

        company_ids = _company_ids_for_warehouse_access(user)
        if company_ids and warehouse.company_id not in company_ids:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании или у вас нет доступа."})

        active_branch = self._auto_branch()
        if active_branch is not None and warehouse.branch_id not in (None, active_branch.id):
            raise ValidationError({"warehouse": "Склад другого филиала."})

        serializer.save(
            agent=agent,
            company=warehouse.company,
            branch=warehouse.branch,
        )


class AgentRequestCartRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentRequestCartSerializer

    def get_queryset(self):
        qs = m.AgentRequestCart.objects.select_related("agent", "warehouse", "approved_by")
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        user = self.request.user
        if not _is_owner_like(user) and instance.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        if instance.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно изменять только черновик.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not _is_owner_like(user) and instance.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        if instance.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно удалять только черновик.")
        instance.delete()


class AgentRequestCartSubmitAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentRequestCart.objects.select_related("agent", "warehouse")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user) and cart.agent_id != user.id:
            return Response({"detail": "Нет доступа."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentRequestCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.submit()
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentRequestCartApproveAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentRequestCart.objects.select_related("agent", "warehouse")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentRequestCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.approve(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentRequestCartRejectAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentRequestCart.objects.select_related("agent", "warehouse")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentRequestCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.reject(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentRequestItemListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = AgentRequestItemSerializer

    def get_queryset(self):
        qs = m.AgentRequestItem.objects.select_related("cart", "cart__agent", "product")
        qs = self._filter_qs_company_branch_relaxed(qs)
        cart_id = self.request.query_params.get("cart")
        if cart_id:
            qs = qs.filter(cart_id=cart_id)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        cart = serializer.validated_data.get("cart")
        if not cart:
            raise ValidationError("Укажите cart.")
        if not _is_owner_like(user) and cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        if cart.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно добавлять позиции только в черновик.")
        serializer.save(
            company=cart.company,
            branch=cart.branch,
        )


class AgentRequestItemDetailAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentRequestItemSerializer

    def get_queryset(self):
        qs = m.AgentRequestItem.objects.select_related("cart", "cart__agent", "product")
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)
        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        user = self.request.user
        if not _is_owner_like(user) and instance.cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        if instance.cart.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно менять позиции только в черновике.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not _is_owner_like(user) and instance.cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        if instance.cart.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно удалять позиции только в черновике.")
        instance.delete()


class AgentMyProductsListAPIView(CompanyBranchRestrictedMixin, APIView):
    def get(self, request, *args, **kwargs):
        user = request.user
        qs = m.AgentStockBalance.objects.select_related("product", "warehouse").filter(agent=user)
        qs = self._filter_qs_company_branch_relaxed(qs)
        data = AgentStockBalanceSerializer(qs, many=True).data
        return Response(data)


class OwnerAgentsProductsListAPIView(CompanyBranchRestrictedMixin, APIView):
    def get(self, request, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        qs = m.AgentStockBalance.objects.select_related("agent", "product", "warehouse")
        qs = self._filter_qs_company_branch_relaxed(qs)
        data = AgentStockBalanceSerializer(qs, many=True).data
        return Response(data)


# ----------------
# Агенты склада: поиск компаний, заявки в компанию, приём/отклонение/отстранение
# ----------------


class CompaniesSearchForAgentsAPIView(APIView):
    """Поиск компаний для отправки заявки стать агентом. Доступно любому аутентифицированному пользователю."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        from apps.users.models import Company
        search = (request.query_params.get("search") or "").strip()[:128]
        qs = Company.objects.all().order_by("name")
        if search:
            qs = qs.filter(name__icontains=search)
        qs = qs[:50]
        data = [{"id": str(c.id), "name": c.name, "slug": getattr(c, "slug", "") or ""} for c in qs]
        return Response(data)


class CompanyWarehouseAgentRequestListCreateAPIView(APIView):
    """Список моих заявок в компании (агент) или заявок в мою компанию (владелец). Создание заявки (агент)."""
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = m.CompanyWarehouseAgent.objects.select_related("company", "user", "decided_by").order_by("-created_at")
        if _is_owner_like(user):
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)
            if company:
                qs = qs.filter(company=company)
            else:
                qs = qs.none()
        else:
            qs = qs.filter(user=user)
        return qs

    def get(self, request, *args, **kwargs):
        status_filter = request.query_params.get("status")
        qs = self.get_queryset()
        if status_filter:
            qs = qs.filter(status=status_filter)
        data = CompanyWarehouseAgentSerializer(qs, many=True).data
        return Response(data)

    def post(self, request, *args, **kwargs):
        if _is_owner_like(request.user):
            return Response({"detail": "Владелец/админ не отправляет заявку в свою компанию."}, status=status.HTTP_400_BAD_REQUEST)
        company_id = request.data.get("company")
        if not company_id:
            raise ValidationError({"company": "Укажите компанию (id)."})
        from apps.users.models import Company
        try:
            company = Company.objects.get(id=company_id)
        except (Company.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"company": "Компания не найдена."})
        note = (request.data.get("note") or "").strip()[:512]
        obj, created = m.CompanyWarehouseAgent.objects.get_or_create(
            company=company,
            user=request.user,
            defaults={"status": m.CompanyWarehouseAgent.Status.PENDING, "note": note},
        )
        if not created:
            if obj.status == m.CompanyWarehouseAgent.Status.PENDING:
                return Response(CompanyWarehouseAgentSerializer(obj).data, status=status.HTTP_200_OK)
            if obj.status == m.CompanyWarehouseAgent.Status.ACTIVE:
                raise ValidationError({"detail": "Вы уже являетесь агентом этой компании."})
            if obj.status == m.CompanyWarehouseAgent.Status.REJECTED:
                raise ValidationError({"detail": "Заявка была отклонена. Повторная заявка не предусмотрена."})
            if obj.status == m.CompanyWarehouseAgent.Status.REMOVED:
                obj.status = m.CompanyWarehouseAgent.Status.PENDING
                obj.note = note
                obj.decided_at = None
                obj.decided_by = None
                obj.save(update_fields=["status", "note", "decided_at", "decided_by", "updated_at"])
        data = CompanyWarehouseAgentSerializer(obj).data
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CompanyWarehouseAgentAcceptAPIView(APIView):
    """Принять заявку агента (только владелец/админ компании)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None, *args, **kwargs):
        if not _is_owner_like(request.user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        company = getattr(request.user, "owned_company", None) or getattr(request.user, "company", None)
        if not company:
            return Response({"detail": "Нет компании."}, status=status.HTTP_403_FORBIDDEN)
        obj = get_object_or_404(
            m.CompanyWarehouseAgent.objects.filter(company=company, status=m.CompanyWarehouseAgent.Status.PENDING),
            pk=pk,
        )
        obj.status = m.CompanyWarehouseAgent.Status.ACTIVE
        obj.decided_at = timezone.now()
        obj.decided_by = request.user
        obj.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
        return Response(CompanyWarehouseAgentSerializer(obj).data)


class CompanyWarehouseAgentRejectAPIView(APIView):
    """Отклонить заявку агента (только владелец/админ компании)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None, *args, **kwargs):
        if not _is_owner_like(request.user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        company = getattr(request.user, "owned_company", None) or getattr(request.user, "company", None)
        if not company:
            return Response({"detail": "Нет компании."}, status=status.HTTP_403_FORBIDDEN)
        obj = get_object_or_404(
            m.CompanyWarehouseAgent.objects.filter(company=company, status=m.CompanyWarehouseAgent.Status.PENDING),
            pk=pk,
        )
        obj.status = m.CompanyWarehouseAgent.Status.REJECTED
        obj.decided_at = timezone.now()
        obj.decided_by = request.user
        obj.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
        return Response(CompanyWarehouseAgentSerializer(obj).data)


class CompanyWarehouseAgentRemoveAPIView(APIView):
    """Отстранить агента от компании (только владелец/админ). После этого агент теряет доступ к складам компании."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None, *args, **kwargs):
        if not _is_owner_like(request.user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        company = getattr(request.user, "owned_company", None) or getattr(request.user, "company", None)
        if not company:
            return Response({"detail": "Нет компании."}, status=status.HTTP_403_FORBIDDEN)
        obj = get_object_or_404(
            m.CompanyWarehouseAgent.objects.filter(company=company, status=m.CompanyWarehouseAgent.Status.ACTIVE),
            pk=pk,
        )
        obj.status = m.CompanyWarehouseAgent.Status.REMOVED
        obj.decided_at = timezone.now()
        obj.decided_by = request.user
        obj.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
        return Response(CompanyWarehouseAgentSerializer(obj).data)
