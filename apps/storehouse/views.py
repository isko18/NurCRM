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
from apps.users.models import Branch  # 🔑 для branch-логики


# ===== helpers для company/branch =====
def _get_company(user):
    """Компания текущего пользователя (owner/company или из user.branch.company)."""
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    # fallback: если у юзера нет company, но есть branch с company
    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)

    return None


def _fixed_branch_from_user(user, company):
    """
    «Жёстко» назначенный филиал (который нельзя менять через ?branch):
      - user.primary_branch() / user.primary_branch
      - user.branch
      - единственный id в user.branch_ids
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) primary_branch: метод или атрибут
    primary = getattr(user, "primary_branch", None)

    # 1a) как метод
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    # 1b) как свойство
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    # 2) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # 3) единственный филиал из branch_ids
    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


# ===== Company + Branch scoped mixin (единая логика, как в других модулях) =====
class CompanyBranchQuerysetMixin:
    """
    Видимость:
      - всегда ограничиваемся компанией пользователя;
      - если у пользователя есть активный филиал → только записи этого филиала;
      - если филиала нет → **все филиалы компании** (никакого branch__isnull).

    Активный филиал:
      1) «жёсткий» филиал пользователя (primary / branch / branch_ids);
      2) ?branch=<uuid> (если филиал принадлежит компании и нет жёсткого филиала);
      3) request.branch (если middleware уже поставил и он от той же компании);
      4) иначе None.

    Создание/обновление:
      - mixin просто гарантирует, что request.branch будет проставлен
        (через _active_branch()), остальное делают сериализаторы/модели.
    """

    _BRANCH_UNSET = object()  # маркер «ещё не вычисляли»

    # --- helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        return _get_company(self._user())

    def _active_branch(self):
        """
        Определяем активный филиал и кешируем:
          1) жёсткий филиал пользователя;
          2) ?branch=<uuid>, если нет жёсткого;
          3) request.branch (middleware / ранее проставлен);
          4) None.
        """
        if getattr(self, "_cached_active_branch", self._BRANCH_UNSET) is not self._BRANCH_UNSET:
            return self._cached_active_branch

        request = self.request
        user = self._user()
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            self._cached_active_branch = None
            return None

        company_id = getattr(company, "id", None)

        # 1) жёсткий филиал из пользователя
        fixed = _fixed_branch_from_user(user, company)
        if fixed is not None:
            setattr(request, "branch", fixed)
            self._cached_active_branch = fixed
            return fixed

        # 2) branch из query-параметра (?branch=<uuid>), если нет жёсткого
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                self._cached_active_branch = br
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/кривой id — игнорируем и продолжаем
                pass

        # 3) request.branch (middleware / ранее проставлен)
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                self._cached_active_branch = b
                return b

        # 4) нет филиала
        setattr(request, "branch", None)
        self._cached_active_branch = None
        return None

    # --- company helper ---
    def _base_company_filter(self, qs):
        company = self._user_company()
        return qs.filter(company=company) if company else qs.none()

    # --- queryset / save hooks ---
    def get_queryset(self):
        # по умолчанию не трогаем, вьюхи реализуют сами
        return super().get_queryset()

    def perform_create(self, serializer):
        """
        Просто гарантируем, что _active_branch() отработал
        и положил request.branch для сериализаторов.
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
        # если филиала нет → все склады компании
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
        # остатки по складам компании и активного филиала (или всем филиалам)
        qs = Stock.objects.select_related("warehouse", "product")
        company = self._user_company()
        if not company:
            return qs.none()
        qs = qs.filter(warehouse__company=company)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(warehouse__branch=active_branch)
        # если филиал не задан — все склады компании
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
        return qs
