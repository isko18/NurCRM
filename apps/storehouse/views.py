from rest_framework import generics, permissions
from rest_framework import filters as drf_filters
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockOut, StockTransfer
)
from .serializers import (
    WarehouseSerializer, SupplierSerializer, ProductSerializer, StockSerializer,
    StockInSerializer, StockOutSerializer, StockTransferSerializer
)


# ===== Company + Branch scoped mixin (как в «барбере») =====
class CompanyBranchQuerysetMixin:
    """
    Видимость:
      - если у пользователя есть активный филиал → только записи этого филиала (branch=<user_branch>)
      - иначе → только глобальные записи (branch is NULL)
    Всегда фильтруем по company пользователя.
    Создание/обновление: принудительно проставляем company/branch из контекста.
    """
    _cached_active_branch = object()  # маркер «ещё не вычисляли»

    # --- helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _user_primary_branch(self):
        """
        Определяем филиал сотрудника:
          1) membership с is_primary=True
          2) любой membership
          3) иначе None
        """
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        memberships = getattr(user, "branch_memberships", None)
        if memberships is None:
            return None
        primary = memberships.filter(is_primary=True).select_related("branch").first()
        if primary and primary.branch:
            return primary.branch
        any_member = memberships.select_related("branch").first()
        return any_member.branch if any_member and any_member.branch else None

    def _active_branch(self):
        if self._cached_active_branch is not object():
            return self._cached_active_branch

        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            self._cached_active_branch = None
            return None

        user_branch = self._user_primary_branch()
        if user_branch and user_branch.company_id == company.id:
            setattr(request, "branch", user_branch)
            self._cached_active_branch = user_branch
            return user_branch

        setattr(request, "branch", None)
        self._cached_active_branch = None
        return None

    def _model_has_field(self, model, field_name: str) -> bool:
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    # --- queryset / save hooks ---
    def _base_company_filter(self, qs):
        company = self._user_company()
        return qs.filter(company=company) if company else qs.none()

    def get_queryset(self):
        """
        Реализация ниже в конкретных вьюхах, потому что для Stock
        ветка берётся с warehouse.branch, а не из самой модели.
        """
        return super().get_queryset()

    def perform_create(self, serializer):
        """
        Сериализаторы уже проставляют company/branch (как в барбере),
        но мы всё равно кладём активную ветку в request.branch,
        чтобы mixin сериализатора увидел её.
        """
        self._active_branch()
        serializer.save()

    def perform_update(self, serializer):
        self._active_branch()
        serializer.save()


# 📦 Склады
class WarehouseListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["name"]
    search_fields = ["name", "address"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        qs = Warehouse.objects.all()
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class WarehouseDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Warehouse.objects.all()
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


# 🚚 Поставщики
class SupplierListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["name"]
    search_fields = ["name", "phone", "email", "address", "contact_name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        qs = Supplier.objects.all()
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class SupplierDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Supplier.objects.all()
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


# 🛒 Товары
class ProductListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["brand", "category", "is_active"]
    search_fields = ["name", "barcode"]
    ordering_fields = ["name", "created_at", "updated_at", "selling_price"]
    ordering = ["name"]

    def get_queryset(self):
        qs = Product.objects.select_related("brand", "category")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class ProductDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Product.objects.select_related("brand", "category")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


# 📊 Остатки
class StockListAPIView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    serializer_class = StockSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    filterset_fields = ["warehouse", "product"]
    ordering_fields = ["quantity"]
    ordering = ["-quantity"]

    def get_queryset(self):
        # остатки по складам компании и активного филиала (или глобальным)
        qs = Stock.objects.select_related("warehouse", "product")
        company = self._user_company()
        if not company:
            return qs.none()
        qs = qs.filter(warehouse__company=company)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(warehouse__branch=active_branch)
        else:
            qs = qs.filter(warehouse__branch__isnull=True)
        return qs


class StockDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveAPIView):
    serializer_class = StockSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Stock.objects.select_related("warehouse", "product")
        company = self._user_company()
        if not company:
            return qs.none()
        qs = qs.filter(warehouse__company=company)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(warehouse__branch=active_branch)
        else:
            qs = qs.filter(warehouse__branch__isnull=True)
        return qs


# 📥 Приход
class StockInListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = StockInSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["supplier", "warehouse", "date", "document_number"]
    search_fields = ["document_number", "supplier__name", "warehouse__name"]
    ordering_fields = ["date", "created_at", "document_number"]
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = StockIn.objects.select_related("supplier", "warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class StockInDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockInSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = StockIn.objects.select_related("supplier", "warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


# 📤 Расход
class StockOutListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = StockOutSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["warehouse", "type", "date", "document_number"]
    search_fields = ["document_number", "warehouse__name", "recipient", "destination_address"]
    ordering_fields = ["date", "created_at", "document_number"]
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = StockOut.objects.select_related("warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class StockOutDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockOutSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = StockOut.objects.select_related("warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


# 🔄 Перемещения
class StockTransferListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ["source_warehouse", "destination_warehouse", "date", "document_number"]
    search_fields = ["document_number", "source_warehouse__name", "destination_warehouse__name"]
    ordering_fields = ["date", "created_at", "document_number"]
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = StockTransfer.objects.select_related("source_warehouse", "destination_warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs


class StockTransferDetailAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = StockTransfer.objects.select_related("source_warehouse", "destination_warehouse")
        qs = self._base_company_filter(qs)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return qs
