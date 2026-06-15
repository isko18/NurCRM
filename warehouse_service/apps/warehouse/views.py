from typing import Optional

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Subquery, Sum, DecimalField, Value as V, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.pagination import PageNumberPagination
from uuid import UUID

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
    AgentRequestCartCreateSaleSerializer,
    AgentStockBalanceSerializer,
    CommonWarehouseBalanceSerializer,
    CompanyWarehouseAgentSerializer,
    CompanyWarehouseAgentCommonAccessUpdateSerializer,
    AgentReturnCartSerializer,
    AgentReturnItemSerializer,
    AgentReturnCartActionSerializer,
)

from apps.warehouse import models as m
from apps.warehouse import services, serializers_documents
from apps.warehouse.filters import (
    WarehouseFilter,
    BrandFilter,
    ProductFilter,
)
from apps.common.utils import _is_owner_like


def _cleanup_empty_agent_request_draft(cart_id):
    """Удаляет пустой черновик заявки, если добавление позиции не удалось."""
    if not cart_id:
        return
    try:
        cart = m.AgentRequestCart.objects.get(pk=cart_id, status=m.AgentRequestCart.Status.DRAFT)
    except (m.AgentRequestCart.DoesNotExist, ValueError, TypeError):
        return
    if not cart.items.exists():
        cart.delete()


def _cleanup_empty_agent_return_draft(cart_id):
    """Удаляет пустой черновик возврата, если добавление позиции не удалось."""
    if not cart_id:
        return
    try:
        cart = m.AgentReturnCart.objects.get(pk=cart_id, status=m.AgentReturnCart.Status.DRAFT)
    except (m.AgentReturnCart.DoesNotExist, ValueError, TypeError):
        return
    if not cart.items.exists():
        cart.delete()


def _owner_agent_stock_queryset(view, *, agent_id=None):
    move_subq = m.AgentStockMove.objects.filter(
        agent=OuterRef("agent"),
        warehouse=OuterRef("warehouse"),
        product=OuterRef("product"),
    ).order_by("-created_at").values("created_at")[:1]
    qs = (
        m.AgentStockBalance.objects
        .select_related("agent", "product", "product__product_group", "product__category", "warehouse")
        .annotate(last_movement_at=Subquery(move_subq))
    )
    qs = view._filter_qs_company_branch_relaxed(qs)
    if agent_id:
        qs = qs.filter(agent_id=agent_id)
    warehouse_id = (view.request.query_params.get("warehouse") or "").strip()
    if warehouse_id:
        try:
            qs = qs.filter(warehouse_id=UUID(warehouse_id))
        except Exception:
            raise ValidationError({"warehouse": "Неверный UUID."})
    search = (view.request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(product__name__icontains=search)
            | Q(product__article__icontains=search)
            | Q(product__barcode__icontains=search)
        )
    product_group_raw = (view.request.query_params.get("product_group") or "").strip()
    if product_group_raw:
        try:
            qs = qs.filter(product__product_group_id=UUID(product_group_raw))
        except Exception:
            raise ValidationError({"product_group": "Неверный UUID."})
    order_by = (view.request.query_params.get("order_by") or "").strip().lower()
    if order_by == "date":
        qs = qs.order_by("last_movement_at", "product__name", "id")
    elif order_by == "-date":
        qs = qs.order_by("-last_movement_at", "product__name", "id")
    else:
        qs = qs.order_by("-last_movement_at", "product__name", "id")
    return qs


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


def _agent_membership_for_company(user, company):
    if not user or not getattr(user, "is_authenticated", False) or company is None:
        return None
    return (
        m.CompanyWarehouseAgent.objects
        .filter(
            user=user,
            company=company,
            status=m.CompanyWarehouseAgent.Status.ACTIVE,
        )
        .select_related("assigned_warehouse")
        .first()
    )


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

    def _agent_membership(self, company=None):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        if _is_owner_like(user) or getattr(user, "company_id", None):
            return None
        company = company or self._company()
        if company is None:
            return None
        return _agent_membership_for_company(user, company)

    def _assigned_agent_warehouse_id(self, company=None):
        membership = self._agent_membership(company=company)
        if membership is None:
            return None
        return getattr(membership, "assigned_warehouse_id", None)

    def _ensure_agent_can_access_warehouse(self, warehouse, *, field_name="warehouse"):
        user = self._user()
        if warehouse is None or not user or _is_owner_like(user) or getattr(user, "company_id", None):
            return
        membership = _agent_membership_for_company(user, getattr(warehouse, "company", None))
        if membership is None:
            raise ValidationError({field_name: "Склад принадлежит другой компании или у вас нет доступа."})
        assigned_warehouse_id = getattr(membership, "assigned_warehouse_id", None)
        if assigned_warehouse_id and assigned_warehouse_id != getattr(warehouse, "id", None):
            raise ValidationError({field_name: "Вам назначен доступ только к другому складу."})

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

        assigned_warehouse_id = self._assigned_agent_warehouse_id(company=company)
        if assigned_warehouse_id:
            if self._model_has_field(model, "warehouse"):
                qs = qs.filter(warehouse_id=assigned_warehouse_id)
            elif model is m.Warehouse:
                qs = qs.filter(id=assigned_warehouse_id)

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


def filter_qs_company_branch_or_global(view, qs):
    """
    Как CompanyBranchRestrictedMixin по компании, но по филиалу:
    при выбранном филиале включаем и записи с branch=NULL (общие на компанию).
    Иначе денежные документы и контрагенты без филиала не попадают в queryset, и query-параметры
    кажутся «не работающими».
    """
    company = view._company()
    user = view._user()
    company_ids = _company_ids_for_warehouse_access(user) if user else []
    if company is None and not company_ids:
        return qs.none()
    if company is not None:
        company_ids = [company.id]
    elif not company_ids:
        return qs.none()
    qs = qs.filter(company_id__in=company_ids)
    branch = view._auto_branch()
    if branch is not None and CompanyBranchRestrictedMixin._model_has_field(qs.model, "branch"):
        qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    assigned_warehouse_id = view._assigned_agent_warehouse_id(company=company)
    if assigned_warehouse_id and CompanyBranchRestrictedMixin._model_has_field(qs.model, "warehouse"):
        qs = qs.filter(warehouse_id=assigned_warehouse_id)
    return qs


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
            .prefetch_related("images", "packages", "alternate_barcodes")
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
            .prefetch_related("images", "packages", "alternate_barcodes")
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
            .prefetch_related("images", "packages", "alternate_barcodes")
        )

    def post(self, request, *args, **kwargs):
        barcode = (request.data.get("barcode") or "").strip()
        if not barcode:
            raise ValidationError({"barcode": "Обязательное поле."})

        warehouse = self._get_warehouse()
        qs = self._base_products_qs().filter(warehouse=warehouse)

        scan_qty = None
        product = qs.filter(Q(barcode=barcode) | Q(alternate_barcodes__barcode=barcode)).distinct().first()
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
    filter_backends = [DjangoFilterBackend]
    # Фильтры для фронта: ?status=approved, ?warehouse=<uuid> и т.д.
    filterset_fields = ["status", "warehouse", "agent", "sale_document", "submitted_at", "approved_at"]

    def get_queryset(self):
        qs = (
            m.AgentRequestCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
        )
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        warehouse = serializer.validated_data.get("warehouse")
        if not warehouse:
            raise ValidationError({"warehouse": "Укажите склад."})

        if _is_owner_like(user):
            agent = serializer.validated_data.get("agent")
            if not agent:
                raise ValidationError({"agent": "Укажите агента, которому отправляете товар."})
            try:
                m.CompanyWarehouseAgent.ensure_active_for_warehouse(agent, warehouse)
            except DjangoValidationError as exc:
                raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        else:
            agent = user

        company_ids = _company_ids_for_warehouse_access(user)
        if company_ids and warehouse.company_id not in company_ids:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании или у вас нет доступа."})
        self._ensure_agent_can_access_warehouse(warehouse, field_name="warehouse")

        active_branch = self._auto_branch()
        if active_branch is not None and warehouse.branch_id not in (None, active_branch.id):
            raise ValidationError({"warehouse": "Склад другого филиала."})

        serializer.save(
            agent=agent,
            company=warehouse.company,
            branch=warehouse.branch,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items_data = list(serializer.validated_data.pop("items_input", []) or [])
        try:
            with transaction.atomic():
                self.perform_create(serializer)
                cart = serializer.instance
                for row in items_data:
                    item = m.AgentRequestItem(
                        cart=cart,
                        product=row["product"],
                        quantity_requested=row["quantity_requested"],
                        company=cart.company,
                        branch=cart.branch,
                    )
                    item.full_clean()
                    item.save()
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))

        cart = (
            m.AgentRequestCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
            .get(pk=cart.pk)
        )
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        headers = self.get_success_headers(out)
        return Response(out, status=status.HTTP_201_CREATED, headers=headers)


class AgentRequestCartRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentRequestCartSerializer

    def get_queryset(self):
        qs = (
            m.AgentRequestCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
        )
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
            err = getattr(exc, "message_dict", {})
            if "items" in err:
                cart.delete()
            raise ValidationError(err or {"detail": str(exc)})
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


class AgentRequestCartDispatchAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Владелец/админ выдаёт товар агенту без заявки агента.

    POST /api/warehouse/agent-carts/<id>/dispatch/
    """

    def post(self, request, pk=None, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        qs = self._filter_qs_company_branch_relaxed(
            m.AgentRequestCart.objects.select_related("agent", "warehouse").prefetch_related("items__product")
        )
        cart = get_object_or_404(qs, pk=pk)
        ser = AgentRequestCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.dispatch_by_owner(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentRequestCartCreateSaleAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Создать документ SALE по позициям заявки агента и привязать продажу к агенту.

    POST /api/warehouse/agent-carts/<id>/create-sale/
    body: { counterparty, post?, payment_kind?, prepayment_amount?, discount_percent?, discount_amount?, comment? }
    """

    def post(self, request, pk=None, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        qs = self._filter_qs_company_branch_relaxed(
            m.AgentRequestCart.objects.select_related("agent", "warehouse").prefetch_related("items__product")
        )
        cart = get_object_or_404(qs, pk=pk)

        if cart.status != m.AgentRequestCart.Status.APPROVED:
            raise ValidationError({"status": "Создать продажу можно только по одобренной заявке (approved)."})
        if cart.sale_document_id:
            raise ValidationError({"sale_document": "По этой заявке уже создан документ продажи."})

        ser = AgentRequestCartCreateSaleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        counterparty = ser.validated_data["counterparty"]

        if counterparty.agent_id != cart.agent_id:
            raise ValidationError({"counterparty": "Контрагент не принадлежит этому агенту."})

        if not cart.items.exists():
            raise ValidationError({"items": "Нельзя создать продажу по пустой заявке."})

        should_post = bool(ser.validated_data.get("post") or False)
        is_sale_request = bool(ser.validated_data.get("is_sale_request") or False)
        is_wholesale = bool(ser.validated_data.get("is_wholesale") or False)

        # Оптовая продажа доступна, только если владелец выдал агенту флаг can_sell_wholesale.
        if is_wholesale and not services.agent_can_sell_wholesale(user=cart.agent, company=cart.company):
            raise ValidationError(
                {"is_wholesale": "У агента нет доступа к оптовым продажам. Выдайте агенту флаг can_sell_wholesale."}
            )

        with transaction.atomic():
            use_common_stock = services.agent_has_common_access_to_warehouse(
                user=cart.agent,
                warehouse=cart.warehouse,
                company=cart.company,
            )
            doc = m.Document.objects.create(
                doc_type=m.Document.DocType.SALE,
                status=(m.Document.Status.SALE_REQUEST if is_sale_request else m.Document.Status.DRAFT),
                warehouse_from=cart.warehouse,
                counterparty=counterparty,
                agent=cart.agent,
                use_common_stock=use_common_stock,
                is_sale_request=is_sale_request,
                is_wholesale=is_wholesale,
                payment_kind=ser.validated_data.get("payment_kind") or m.Document.PaymentKind.CASH,
                prepayment_amount=ser.validated_data.get("prepayment_amount") or Decimal("0.00"),
                discount_percent=ser.validated_data.get("discount_percent") or Decimal("0.00"),
                discount_amount=ser.validated_data.get("discount_amount") or Decimal("0.00"),
                comment=(ser.validated_data.get("comment") or "").strip(),
            )

            for it in cart.items.select_related("product").all():
                product = it.product
                retail = Decimal(getattr(product, "price", None) or 0)
                wholesale = Decimal(getattr(product, "wholesale_price", None) or 0)
                chosen = wholesale if (is_wholesale and wholesale > 0) else retail
                price = chosen.quantize(Decimal("0.01"))
                line_dp = Decimal(getattr(product, "discount_percent", None) or 0).quantize(Decimal("0.01"))
                item = m.DocumentItem(
                    document=doc,
                    product=product,
                    qty=it.quantity_requested,
                    price=price,
                    discount_percent=line_dp,
                    discount_amount=Decimal("0.00"),
                )
                try:
                    item.clean()
                except DjangoValidationError as exc:
                    raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
                item.save()

            services.recalc_document_totals(doc)

            cart.sale_document = doc
            cart.save(update_fields=["sale_document"])

            if should_post:
                try:
                    services.post_document(doc)
                except ValueError as exc:
                    raise ValidationError({"detail": str(exc)})
                doc.refresh_from_db()

        out = serializers_documents.DocumentSerializer(doc, context={"request": request}).data
        return Response(out, status=status.HTTP_201_CREATED)


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
            assigned_warehouse_id = self._assigned_agent_warehouse_id()
            if assigned_warehouse_id:
                qs = qs.filter(cart__warehouse_id=assigned_warehouse_id)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        cart = serializer.validated_data.get("cart")
        if not cart:
            raise ValidationError("Укажите cart.")
        if not _is_owner_like(user) and cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к заявке.")
        self._ensure_agent_can_access_warehouse(getattr(cart, "warehouse", None), field_name="cart")
        if cart.status != m.AgentRequestCart.Status.DRAFT:
            raise ValidationError("Можно добавлять позиции только в черновик.")
        try:
            serializer.save(
                company=cart.company,
                branch=cart.branch,
            )
        except DjangoValidationError as exc:
            _cleanup_empty_agent_request_draft(getattr(cart, "pk", None))
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))

    def create(self, request, *args, **kwargs):
        cart_id = request.data.get("cart")
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError:
            _cleanup_empty_agent_request_draft(cart_id)
            raise


class AgentRequestItemDetailAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentRequestItemSerializer

    def get_queryset(self):
        qs = m.AgentRequestItem.objects.select_related("cart", "cart__agent", "product")
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)
            assigned_warehouse_id = self._assigned_agent_warehouse_id()
            if assigned_warehouse_id:
                qs = qs.filter(cart__warehouse_id=assigned_warehouse_id)
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


class AgentReturnCartListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = AgentReturnCartSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "warehouse", "agent", "submitted_at", "approved_at"]

    def get_queryset(self):
        qs = (
            m.AgentReturnCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
        )
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        warehouse = serializer.validated_data.get("warehouse")
        if not warehouse:
            raise ValidationError({"warehouse": "Укажите склад."})

        if _is_owner_like(user):
            agent = serializer.validated_data.get("agent")
            if not agent:
                raise ValidationError({"agent": "Укажите агента, у которого принимаете возврат."})
            try:
                m.CompanyWarehouseAgent.ensure_active_for_warehouse(agent, warehouse)
            except DjangoValidationError as exc:
                raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        else:
            agent = user

        company_ids = _company_ids_for_warehouse_access(user)
        if company_ids and warehouse.company_id not in company_ids:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании или у вас нет доступа."})
        self._ensure_agent_can_access_warehouse(warehouse, field_name="warehouse")

        active_branch = self._auto_branch()
        if active_branch is not None and warehouse.branch_id not in (None, active_branch.id):
            raise ValidationError({"warehouse": "Склад другого филиала."})

        serializer.save(
            agent=agent,
            company=warehouse.company,
            branch=warehouse.branch,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items_data = list(serializer.validated_data.pop("items_input", []) or [])
        try:
            with transaction.atomic():
                self.perform_create(serializer)
                cart = serializer.instance
                for row in items_data:
                    item = m.AgentReturnItem(
                        cart=cart,
                        product=row["product"],
                        quantity_returned=row["quantity_returned"],
                        company=cart.company,
                        branch=cart.branch,
                    )
                    item.full_clean()
                    item.save()
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))

        cart = (
            m.AgentReturnCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
            .get(pk=cart.pk)
        )
        out = AgentReturnCartSerializer(cart, context={"request": request}).data
        headers = self.get_success_headers(out)
        return Response(out, status=status.HTTP_201_CREATED, headers=headers)


class AgentReturnCartRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentReturnCartSerializer

    def get_queryset(self):
        qs = (
            m.AgentReturnCart.objects
            .select_related("agent", "warehouse", "approved_by")
            .prefetch_related("items__product")
        )
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        user = self.request.user
        if not _is_owner_like(user) and instance.agent_id != user.id:
            raise PermissionDenied("Нет доступа к возврату.")
        if instance.status != m.AgentReturnCart.Status.DRAFT:
            raise ValidationError("Можно изменять только черновик.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not _is_owner_like(user) and instance.agent_id != user.id:
            raise PermissionDenied("Нет доступа к возврату.")
        if instance.status != m.AgentReturnCart.Status.DRAFT:
            raise ValidationError("Можно удалять только черновик.")
        instance.delete()


class AgentReturnCartSubmitAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentReturnCart.objects.select_related("agent", "warehouse")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user) and cart.agent_id != user.id:
            return Response({"detail": "Нет доступа."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentReturnCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.submit()
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentReturnCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentReturnCartApproveAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentReturnCart.objects.select_related("agent", "warehouse").prefetch_related("items__product")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentReturnCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.approve(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentReturnCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentReturnCartRejectAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        qs = self._filter_qs_company_branch_relaxed(
            m.AgentReturnCart.objects.select_related("agent", "warehouse")
        )
        cart = get_object_or_404(qs, pk=pk)
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        ser = AgentReturnCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.reject(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentReturnCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentReturnCartReceiveAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Владелец/админ принимает возврат от агента без заявки агента.

    POST /api/warehouse/agent-return-carts/<id>/receive/
    """

    def post(self, request, pk=None, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        qs = self._filter_qs_company_branch_relaxed(
            m.AgentReturnCart.objects.select_related("agent", "warehouse").prefetch_related("items__product")
        )
        cart = get_object_or_404(qs, pk=pk)
        ser = AgentReturnCartActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            cart.receive_by_owner(user)
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))
        out = AgentReturnCartSerializer(cart, context={"request": request}).data
        return Response(out)


class AgentReturnItemListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = AgentReturnItemSerializer

    def get_queryset(self):
        qs = m.AgentReturnItem.objects.select_related("cart", "cart__agent", "product")
        qs = self._filter_qs_company_branch_relaxed(qs)
        cart_id = self.request.query_params.get("cart")
        if cart_id:
            qs = qs.filter(cart_id=cart_id)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)
            assigned_warehouse_id = self._assigned_agent_warehouse_id()
            if assigned_warehouse_id:
                qs = qs.filter(cart__warehouse_id=assigned_warehouse_id)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        cart = serializer.validated_data.get("cart")
        if not cart:
            raise ValidationError("Укажите cart.")
        if not _is_owner_like(user) and cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к возврату.")
        self._ensure_agent_can_access_warehouse(getattr(cart, "warehouse", None), field_name="cart")
        if cart.status != m.AgentReturnCart.Status.DRAFT:
            raise ValidationError("Можно добавлять позиции только в черновик.")
        try:
            serializer.save(
                company=cart.company,
                branch=cart.branch,
            )
        except DjangoValidationError as exc:
            _cleanup_empty_agent_return_draft(getattr(cart, "pk", None))
            raise ValidationError(getattr(exc, "message_dict", {"detail": str(exc)}))

    def create(self, request, *args, **kwargs):
        cart_id = request.data.get("cart")
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError:
            _cleanup_empty_agent_return_draft(cart_id)
            raise


class AgentReturnItemDetailAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentReturnItemSerializer

    def get_queryset(self):
        qs = m.AgentReturnItem.objects.select_related("cart", "cart__agent", "product")
        qs = self._filter_qs_company_branch_relaxed(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)
            assigned_warehouse_id = self._assigned_agent_warehouse_id()
            if assigned_warehouse_id:
                qs = qs.filter(cart__warehouse_id=assigned_warehouse_id)
        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        user = self.request.user
        if not _is_owner_like(user) and instance.cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к возврату.")
        if instance.cart.status != m.AgentReturnCart.Status.DRAFT:
            raise ValidationError("Можно менять позиции только в черновике.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not _is_owner_like(user) and instance.cart.agent_id != user.id:
            raise PermissionDenied("Нет доступа к возврату.")
        if instance.cart.status != m.AgentReturnCart.Status.DRAFT:
            raise ValidationError("Можно удалять позиции только в черновике.")
        instance.delete()


class AgentMyProductsListAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Остатки агента:
    - при включённом общем доступе (common_access_enabled=true) — остатки общего склада (WarehouseProduct.quantity)
    - иначе — его персональные остатки (AgentStockBalance)
    Поддерживает:
      - ?search=<text> по названию/артикулу/штрихкоду товара
      - пагинацию PageNumberPagination (?page=, ?page_size=)
      - order_by=date|-date (как раньше)
    """

    class _Paginator(PageNumberPagination):
        page_size_query_param = "page_size"

    pagination_class = _Paginator

    def _paginate_and_respond(self, rows, serializer_class):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, self.request, view=self)
        ser = serializer_class(page, many=True)
        return paginator.get_paginated_response(ser.data)

    def get(self, request, *args, **kwargs):
        user = request.user
        company = self._company()
        product_group_raw = (request.query_params.get("product_group") or "").strip()
        product_group_id = None
        if product_group_raw:
            try:
                product_group_id = UUID(product_group_raw)
            except Exception:
                raise ValidationError({"product_group": "Неверный UUID."})
        # Пытаемся найти настройку общего доступа к складу для агента.
        # Не ограничиваемся только "текущей" компанией, чтобы работать
        # даже если у пользователя несколько компаний/ролей.
        membership_qs = m.CompanyWarehouseAgent.objects.filter(
            user=user,
            status=m.CompanyWarehouseAgent.Status.ACTIVE,
            common_access_enabled=True,
            common_warehouse__isnull=False,
        )
        # Если _company() определена, по возможности предпочитаем её.
        if company is not None:
            membership_qs = membership_qs.filter(company=company)

        membership = membership_qs.select_related("common_warehouse").first()
        if membership and membership.common_warehouse_id:
            wh = membership.common_warehouse
            prod_qs = (
                m.WarehouseProduct.objects
                .filter(warehouse=wh)
                .select_related("product_group", "category")
                .only(
                    "id",
                    "name",
                    "article",
                    "unit",
                    "price",
                    "discount_percent",
                    "quantity",
                    "warehouse_id",
                    "created_date",
                    "updated_date",
                    "product_group_id",
                    "category_id",
                )
            )
            if product_group_id:
                prod_qs = prod_qs.filter(product_group_id=product_group_id)
            # Поиск по товарам общего склада
            search = (request.query_params.get("search") or "").strip()
            if search:
                prod_qs = prod_qs.filter(
                    Q(name__icontains=search)
                    | Q(article__icontains=search)
                    | Q(barcode__icontains=search)
                )
            order_by = (request.query_params.get("order_by") or "").strip().lower()
            if order_by == "date":
                prod_qs = prod_qs.order_by("created_date", "id")
            elif order_by == "-date":
                prod_qs = prod_qs.order_by("-created_date", "-id")
            else:
                # по умолчанию — по дате создания (сначала новые)
                prod_qs = prod_qs.order_by("-created_date", "id")
            prod_qs = self._filter_qs_company_branch_relaxed(prod_qs)
            rows = [
                CommonWarehouseBalanceSerializer.make_row(
                    agent_id=user.id,
                    warehouse_id=wh.id,
                    product=p,
                )
                for p in prod_qs
            ]
            return self._paginate_and_respond(rows, CommonWarehouseBalanceSerializer)

        move_subq = m.AgentStockMove.objects.filter(
            agent=OuterRef("agent"),
            warehouse=OuterRef("warehouse"),
            product=OuterRef("product"),
        ).order_by("-created_at").values("created_at")[:1]
        qs = (
            m.AgentStockBalance.objects
            .filter(agent=user)
            .select_related("product", "product__product_group", "product__category", "warehouse")
            .annotate(last_movement_at=Subquery(move_subq))
        )
        qs = self._filter_qs_company_branch_relaxed(qs)
        if product_group_id:
            qs = qs.filter(product__product_group_id=product_group_id)
        # Поиск по товарам агента
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__article__icontains=search)
                | Q(product__barcode__icontains=search)
            )
        order_by = (request.query_params.get("order_by") or "").strip().lower()
        if order_by == "date":
            qs = qs.order_by("last_movement_at", "product__name", "id")
        elif order_by == "-date":
            qs = qs.order_by("-last_movement_at", "product__name", "id")
        else:
            # по умолчанию — по дате (последнее движение), сначала новые
            qs = qs.order_by("-last_movement_at", "product__name", "id")
        return self._paginate_and_respond(qs, AgentStockBalanceSerializer)


class OwnerAgentsProductsListAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Остатки на руках у агентов (владелец/админ).

    GET /api/warehouse/owner/agents/products/
    Query: agent, warehouse, search, product_group, order_by, page, page_size
    """

    class _Paginator(PageNumberPagination):
        page_size_query_param = "page_size"

    pagination_class = _Paginator

    def get(self, request, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        agent_raw = (request.query_params.get("agent") or "").strip()
        agent_id = None
        if agent_raw:
            try:
                agent_id = UUID(agent_raw)
            except Exception:
                raise ValidationError({"agent": "Неверный UUID."})

        qs = _owner_agent_stock_queryset(self, agent_id=agent_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        ser = AgentStockBalanceSerializer(page, many=True)
        return paginator.get_paginated_response(ser.data)


class OwnerAgentProductsListAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Остатки конкретного агента (владелец/админ).

    GET /api/warehouse/owner/agents/<agent_id>/products/
    """

    class _Paginator(PageNumberPagination):
        page_size_query_param = "page_size"

    pagination_class = _Paginator

    def get(self, request, agent_id=None, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        qs = _owner_agent_stock_queryset(self, agent_id=agent_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        ser = AgentStockBalanceSerializer(page, many=True)
        return paginator.get_paginated_response(ser.data)


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


class CompanyWarehouseAgentCommonAccessUpdateAPIView(APIView):
    """
    Владелец/админ обновляет складской доступ агента.

    PATCH /api/warehouse/agents/company-requests/{id}/common-access/
    body:
      - assigned_warehouse: uuid|null
      - common_access_enabled: bool
      - common_warehouse: uuid|null (обязателен если common_access_enabled=true)
      - can_sell_wholesale: bool (разрешить агенту оптовые продажи)
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk=None, *args, **kwargs):
        if not _is_owner_like(request.user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)
        company = getattr(request.user, "owned_company", None) or getattr(request.user, "company", None)
        if not company:
            return Response({"detail": "Нет компании."}, status=status.HTTP_403_FORBIDDEN)

        obj = get_object_or_404(
            m.CompanyWarehouseAgent.objects.filter(company=company, status=m.CompanyWarehouseAgent.Status.ACTIVE),
            pk=pk,
        )

        ser = CompanyWarehouseAgentCommonAccessUpdateSerializer(instance=obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(CompanyWarehouseAgentSerializer(obj).data)


class CompanyWarehouseAgentAdminAssignAPIView(APIView):
    """
    Владелец/админ напрямую назначает/включает агента склада без заявки.

    POST /api/warehouse/agents/company-memberships/
    body:
      - user: uuid пользователя
      - assigned_warehouse: uuid|null (опционально)
      - common_access_enabled: bool (опционально)
      - common_warehouse: uuid|null (опционально, обязателен если common_access_enabled=true)
      - can_sell_wholesale: bool (опционально, разрешить агенту оптовые продажи)
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Только владелец/админ."}, status=status.HTTP_403_FORBIDDEN)

        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if not company:
            return Response({"detail": "Нет компании."}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get("user")
        if not user_id:
            raise ValidationError({"user": "Укажите пользователя (id)."})

        UserModel = get_user_model()
        try:
            target_user = UserModel.objects.get(id=user_id)
        except (UserModel.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"user": "Пользователь не найден."})

        obj, created = m.CompanyWarehouseAgent.objects.get_or_create(
            company=company,
            user=target_user,
            defaults={
                "status": m.CompanyWarehouseAgent.Status.ACTIVE,
            },
        )

        # Применяем настройки общего доступа к складу, если они переданы
        if (
            "assigned_warehouse" in request.data
            or "common_access_enabled" in request.data
            or "common_warehouse" in request.data
            or "can_sell_wholesale" in request.data
        ):
            ser = CompanyWarehouseAgentCommonAccessUpdateSerializer(
                instance=obj,
                data=request.data,
                partial=True,
            )
            ser.is_valid(raise_exception=True)
            ser.save()

        # Гарантируем активный статус после назначения
        if obj.status != m.CompanyWarehouseAgent.Status.ACTIVE:
            obj.status = m.CompanyWarehouseAgent.Status.ACTIVE
            obj.decided_at = timezone.now()
            obj.decided_by = user
            obj.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])

        return Response(CompanyWarehouseAgentSerializer(obj).data)
