from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from django.db import transaction, IntegrityError
from django.db.models import Sum, Count, Avg, F, Q, Prefetch, Value as V, Exists, OuterRef, Subquery
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from itertools import groupby
from typing import List, Optional, Dict, Any
from operator import attrgetter
from datetime import datetime, date as _date
from django.db.models.functions import Coalesce
import logging

from rest_framework import generics, permissions, filters, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from .filters import TransactionRecordFilter, DebtFilter, DebtPaymentFilter
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import DecimalField, ExpressionWrapper
from rest_framework.pagination import CursorPagination


from apps.users.models import Branch, User

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event,
    ProductBrand, ProductCategory, Warehouse, WarehouseEvent, Client,
    GlobalProduct, GlobalBrand, GlobalCategory, ClientDeal, Bid, SocialApplications, TransactionRecord,
    ContractorWork, DealInstallment, DebtPayment, Debt, ObjectSaleItem, ObjectSale, ObjectItem, ItemMake,
    ManufactureSubreal, Acceptance, ReturnFromAgent, AgentSaleAllocation, ProductImage,
    AgentRequestCart, AgentRequestItem, ProductPackage, ProductCharacteristics, DealPayment,
    ProductRecipeItem,
    ProductFavorite,
    MarketSaleEmployeePayProfile,
    Sale,
    SaleItem,
    SupplierReceipt,
    SupplierReceiptItem,
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ProductListSerializer, sync_product_promotion_tiers,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer, ClientSerializer, ClientDealSerializer, BidSerializers, SocialApplicationsSerializers,
    TransactionRecordSerializer, ContractorWorkSerializer, DebtSerializer, DebtPaymentSerializer,
    ObjectItemSerializer, ObjectSaleSerializer, ObjectSaleItemSerializer,
    BulkIdsSerializer, ItemMakeSerializer,
    ManufactureSubrealSerializer, AcceptanceCreateSerializer, ReturnCreateSerializer,
    BulkSubrealCreateSerializer, AcceptanceReadSerializer, ReturnApproveSerializer, ReturnRejectSerializer, ReturnReadSerializer,
    AgentProductOnHandSerializer, AgentWithProductsSerializer, GlobalProductReadSerializer,
    ProductImageSerializer,
    AgentRequestCartApproveSerializer, AgentRequestCartRejectSerializer,
    AgentRequestCartSerializer, AgentRequestCartSubmitSerializer, AgentRequestItemSerializer, DealPayInputSerializer, DealRefundInputSerializer,
    MarketSaleEmployeePayProfileSerializer,
    SupplierReceiptCreateSerializer,
    SupplierReceiptReadSerializer,
)
from django.db.models import ProtectedError
from apps.utils import product_images_prefetch, _is_owner_like
from apps.main.analytics_agent import build_agent_analytics_payload, _parse_period, _compute_agent_on_hand
from apps.main.analytics_owner_production import build_owner_analytics_payload, _dt_range
from apps.main.services import _parse_bool_like, _parse_date_to_aware_datetime, _parse_kind, _parse_int_nonneg, _parse_decimal
    


# ===========================
#  Company + Branch mixin (как в barber)
# ===========================
_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")


def _to_dec(v, default=Decimal("0")):
    if v in (None, "", "null"):
        return default
    return Decimal(str(v))


def _calc_price(purchase_price: Decimal, markup_percent: Decimal) -> Decimal:
    price = purchase_price * (Decimal("1") + markup_percent / Decimal("100"))
    return price.quantize(_Q2, rounding=ROUND_HALF_UP)


def _calc_markup(purchase_price: Decimal, price: Decimal) -> Decimal:
    if purchase_price <= 0:
        return Decimal("0.00")
    mp = (price / purchase_price - Decimal("1")) * Decimal("100")
    # ВАЖНО: наценка хранится точнее (4 знака), иначе при обратном пересчёте цены
    # (purchase_price + markup_percent) будут появляться «копейки» из-за округления процента.
    return mp.quantize(_Q4, rounding=ROUND_HALF_UP)

class AgentCartLockMixin:
    """
    Безопасная блокировка корзины:
    1) сначала выбираем корзину с фильтрами компании/филиала/прав
    2) потом отдельно лочим её чистым SELECT ... FOR UPDATE без join'ов
    """

    def _lock_cart_for_submit(self, request, pk):
        """
        Агент сабмитит ТОЛЬКО свою корзину.
        Владелец тоже может (разрешаем, вдруг нужно).
        """
        allowed_qs = AgentRequestCart.objects.all()
        allowed_qs = self._filter_qs_company_branch(allowed_qs)

        user = request.user
        if not _is_owner_like(user):
            allowed_qs = allowed_qs.filter(agent=user)

        # шаг 1: находим корзину с фильтрами доступа
        cart = get_object_or_404(allowed_qs, pk=pk)

        # шаг 2: чистый лок без join'ов
        locked_cart = (
            AgentRequestCart.objects
            .select_related(None)        # ВАЖНО: убираем автоджойны
            .select_for_update()
            .get(pk=cart.pk)
        )
        return locked_cart

    def _lock_cart_for_owner_action(self, request, pk):
        """
        approve / reject -> только для владельца/админа.
        """
        user = request.user
        if not _is_owner_like(user):
            raise PermissionDenied("Forbidden")

        allowed_qs = AgentRequestCart.objects.all()
        allowed_qs = self._filter_qs_company_branch(allowed_qs)

        cart = get_object_or_404(allowed_qs, pk=pk)

        locked_cart = (
            AgentRequestCart.objects
            .select_related(None)        # ВАЖНО
            .select_for_update()
            .get(pk=cart.pk)
        )
        return locked_cart


class CompanyBranchRestrictedMixin:
    """
    - Фильтрует queryset по компании и (если у модели есть поле branch) по «активному филиалу».
    - На create/save подставляет company и (если у модели есть поле branch) — текущий филиал.

    Активный филиал:

        1) «жёсткий» филиал сотрудника:
            - user.primary_branch() / user.primary_branch
            - первый филиал из branch_ids
            - первая запись из user.branch_memberships / user.branches (если есть такие связи)
            - request.branch (если мидлварь уже положила)
        2) ?branch=<uuid> в запросе (если филиал принадлежит компании,
           И у пользователя нет жёстко назначенного филиала)
        3) None (нет филиала — работаем по всей компании, но только с записями без branch)

    Логика выборки:
        - если branch определён → показываем только данные этого филиала;
        - если branch = None → показываем только данные без филиала (branch IS NULL).
    """

    permission_classes = [permissions.IsAuthenticated]

    # ----- helpers -----
    def _request(self):
        return getattr(self, "request", None)

    def _user(self):
        req = self._request()
        return getattr(req, "user", None) if req else None

    def _company(self):
        """
        Компания текущего пользователя.
        Для суперюзера -> None (без ограничения по company).

        Если у юзера нет company, но есть филиал с company — берём её.
        """
        u = self._user()
        if not u or not getattr(u, "is_authenticated", False):
            return None
        if getattr(u, "is_superuser", False):
            return None

        company = getattr(u, "owned_company", None) or getattr(u, "company", None)
        if company:
            return company

        # fallback: пробуем достать компанию из его филиала (если есть связь)
        br = getattr(u, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _fixed_branch_from_user(self, company) -> Optional[Branch]:
        """
        «Жёстко» назначенный филиал сотрудника (который нельзя менять через ?branch):

         - user.primary_branch() или user.primary_branch
         - user.branch (если есть такое поле)
         - первый филиал из branch_ids (как в /me)
         - первая связь из user.branches / user.branch_memberships (если такие есть)
         - request.branch (если мидлварь уже положила)
        """
        req = self._request()
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        # 1) user.primary_branch как метод
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        # 1b) user.primary_branch как атрибут
        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        # 1c) user.branch (если так хранится)
        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 1d) если у пользователя есть M2M/through связи на филиалы: user.branches
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

        # 1e) если есть user.branch_memberships -> branch
        try:
            if hasattr(user, "branch_memberships"):
                ms = (
                    user.branch_memberships
                    .select_related("branch")
                )
                if company_id:
                    ms = ms.filter(branch__company_id=company_id)
                m = ms.first()
                if m and getattr(m, "branch", None):
                    return m.branch
        except Exception:
            pass

        # 1f) если на модели User есть поле/свойство branch_ids (как в /me)
        #     выбираем первый филиал этой компании
        branch_ids = getattr(user, "branch_ids", None)
        if branch_ids:
            try:
                b = (
                    Branch.objects
                    .filter(id__in=list(branch_ids), company_id=company_id)
                    .first()
                )
                if b:
                    return b
            except Exception:
                # на всякий случай не роняем
                pass

        # 2) request.branch как результат работы middleware
        if req and hasattr(req, "branch"):
            b = getattr(req, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        return None

    def _auto_branch(self) -> Optional[Branch]:
        """
        Активный филиал:
          1) «Жёсткий» филиал сотрудника (primary / branch / branch_ids / memberships / request.branch)
          2) ?branch=<uuid> в запросе (если принадлежит компании и НЕТ жёсткого филиала)
          3) None (нет филиала — глобальный режим по всей компании, но только записи без branch)
        """
        req = self._request()
        user = self._user()
        if not req or not user or not getattr(user, "is_authenticated", False):
            return None

        # чтобы не дергать логику по несколько раз на один запрос
        cached = getattr(req, "_cached_auto_branch", None)
        if cached is not None:
            return cached

        company = self._company()
        company_id = getattr(company, "id", None)

        # 1) сначала ищем жёстко назначенный филиал
        fixed_branch = self._fixed_branch_from_user(company)
        if fixed_branch is not None:
            setattr(req, "branch", fixed_branch)
            setattr(req, "_cached_auto_branch", fixed_branch)
            return fixed_branch

        # 2) если у пользователя НЕТ назначенного филиала — позволяем выбирать через ?branch
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
                # чужой/битый UUID — игнорируем
                pass

        # 3) никакого филиала → None (работаем по компании, но без филиалов)
        setattr(req, "_cached_auto_branch", None)
        return None

    @staticmethod
    def _model_has_field(model, field_name: str) -> bool:
        try:
            return any(f.name == field_name for f in model._meta.get_fields())
        except Exception:
            return False

    def _filter_qs_company_branch(
        self,
        qs,
        company_field: Optional[str] = None,
        branch_field: Optional[str] = None,
    ):
        """
        Ограничение queryset текущей company / branch.

        По умолчанию смотрим на поля самой модели:
            company / branch

        Но если данные живут не на самой модели, а через FK (например,
        AgentRequestItem -> cart -> company/branch), можно передать:
            company_field="cart__company"
            branch_field="cart__branch"

        НОВАЯ ЛОГИКА:
            - если branch определён → фильтруем по этому branch;
            - если branch is None → показываем только записи с branch IS NULL.
        """

        company = self._company()
        branch = self._auto_branch()
        model = qs.model

        # company
        if company is not None:
            if company_field:
                qs = qs.filter(**{company_field: company})
            elif self._model_has_field(model, "company"):
                qs = qs.filter(company=company)

        # branch
        if branch_field:
            if branch is not None:
                # есть активный филиал → только он
                qs = qs.filter(**{branch_field: branch})
            else:
                # филиал не выбран → только глобальные записи без филиала
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
        # пробрасываем request, чтобы сериализаторы могли использовать company/branch/юзера
        ctx = super().get_serializer_context() if hasattr(super(), "get_serializer_context") else {}
        ctx["request"] = self.request
        return ctx

    def _save_with_company_branch(self, serializer, **extra):
        """
        Безопасно подставляет company/branch только если такие поля есть у модели.

        ВАЖНО:
        - если у пользователя есть активный филиал → всегда жёстко проставляем его в branch;
        - если филиала нет → branch не трогаем (можно создавать как глобальные, так и по филиалам,
          если это позволено сериализатором/валидаторами).
        """
        model = serializer.Meta.model
        kwargs = dict(extra)

        company = self._company()
        if self._model_has_field(model, "company") and company is not None:
            kwargs.setdefault("company", company)

        if self._model_has_field(model, "branch"):
            branch = self._auto_branch()
            if branch is not None:
                # сотрудник с филиалом — жёстко пишем его, игнорируя поле в payload
                kwargs["branch"] = branch
            # если branch is None — НЕ подставляем, пусть решает сериализатор/валидатор

        serializer.save(**kwargs)

    def perform_create(self, serializer):
        self._save_with_company_branch(serializer)

    def perform_update(self, serializer):
        self._save_with_company_branch(serializer)


# ========= Утилиты для выборок суперпользователя в некоторых вьюхах =========
def _get_company(user):
    """
    Помощник, если нужно руками получить "компанию пользователя" вне миксина.
    Для суперпользователя возвращаем None (без ограничения по company).

    NEW: если у юзера нет company, но есть branch с company — берём её.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return None

    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)

    return None

# ========= Утилиты для выборок суперпользователя в некоторых вьюхах =========



# ===========================
#  Contacts
# ===========================
class ContactListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.select_related("company", "branch", "owner").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "email", "phone", "client_company"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        # owner + company/branch
        self._save_with_company_branch(serializer, owner=self.request.user)


class ContactRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.select_related("company", "branch", "owner").all()


# ===========================
#  Pipelines
# ===========================
class PipelineListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.select_related("company", "branch", "owner").all()
    filter_backends = [filters.SearchFilter, DjangoFilterBackend]
    search_fields = ["name"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        self._save_with_company_branch(serializer, owner=self.request.user)


class PipelineRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.select_related("company", "branch", "owner").all()


# ===========================
#  Deals
# ===========================
class DealListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.select_related("company", "branch", "pipeline", "contact", "assigned_to").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "stage"]
    filterset_fields = "__all__"


class DealRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.select_related("company", "branch", "pipeline", "contact", "assigned_to").all()


# ===========================
#  Tasks
# ===========================
class TaskListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "description"]
    filterset_fields = "__all__"


class TaskRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.select_related("company", "branch", "assigned_to", "deal").all()


# ===========================
#  Integrations / Analytics
# ===========================
class IntegrationListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class IntegrationRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()


class AnalyticsListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    queryset = Analytics.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


# ===========================
#  Orders
# ===========================
class OrderListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related("items__product")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["order_number", "customer_name", "department", "phone"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        # company/branch проставит миксин
        super().perform_create(serializer)


class OrderRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related("items__product")


def _annotate_product_is_favorite(qs):
    """is_favorite: избранное привязано к компании товара (общее для всех сотрудников)."""
    return qs.annotate(
        is_favorite=Exists(
            ProductFavorite.objects.filter(
                product_id=OuterRef("pk"),
                company_id=OuterRef("company_id"),
            )
        )
    )


# ===========================
#  Product create by barcode (ручной view)
# ===========================
class ProductListView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode"]
    ordering_fields = ["created_at", "updated_at", "price"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            Product.objects
            .select_related(
                "company",
                "branch",
                "brand",
                "category",
                "client",
                "created_by",
                "characteristics",  # OneToOne
            )
            .prefetch_related(
                "item_make",
                "packages",
                "recipe_items__item_make",
                product_images_prefetch,
            )
        )
        qs = self._filter_qs_company_branch(qs)
        return _annotate_product_is_favorite(qs)

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)

        qp = self.request.query_params
        suppliers_csv = (qp.get("suppliers") or "").strip()
        supplier_one = (qp.get("supplier") or "").strip()
        supplier_ids = (
            [x.strip() for x in suppliers_csv.split(",") if x.strip()]
            if suppliers_csv
            else ([supplier_one] if supplier_one else [])
        )
        if supplier_ids:
            parsed_ids = []
            for s in supplier_ids:
                try:
                    parsed_ids.append(UUID(str(s)))
                except (ValueError, TypeError, AttributeError):
                    continue
            if not parsed_ids:
                qs = qs.none()
            else:
                company = self._company()
                if company is None:
                    qs = qs.none()
                else:
                    supplier_qs = Client.objects.filter(
                        company=company,
                        type=Client.StatusClient.SUPPLIERS,
                        id__in=parsed_ids,
                    )
                    branch = self._auto_branch()
                    if branch is not None:
                        supplier_qs = supplier_qs.filter(branch__in=[None, branch])
                    allowed = list(supplier_qs.values_list("id", flat=True))
                    qs = qs.filter(client_id__in=allowed) if allowed else qs.none()

        hk = (qp.get("hotkey_group") or qp.get("group") or "").strip().upper()
        if hk and any(hk == c[0] for c in Product.HotkeyGroup.choices):
            qs = qs.filter(hotkey_group=hk)

        # Всегда: избранные сверху. Дальше — стандартная сортировка (ordering filter / default ordering).
        current = list(qs.query.order_by) or []
        # если уже есть сортировка по is_favorite — не дублируем
        if not any("is_favorite" in o for o in current):
            qs = qs.order_by("-is_favorite", *current)
        return qs


class CompactProductCursorPagination(CursorPagination):
    page_size = 30
    ordering = "-created_at"


class ProductCompactListView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """Компактный список товаров для бесконечного скролла — лёгкий сериализатор + курсорная пагинация."""
    serializer_class = ProductListSerializer
    pagination_class = CompactProductCursorPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode"]
    ordering_fields = ["created_at", "updated_at", "price"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            Product.objects
            .select_related("brand", "category")  # Оптимизация: загружаем brand и category одним запросом (на случай будущего использования)
            .only(
                "id",
                "name",
                "price",
                "quantity",
                "brand_id",
                "category_id",
                "code",
                "article",
                "company_id",
                "hotkey_group",
            )
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=ProductImage.objects.filter(is_primary=True).only("id", "image", "is_primary"),
                ),
            )
        )
        qs = self._filter_qs_company_branch(qs)
        return _annotate_product_is_favorite(qs)

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)
        qp = self.request.query_params
        hk = (qp.get("hotkey_group") or qp.get("group") or "").strip().upper()
        if hk and any(hk == c[0] for c in Product.HotkeyGroup.choices):
            qs = qs.filter(hotkey_group=hk)
        current = list(qs.query.order_by) or []
        if not any("is_favorite" in o for o in current):
            qs = qs.order_by("-is_favorite", *current)
        return qs


class ProductCreateByBarcodeAPIView(CompanyBranchRestrictedMixin, generics.CreateAPIView):
    """
    Создание товара только по штрих-коду (если найден в глобальной базе).
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = self._company()
        branch = self._auto_branch()
        data = request.data

        barcode = (data.get("barcode") or "").strip()
        description = (data.get("description") or "").strip()
        article = (data.get("article") or "").strip()

        if not barcode:
            return Response({"barcode": "Укажите штрих-код."}, status=status.HTTP_400_BAD_REQUEST)

        # Дубликат внутри компании
        if Product.objects.filter(company=company, barcode=barcode).exists():
            return Response(
                {"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gp = (
            GlobalProduct.objects
            .select_related("brand", "category")
            .filter(barcode=barcode)
            .first()
        )
        if not gp:
            return Response(
                {"barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # kind
        kind_value = _parse_kind(data.get("kind"), Product)

        # decimals
        try:
            purchase_price = _parse_decimal(data.get("purchase_price", 0), "purchase_price")
            markup_percent = _parse_decimal(data.get("markup_percent", 0), "markup_percent")
            discount_percent = _parse_decimal(data.get("discount_percent", 0), "discount_percent")
        except ValueError as e:
            return Response({str(e): "Неверный формат числа."}, status=status.HTTP_400_BAD_REQUEST)

        # ====== FIX: двусторонняя логика price <-> markup_percent ======
        price_raw = data.get("price", None)

        # если прислали price — считаем markup_percent
        if price_raw not in (None, ""):
            try:
                price = _to_dec(price_raw)
            except Exception:
                return Response({"price": "Неверный формат цены продажи."}, status=status.HTTP_400_BAD_REQUEST)
            markup_percent = _calc_markup(purchase_price, price)
        else:
            # иначе считаем price из markup_percent
            price = _calc_price(purchase_price, markup_percent)

        # quantity
        try:
            quantity = _parse_int_nonneg(data.get("quantity", 0), "quantity")
        except ValueError:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # date -> aware datetime
        raw_date = data.get("date")
        try:
            date_value = _parse_date_to_aware_datetime(raw_date) if raw_date else timezone.now()
        except ValueError:
            return Response(
                {"date": "Неверный формат даты. Используйте YYYY-MM-DD или ISO datetime."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # unit/is_weight/country/expiration
        unit = (data.get("unit") or "шт.").strip()
        is_weight = _parse_bool_like(data.get("is_weight"))
        country = (data.get("country") or "").strip()

        expiration_raw = data.get("expiration_date")
        expiration_date = None
        if expiration_raw not in (None, ""):
            expiration_date = parse_date(str(expiration_raw))
            if not expiration_date:
                return Response(
                    {"expiration_date": "Неверный формат. Используйте YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # локальные справочники
        brand = ProductBrand.objects.get_or_create(company=company, name=gp.brand.name)[0] if gp.brand else None
        category = ProductCategory.objects.get_or_create(company=company, name=gp.category.name)[0] if gp.category else None

        # packages_input
        packages_input = data.get("packages_input") or data.get("packages")
        if not isinstance(packages_input, list):
            packages_input = []

        # create product
        product = Product.objects.create(
            company=company,
            branch=branch,
            kind=kind_value,

            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,

            article=article,
            description=description,

            unit=unit,
            is_weight=is_weight,

            purchase_price=purchase_price,
            markup_percent=markup_percent,
            price=price,
            discount_percent=discount_percent,

            quantity=quantity,
            country=country,
            expiration_date=expiration_date,

            date=date_value,
            created_by=request.user,
            stock=_parse_bool_like(data.get("stock", False)),
        )

        # characteristics
        chars_data = data.get("characteristics")
        if isinstance(chars_data, dict):
            ProductCharacteristics.objects.update_or_create(
                product=product,
                defaults={
                    "company": company,
                    "branch": branch,
                    "height_cm": chars_data.get("height_cm") or None,
                    "width_cm": chars_data.get("width_cm") or None,
                    "depth_cm": chars_data.get("depth_cm") or None,
                    "factual_weight_kg": chars_data.get("factual_weight_kg") or None,
                    "description": chars_data.get("description") or "",
                },
            )

        # packages bulk_create
        packages_to_create = []
        for pkg in packages_input:
            if not isinstance(pkg, dict):
                continue
            name = (pkg.get("name") or "").strip()
            if not name:
                continue
            try:
                qip = int(pkg.get("quantity_in_package"))
            except (TypeError, ValueError):
                continue
            unit_pkg = (pkg.get("unit") or "").strip()
            piece_raw = pkg.get("piece_unit_price")
            if piece_raw in (None, "", "null"):
                return Response(
                    {
                        "packages_input": "Для каждой упаковки укажите piece_unit_price (цена за штуку при поштучной продаже).",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                piece_unit_price = Decimal(str(piece_raw))
            except Exception:
                return Response(
                    {"packages_input": "Неверный формат piece_unit_price в упаковке."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if piece_unit_price < 0:
                return Response(
                    {"packages_input": "piece_unit_price не может быть отрицательной."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            piece_unit_price = piece_unit_price.quantize(_Q2, rounding=ROUND_HALF_UP)

            packages_to_create.append(
                ProductPackage(
                    product=product,
                    company=company,
                    branch=branch,
                    name=name,
                    quantity_in_package=qip,
                    unit=unit_pkg,
                    piece_unit_price=piece_unit_price,
                )
            )

        if packages_to_create:
            ProductPackage.objects.bulk_create(packages_to_create)

        raw_promo = data.get("promotion_rules_input")
        if raw_promo is None:
            raw_promo = data.get("promotion_rules")
        try:
            sync_product_promotion_tiers(
                product,
                raw_promo,
                stock_enabled=bool(product.stock),
                partial=False,
            )
        except serializers.ValidationError as ve:
            return Response(
                ve.detail if isinstance(ve.detail, dict) else {"detail": ve.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product = (
            _annotate_product_is_favorite(
                Product.objects.filter(pk=product.pk)
                .select_related("company", "branch", "brand", "category", "client", "created_by", "characteristics")
                .prefetch_related(
                    "item_make",
                    "packages",
                    "recipe_items__item_make",
                    "promotion_tiers",
                    product_images_prefetch,
                )
            )
            .get()
        )
        ser = self.get_serializer(product, context=self.get_serializer_context())
        return Response(ser.data, status=status.HTTP_201_CREATED)


class ProductFavoriteAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    POST /api/main/products/<product_id>/favorite/
    body: { "is_favorite": true|false }  (если не передали — переключает)

    Избранное общее на компанию товара (все сотрудники видят одно и то же).
    """

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, product_id, *args, **kwargs):
        product = get_object_or_404(
            self._filter_qs_company_branch(Product.objects.all()),
            id=product_id,
        )

        req_company = self._company()
        if req_company is not None and req_company.id != product.company_id:
            raise PermissionDenied("Товар не принадлежит вашей компании.")

        fav_company = product.company

        raw = request.data.get("is_favorite", None) if isinstance(request.data, dict) else None
        if raw is None:
            exists = ProductFavorite.objects.filter(company=fav_company, product=product).exists()
            if exists:
                ProductFavorite.objects.filter(company=fav_company, product=product).delete()
                return Response({"product_id": str(product.id), "is_favorite": False}, status=status.HTTP_200_OK)
            ProductFavorite.objects.create(company=fav_company, product=product)
            return Response({"product_id": str(product.id), "is_favorite": True}, status=status.HTTP_200_OK)

        if isinstance(raw, bool):
            is_fav = raw
        else:
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "y", "да", "on"):
                is_fav = True
            elif s in ("0", "false", "no", "n", "нет", "off"):
                is_fav = False
            else:
                raise ValidationError({"is_favorite": "Ожидается boolean (true/false)."})
        if is_fav:
            ProductFavorite.objects.get_or_create(company=fav_company, product=product)
        else:
            ProductFavorite.objects.filter(company=fav_company, product=product).delete()

        return Response({"product_id": str(product.id), "is_favorite": is_fav}, status=status.HTTP_200_OK)


class MarketSaleEmployeePayProfileListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = MarketSaleEmployeePayProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["user", "branch"]
    ordering_fields = ["id"]

    def get_queryset(self):
        company = self._company()
        if not company:
            return MarketSaleEmployeePayProfile.objects.none()
        qs = MarketSaleEmployeePayProfile.objects.filter(company=company)
        branch = self._auto_branch()
        if branch is not None:
            qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
        return qs.select_related("user", "branch").order_by("user_id", "-branch_id")

    def perform_create(self, serializer):
        serializer.save()


class MarketSaleEmployeePayProfileRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = MarketSaleEmployeePayProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        company = self._company()
        if not company:
            return MarketSaleEmployeePayProfile.objects.none()
        qs = MarketSaleEmployeePayProfile.objects.filter(company=company)
        branch = self._auto_branch()
        if branch is not None:
            qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
        return qs.select_related("user", "branch")


# ==========================
# Product create manual
# ==========================
class ProductCreateManualAPIView(CompanyBranchRestrictedMixin, generics.CreateAPIView):
    """
    Ручное создание товара + (опционально) добавление в глобальную базу.
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    def _normalize_status(self, raw):
        if raw in (None, "", "null"):
            return None
        v = str(raw).strip().lower()
        mapping = {
            "pending":  Product.Status.PENDING,
            "accepted": Product.Status.ACCEPTED,
            "rejected": Product.Status.REJECTED,
            "ожидание": Product.Status.PENDING,
            "принят":   Product.Status.ACCEPTED,
            "отказ":    Product.Status.REJECTED,
        }
        if v in mapping:
            return mapping[v]
        codes = {c[0] for c in Product.Status.choices}
        if v in codes:
            return v
        raise ValueError(f"Недопустимый статус. Допустимые: {', '.join(sorted(codes))}.")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = self._company()
        branch = self._auto_branch()
        data = request.data

        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        article = (data.get("article") or "").strip()

        if not name:
            return Response({"name": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)

        barcode = (data.get("barcode") or "").strip() or None
        if barcode and Product.objects.filter(company=company, barcode=barcode).exists():
            return Response(
                {"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # kind
        kind_value = _parse_kind(data.get("kind"), Product)

        # decimals
        try:
            purchase_price = _parse_decimal(data.get("purchase_price", 0), "purchase_price")
            markup_percent = _parse_decimal(data.get("markup_percent", 0), "markup_percent")
            discount_percent = _parse_decimal(data.get("discount_percent", 0), "discount_percent")
        except ValueError as e:
            return Response({str(e): "Неверный формат числа."}, status=status.HTTP_400_BAD_REQUEST)

        # ====== FIX: двусторонняя логика price <-> markup_percent ======
        price_raw = data.get("price", None)
        price_provided = price_raw not in (None, "")
        if price_raw not in (None, ""):
            try:
                price = _to_dec(price_raw)
            except Exception:
                return Response({"price": "Неверный формат цены продажи."}, status=status.HTTP_400_BAD_REQUEST)
            markup_percent = _calc_markup(purchase_price, price)
        else:
            price = _calc_price(purchase_price, markup_percent)

        # quantity
        try:
            quantity = _parse_int_nonneg(data.get("quantity", 0), "quantity")
        except ValueError:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # status
        try:
            status_value = self._normalize_status(data.get("status"))
        except ValueError as e:
            return Response({"status": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # date (aware datetime)
        raw_date = data.get("date")
        try:
            date_value = _parse_date_to_aware_datetime(raw_date) if raw_date else timezone.now()
        except ValueError:
            return Response(
                {"date": "Неверный формат даты. Используйте YYYY-MM-DD или ISO datetime."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # unit/is_weight/country/expiration
        unit = (data.get("unit") or "шт.").strip()
        is_weight = _parse_bool_like(data.get("is_weight"))
        country = (data.get("country") or "").strip()

        expiration_raw = data.get("expiration_date")
        expiration_date = None
        if expiration_raw not in (None, ""):
            expiration_date = parse_date(str(expiration_raw))
            if not expiration_date:
                return Response(
                    {"expiration_date": "Неверный формат. Используйте YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # brand/category (через global)
        brand_name = (data.get("brand_name") or "").strip()
        category_name = (data.get("category_name") or "").strip()
        g_brand = GlobalBrand.objects.get_or_create(name=brand_name)[0] if brand_name else None
        g_category = GlobalCategory.objects.get_or_create(name=category_name)[0] if category_name else None

        brand = ProductBrand.objects.get_or_create(company=company, name=g_brand.name)[0] if g_brand else None
        category = ProductCategory.objects.get_or_create(company=company, name=g_category.name)[0] if g_category else None

        # client
        client = None
        client_id = data.get("client")
        if client_id:
            client = get_object_or_404(Client, id=client_id, company=company)

        # packages_input
        packages_input = data.get("packages_input") or data.get("packages")
        if not isinstance(packages_input, list):
            packages_input = []

        product = Product(
            company=company,
            branch=branch,
            kind=kind_value,

            name=name,
            barcode=barcode,
            brand=brand,
            category=category,

            article=article,
            description=description,

            unit=unit,
            is_weight=is_weight,

            purchase_price=purchase_price,
            markup_percent=markup_percent,
            price=price,
            discount_percent=discount_percent,

            quantity=quantity,

            client=client,
            status=status_value,
            date=date_value,

            country=country,
            expiration_date=expiration_date,

            created_by=request.user,
            stock=_parse_bool_like(data.get("stock", False)),
        )
        if price_provided:
            setattr(product, "_manual_price", True)
        product.save()

        # characteristics
        chars_data = data.get("characteristics")
        if isinstance(chars_data, dict):
            ProductCharacteristics.objects.update_or_create(
                product=product,
                defaults={
                    "company": company,
                    "branch": branch,
                    "height_cm": chars_data.get("height_cm") or None,
                    "width_cm": chars_data.get("width_cm") or None,
                    "depth_cm": chars_data.get("depth_cm") or None,
                    "factual_weight_kg": chars_data.get("factual_weight_kg") or None,
                    "description": chars_data.get("description") or "",
                },
            )

        # ====== recipe (приоритет над item_make) ======
        recipe_input = data.get("recipe")
        if recipe_input and isinstance(recipe_input, list):
            # Validate recipe entries
            seen_ids = set()
            recipe_entries = []
            for idx, entry in enumerate(recipe_input):
                if not isinstance(entry, dict):
                    return Response(
                        {"recipe": f"Элемент #{idx}: ожидается объект."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                raw_id = entry.get("id")
                raw_qty = entry.get("qty_per_unit")
                if not raw_id:
                    return Response(
                        {"recipe": f"Элемент #{idx}: отсутствует id."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if raw_qty is None:
                    return Response(
                        {"recipe": f"Элемент #{idx}: отсутствует qty_per_unit."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    qty_per_unit = Decimal(str(raw_qty))
                except Exception:
                    return Response(
                        {"recipe": f"Элемент #{idx}: qty_per_unit — неверный формат числа."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if qty_per_unit <= 0:
                    return Response(
                        {"recipe": f"Элемент #{idx}: qty_per_unit должен быть > 0."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                str_id = str(raw_id)
                if str_id in seen_ids:
                    return Response(
                        {"recipe": f"Элемент #{idx}: дубликат id={raw_id}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen_ids.add(str_id)
                recipe_entries.append({"id": str_id, "qty_per_unit": qty_per_unit})

            # Verify all item_make ids exist and belong to company
            im_ids = [e["id"] for e in recipe_entries]
            ims_qs = ItemMake.objects.filter(id__in=im_ids, company=company).select_for_update()
            ims_map = {str(im.id): im for im in ims_qs}
            missing = [eid for eid in im_ids if eid not in ims_map]
            if missing:
                return Response(
                    {"recipe": f"Сырьё не найдено или принадлежит другой компании: {missing}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            product_qty = Decimal(str(product.quantity or 0))

            # Check stock sufficiency and deduct
            for entry in recipe_entries:
                im = ims_map[entry["id"]]
                required = entry["qty_per_unit"] * product_qty
                if im.quantity < required:
                    return Response(
                        {"recipe": (
                            f"Недостаточно сырья «{im.name}»: "
                            f"требуется {required}, доступно {im.quantity}."
                        )},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            # Deduct raw materials and save recipe
            recipe_objects = []
            for entry in recipe_entries:
                im = ims_map[entry["id"]]
                required = entry["qty_per_unit"] * product_qty
                im.quantity -= required
                im.save(update_fields=["quantity", "updated_at"])
                recipe_objects.append(
                    ProductRecipeItem(
                        product=product,
                        item_make=im,
                        qty_per_unit=entry["qty_per_unit"],
                    )
                )
            ProductRecipeItem.objects.bulk_create(recipe_objects)

            # Also set the M2M item_make for backward compat
            product.item_make.set(list(ims_map.values()))

        else:
            # Backward compatibility: handle old item_make field (no recipe)
            item_make_input = data.get("item_make") or data.get("item_make_ids")
            if item_make_input:
                if isinstance(item_make_input, str):
                    item_make_ids = [item_make_input]
                elif isinstance(item_make_input, (list, tuple)):
                    item_make_ids = list(item_make_input)
                else:
                    item_make_ids = []

                ims = ItemMake.objects.filter(id__in=item_make_ids, company=company)
                if len(item_make_ids) != ims.count():
                    return Response(
                        {"item_make": "Один или несколько item_make не найдены или принадлежат другой компании."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                product.item_make.set(ims)

        # packages
        packages_to_create = []
        for pkg in packages_input:
            if not isinstance(pkg, dict):
                continue
            name_pkg = (pkg.get("name") or "").strip()
            if not name_pkg:
                continue
            try:
                qip = int(pkg.get("quantity_in_package"))
            except (TypeError, ValueError):
                continue
            unit_pkg = (pkg.get("unit") or "").strip()
            piece_raw = pkg.get("piece_unit_price")
            if piece_raw in (None, "", "null"):
                return Response(
                    {
                        "packages_input": "Для каждой упаковки укажите piece_unit_price (цена за штуку при поштучной продаже).",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                piece_unit_price = Decimal(str(piece_raw))
            except Exception:
                return Response(
                    {"packages_input": "Неверный формат piece_unit_price в упаковке."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if piece_unit_price < 0:
                return Response(
                    {"packages_input": "piece_unit_price не может быть отрицательной."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            piece_unit_price = piece_unit_price.quantize(_Q2, rounding=ROUND_HALF_UP)

            packages_to_create.append(
                ProductPackage(
                    product=product,
                    company=company,
                    branch=branch,
                    name=name_pkg,
                    quantity_in_package=qip,
                    unit=unit_pkg,
                    piece_unit_price=piece_unit_price,
                )
            )

        if packages_to_create:
            ProductPackage.objects.bulk_create(packages_to_create)

        raw_promo = data.get("promotion_rules_input")
        if raw_promo is None:
            raw_promo = data.get("promotion_rules")
        try:
            sync_product_promotion_tiers(
                product,
                raw_promo,
                stock_enabled=bool(product.stock),
                partial=False,
            )
        except serializers.ValidationError as ve:
            return Response(
                ve.detail if isinstance(ve.detail, dict) else {"detail": ve.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # add to global product base (optional)
        if barcode:
            GlobalProduct.objects.get_or_create(
                barcode=barcode,
                defaults={"name": name, "brand": g_brand, "category": g_category},
            )

        # Refetch with all related data for serialization
        product = (
            _annotate_product_is_favorite(
                Product.objects.filter(pk=product.pk)
                .select_related("company", "branch", "brand", "category", "client", "created_by", "characteristics")
                .prefetch_related(
                    "item_make",
                    "packages",
                    "recipe_items__item_make",
                    "promotion_tiers",
                    product_images_prefetch,
                )
            )
            .get()
        )
        ser = self.get_serializer(product, context=self.get_serializer_context())
        return Response(ser.data, status=status.HTTP_201_CREATED)


class ProductRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer

    queryset = (
        Product.objects
        .select_related(
            "company",
            "branch",
            "brand",
            "category",
            "client",
            "created_by",
            "characteristics",
        )
        .prefetch_related(
            "item_make",
            "packages",
            "recipe_items__item_make",
            "promotion_tiers",
            product_images_prefetch,
        )
        .all()
    )

    def get_queryset(self):
        return _annotate_product_is_favorite(super().get_queryset())

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        data = request.data

        recipe_input = data.get("recipe")
        has_recipe = recipe_input is not None

        if has_recipe:
            if not isinstance(recipe_input, list):
                return Response(
                    {"recipe": "Ожидается массив."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Parse and validate new recipe
            seen_ids = set()
            new_recipe_entries = []
            recipe_errors = []
            for idx, entry in enumerate(recipe_input):
                if not isinstance(entry, dict):
                    recipe_errors.append(f"Элемент #{idx}: ожидается объект.")
                    continue
                raw_id = entry.get("id")
                raw_qty = entry.get("qty_per_unit")
                if not raw_id:
                    recipe_errors.append(f"Элемент #{idx}: отсутствует id.")
                    continue
                if raw_qty is None:
                    recipe_errors.append(f"Элемент #{idx}: отсутствует qty_per_unit.")
                    continue
                try:
                    qty_per_unit = Decimal(str(raw_qty))
                except Exception:
                    recipe_errors.append(f"Элемент #{idx}: qty_per_unit — неверный формат.")
                    continue
                if qty_per_unit <= 0:
                    recipe_errors.append(f"Элемент #{idx}: qty_per_unit должен быть > 0.")
                    continue
                str_id = str(raw_id)
                if str_id in seen_ids:
                    recipe_errors.append(f"Элемент #{idx}: дубликат id={raw_id}.")
                    continue
                seen_ids.add(str_id)
                new_recipe_entries.append({"id": str_id, "qty_per_unit": qty_per_unit})

            if recipe_errors:
                return Response(
                    {"recipe": recipe_errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # ---- Read old state ----
        old_quantity = Decimal(str(instance.quantity or 0))
        old_recipe_qs = ProductRecipeItem.objects.filter(product=instance).select_related("item_make")
        old_recipe_map = {str(ri.item_make_id): ri.qty_per_unit for ri in old_recipe_qs}

        # ---- Determine new state ----
        new_quantity_raw = data.get("quantity")
        new_quantity = Decimal(str(new_quantity_raw)) if new_quantity_raw is not None else old_quantity

        if has_recipe:
            new_recipe_map = {e["id"]: e["qty_per_unit"] for e in new_recipe_entries}
        else:
            new_recipe_map = dict(old_recipe_map)

        # ---- Compute deltas ----
        all_item_ids = set(old_recipe_map.keys()) | set(new_recipe_map.keys())
        deltas = {}
        for im_id in all_item_ids:
            old_req = old_recipe_map.get(im_id, Decimal("0")) * old_quantity
            new_req = new_recipe_map.get(im_id, Decimal("0")) * new_quantity
            delta = new_req - old_req
            if delta != 0:
                deltas[im_id] = delta

        if deltas:
            company = self._company()
            # Lock rows for concurrent safety
            ims_qs = (
                ItemMake.objects
                .filter(id__in=deltas.keys(), company=company)
                .select_for_update()
            )
            ims_map = {str(im.id): im for im in ims_qs}

            # Check all exist
            missing = [eid for eid in deltas if eid not in ims_map]
            if missing:
                return Response(
                    {"recipe": f"Сырьё не найдено: {missing}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check stock sufficiency for items that need deduction (delta > 0)
            insufficiency_errors = []
            for im_id, delta in deltas.items():
                if delta > 0:
                    im = ims_map[im_id]
                    if im.quantity < delta:
                        insufficiency_errors.append(
                            f"Недостаточно сырья «{im.name}»: "
                            f"нужно досписать {delta}, доступно {im.quantity}."
                        )

            if insufficiency_errors:
                return Response(
                    {"recipe": insufficiency_errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Apply deltas
            for im_id, delta in deltas.items():
                im = ims_map[im_id]
                im.quantity -= delta
                im.save(update_fields=["quantity", "updated_at"])

        # ---- Update recipe rows ----
        if has_recipe:
            ProductRecipeItem.objects.filter(product=instance).delete()
            if new_recipe_entries:
                company = self._company()
                ims_qs = ItemMake.objects.filter(
                    id__in=[e["id"] for e in new_recipe_entries],
                    company=company,
                )
                ims_map_for_recipe = {str(im.id): im for im in ims_qs}
                ProductRecipeItem.objects.bulk_create([
                    ProductRecipeItem(
                        product=instance,
                        item_make=ims_map_for_recipe[e["id"]],
                        qty_per_unit=e["qty_per_unit"],
                    )
                    for e in new_recipe_entries
                ])
                # Update M2M for backward compat
                instance.item_make.set(list(ims_map_for_recipe.values()))

        # ---- Let DRF serializer handle all other fields ----
        # Remove recipe from data so the serializer doesn't choke on it
        mutable_data = data.copy() if hasattr(data, "copy") else dict(data)
        mutable_data.pop("recipe", None)

        serializer = self.get_serializer(instance, data=mutable_data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        # Refetch with all prefetches
        instance = (
            _annotate_product_is_favorite(
                Product.objects.filter(pk=instance.pk)
                .select_related("company", "branch", "brand", "category", "client", "created_by", "characteristics")
                .prefetch_related(
                    "item_make",
                    "packages",
                    "recipe_items__item_make",
                    "promotion_tiers",
                    product_images_prefetch,
                )
            )
            .get()
        )
        return Response(
            self.get_serializer(instance).data,
            status=status.HTTP_200_OK,
        )


class ProductBulkDeleteAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    DELETE /api/main/products/bulk-delete/
    Body: {"ids": [...], "soft": false, "require_all": false}
    """
    def delete(self, request, *args, **kwargs):
        serializer = BulkIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]
        soft = serializer.validated_data["soft"]
        require_all = serializer.validated_data["require_all"]

        # в рамках компании и текущего филиала/глобальных
        qs = self._filter_qs_company_branch(Product.objects.all()).filter(id__in=ids)
        found_map = {p.id: p for p in qs}
        not_found = [str(id_) for id_ in ids if id_ not in found_map]

        results = {"deleted": [], "protected": [], "not_found": not_found}

        def _delete_one(p: Product):
            try:
                if soft and hasattr(p, "is_active"):
                    p.is_active = False
                    p.save(update_fields=["is_active"])
                else:
                    p.delete()
                results["deleted"].append(str(p.id))
            except ProtectedError:
                results["protected"].append(str(p.id))

        if require_all:
            try:
                with transaction.atomic():
                    for p in found_map.values():
                        _delete_one(p)
                    if results["protected"]:
                        raise ProtectedError("protected", None)
            except ProtectedError:
                return Response(
                    {
                        "detail": "Некоторые продукты защищены связями, удаление откатено.",
                        "protected": results["protected"],
                        "not_found": results["not_found"],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(results, status=status.HTTP_200_OK)

        for p in found_map.values():
            _delete_one(p)

        http_status = status.HTTP_200_OK if not results["protected"] else status.HTTP_207_MULTI_STATUS
        return Response(results, status=http_status)


class ProductByBarcodeAPIView(CompanyBranchRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = ProductSerializer
    lookup_field = "barcode"

    def get_queryset(self):
        qs = (
            Product.objects
            .select_related(
                "company",
                "branch",
                "brand",
                "category",
                "client",
                "created_by",
                "characteristics",
            )
            .prefetch_related(
                "item_make",
                "packages",
                product_images_prefetch,
            )
            .all()
        )
        return _annotate_product_is_favorite(self._filter_qs_company_branch(qs))

    def get_object(self):
        from rest_framework.exceptions import NotFound

        barcode = self.kwargs.get("barcode")
        if not barcode:
            raise NotFound(detail="Штрих-код не указан")

        product = self.get_queryset().filter(barcode=barcode).first()
        if not product:
            raise NotFound(detail="Товар с таким штрих-кодом не найден")
        return product

# ===========================
#  Reviews
# ===========================
class ReviewListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.select_related("company", "branch", "user", "product").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        self._save_with_company_branch(serializer, user=self.request.user)


class ReviewRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.select_related("company", "branch", "user", "product").all()


# ===========================
#  Notifications
# ===========================
class NotificationListView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.select_related("company", "branch", "user").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class NotificationDetailView(CompanyBranchRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.select_related("company", "branch", "user").all()


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"status": "Все уведомления прочитаны"}, status=status.HTTP_200_OK)


# ===========================
#  Events
# ===========================
class EventListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.select_related("company", "branch", "user").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class EventRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.select_related("company", "branch", "user").all()


# ===========================
#  Warehouses
# ===========================
class WarehouseListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "location"]
    filterset_fields = "__all__"


class WarehouseRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.select_related("company", "branch").all()


class WarehouseEventListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.select_related("company", "branch", "warehouse", "responsible_person").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "client_name"]
    filterset_fields = "__all__"


class WarehouseEventRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.select_related("company", "branch", "warehouse", "responsible_person").all()


# ===========================
#  Product taxonomies
# ===========================
class ProductCategoryListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.select_related("company", "branch", "parent").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_crm_category_name_global_per_company" in msg
                or "uq_crm_category_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Категория с таким названием уже существует."})
            raise


class ProductCategoryRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.select_related("company", "branch", "parent").all()

    def perform_update(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_crm_category_name_global_per_company" in msg
                or "uq_crm_category_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Категория с таким названием уже существует."})
            raise


class ProductBrandListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_crm_brand_name_global_per_company" in msg
                or "uq_crm_brand_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Бренд с таким названием уже существует."})
            raise


class ProductBrandRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.select_related("company", "branch").all()

    def perform_update(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_crm_brand_name_global_per_company" in msg
                or "uq_crm_brand_name_per_branch" in msg
            ):
                raise ValidationError({"name": "Бренд с таким названием уже существует."})
            raise


# ===========================
#  Product views
# ===========================


class ProductByGlobalBarcodeAPIView(CompanyBranchRestrictedMixin, generics.RetrieveAPIView):
    """
    GET /main/products/barcode/<barcode>/
    Возвращает товар ТОЛЬКО из глобальной базы (GlobalProduct).
    """
    serializer_class = GlobalProductReadSerializer
    lookup_field = "barcode"
    # важно определить queryset, иначе миксин выдаст AssertionError
    queryset = GlobalProduct.objects.select_related("brand", "category").all()

    def get_object(self):
        barcode = self.kwargs.get("barcode")
        if not barcode:
            raise NotFound(detail="Штрих-код не указан")
        obj = self.get_queryset().filter(barcode=barcode).first()
        if not obj:
            raise NotFound(detail="Товар с таким штрих-кодом не найден в глобальной базе")
        return obj




# ===========================
#  Order analytics
# ===========================
class OrderAnalyticsView(APIView, CompanyBranchRestrictedMixin):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # берём заказы в рамках company/branch по общей логике миксина
        orders = self._filter_qs_company_branch(Order.objects.all())

        start_date = request.query_params.get("start")
        end_date = request.query_params.get("end")
        status_filter = request.query_params.get("status")

        if start_date:
            start_date = parse_date(start_date)
            orders = orders.filter(date_ordered__gte=start_date)

        if end_date:
            end_date = parse_date(end_date)
            orders = orders.filter(date_ordered__lte=end_date)

        if status_filter:
            orders = orders.filter(status=status_filter)

        total_orders = orders.count()
        total_amount = orders.aggregate(total=Sum("items__total"))["total"] or 0
        average_amount = orders.aggregate(avg=Avg("items__total"))["avg"] or 0

        orders_by_status = orders.values("status").annotate(
            order_count=Count("id"),
            total_amount=Sum("items__total"),
            average_amount=Avg("items__total"),
        )

        response_data = {
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "status": status_filter,
            },
            "summary": {
                "total_orders": total_orders,
                "total_amount": total_amount,
                "average_order_amount": average_amount,
            },
            "orders_by_status": list(orders_by_status),
        }

        return Response(response_data)


# ===========================
#  Clients
# ===========================
def _filter_clients_visible_for_user(qs, user):
    """
    Agents (не owner/admin) видят только своих клиентов.
    Owner/admin видят всех клиентов компании/филиала (фильтр CompanyBranchRestrictedMixin остаётся).
    """
    if user and not _is_owner_like(user):
        return qs.filter(salesperson=user)
    return qs


class ClientListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/clients/
    POST /api/main/clients/
    """
    serializer_class = ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "date"]
    search_fields = ["full_name", "phone", "email"]
    ordering_fields = ["created_at", "updated_at", "date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = self._filter_qs_company_branch(
            Client.objects.select_related("company", "branch").all()
        )
        return _filter_clients_visible_for_user(qs, self.request.user)

    def perform_create(self, serializer):
        # Агенту нельзя создавать "чужих" клиентов — привязываем к нему.
        if not _is_owner_like(self.request.user):
            self._save_with_company_branch(serializer, salesperson=self.request.user)
            return
        # owner/admin может назначать salesperson через payload (или оставить пустым)
        self._save_with_company_branch(serializer)


class ClientRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/clients/<uuid:pk>/
    PATCH  /api/main/clients/<uuid:pk>/
    PUT    /api/main/clients/<uuid:pk>/
    DELETE /api/main/clients/<uuid:pk>/
    """
    serializer_class = ClientSerializer

    def get_queryset(self):
        qs = self._filter_qs_company_branch(
            Client.objects.select_related("company", "branch").all()
        )
        return _filter_clients_visible_for_user(qs, self.request.user)

    def perform_update(self, serializer):
        # Агенту нельзя "перекидывать" клиента на другого salesperson.
        if not _is_owner_like(self.request.user):
            self._save_with_company_branch(serializer, salesperson=self.request.user)
            return
        self._save_with_company_branch(serializer)


def _deal_prefetch():
    return [
        Prefetch("installments", queryset=DealInstallment.objects.order_by("number")),
        Prefetch(
            "payments",
            queryset=DealPayment.objects.select_related("installment", "created_by").order_by("-created_at"),
        ),
    ]


# ===== Deals list/create =====

class ClientDealListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
      GET  /api/main/deals/
      POST /api/main/deals/
      GET  /api/main/clients/<client_id>/deals/
      POST /api/main/clients/<client_id>/deals/
    """
    serializer_class = ClientDealSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["kind", "client"]
    search_fields = ["title", "note"]
    ordering_fields = ["created_at", "updated_at", "amount", "kind"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            ClientDeal.objects
            .select_related("client")
            .prefetch_related(*_deal_prefetch())
        )
        qs = self._filter_qs_company_branch(qs)
        if not _is_owner_like(self.request.user):
            qs = qs.filter(client__salesperson=self.request.user)

        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)

        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        client_id = self.kwargs.get("client_id")
        user = self.request.user

        if not company:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})

        if client_id:
            client_qs = Client.objects.filter(company=company)
            if not _is_owner_like(user):
                client_qs = client_qs.filter(salesperson=user)
            client = get_object_or_404(client_qs, id=client_id)

            # клиент может быть общий (branch=None)
            if branch is not None and client.branch_id not in (None, branch.id):
                raise serializers.ValidationError({"client": "Клиент другого филиала."})

            serializer.save(company=company, branch=branch, client=client)
            return

        client = serializer.validated_data.get("client")
        if not client or client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент не найден в вашей компании."})
        if not _is_owner_like(user) and client.salesperson_id != user.id:
            raise serializers.ValidationError({"client": "Доступ запрещён: это не ваш клиент."})

        if branch is not None and client.branch_id not in (None, branch.id):
            raise serializers.ValidationError({"client": "Клиент другого филиала."})

        serializer.save(company=company, branch=branch)


# ===== Deals retrieve/update/destroy =====

class ClientDealRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView,
):
    """
    GET    /api/main/clients/<client_id>/deals/<uuid:pk>/
    PATCH  /api/main/clients/<client_id>/deals/<uuid:pk>/
    PUT    /api/main/clients/<client_id>/deals/<uuid:pk>/
    DELETE /api/main/clients/<client_id>/deals/<uuid:pk>/
    """
    serializer_class = ClientDealSerializer

    def get_queryset(self):
        qs = (
            ClientDeal.objects
            .select_related("client")
            .prefetch_related(*_deal_prefetch())
        )
        qs = self._filter_qs_company_branch(qs)
        if not _is_owner_like(self.request.user):
            qs = qs.filter(client__salesperson=self.request.user)

        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)

        return qs

    @transaction.atomic
    def perform_update(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        user = self.request.user

        if not company:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})

        new_client = serializer.validated_data.get("client")
        if new_client:
            if new_client.company_id != company.id:
                raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
            if not _is_owner_like(user) and new_client.salesperson_id != user.id:
                raise serializers.ValidationError({"client": "Доступ запрещён: это не ваш клиент."})
            if branch is not None and new_client.branch_id not in (None, branch.id):
                raise serializers.ValidationError({"client": "Клиент другого филиала."})

        deal = serializer.save(company=company, branch=branch)

        # сброс кеша prefetch — чтобы отдать свежие installments/payments
        deal.refresh_from_db()
        serializer.instance = deal


# ===== PAY (создаём DealPayment + обновляем installment) =====

class ClientDealPayAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/deals/<uuid:pk>/pay/
    POST /api/main/clients/<client_id>/deals/<uuid:pk>/pay/

    body:
    {
      "installment_id": "<uuid>" | null,
      "amount": "5000.00" | null,
      "date": "2025-11-10" | null,
      "idempotency_key": "<uuid>",   # ОБЯЗАТЕЛЬНО
      "note": "..." | ""
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        client_id = kwargs.get("client_id")

        deal_qs = self._filter_qs_company_branch(
            ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch())
        ).filter(pk=pk)
        if not _is_owner_like(request.user):
            deal_qs = deal_qs.filter(client__salesperson=request.user)

        if client_id:
            deal_qs = deal_qs.filter(client_id=client_id)

        deal = get_object_or_404(deal_qs)

        if deal.kind != ClientDeal.Kind.DEBT:
            return Response(
                {"detail": "Оплата помесячно доступна только для сделок типа 'debt'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inp = DealPayInputSerializer(data=request.data)
        inp.is_valid(raise_exception=True)
        data = inp.validated_data

        idem = data["idempotency_key"]
        paid_date = data.get("date") or timezone.localdate()
        note = data.get("note", "") or ""
        amount = data.get("amount", None)

        # идемпотентность
        if DealPayment.objects.filter(deal=deal, idempotency_key=idem).exists():
            fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
            return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)

        inst_qs = DealInstallment.objects.select_for_update().filter(deal=deal)

        inst_id = data.get("installment_id")
        if inst_id:
            inst = get_object_or_404(inst_qs, id=inst_id)
        else:
            inst = (
                inst_qs
                .filter(paid_amount__lt=F("amount"))
                .order_by("number")
                .first()
            )
            if not inst:
                return Response({"detail": "Все взносы уже полностью оплачены."}, status=status.HTTP_400_BAD_REQUEST)

        total = (inst.amount or Decimal("0")).quantize(Decimal("0.01"))
        current_paid = (inst.paid_amount or Decimal("0")).quantize(Decimal("0.01"))
        remaining = (total - current_paid).quantize(Decimal("0.01"))

        if remaining <= 0:
            return Response({"detail": f"Взнос №{inst.number} уже полностью оплачен."}, status=status.HTTP_400_BAD_REQUEST)

        pay_amt = remaining if amount is None else Decimal(str(amount)).quantize(Decimal("0.01"))
        if pay_amt <= 0:
            return Response({"amount": "Сумма оплаты должна быть больше нуля."}, status=status.HTTP_400_BAD_REQUEST)
        if pay_amt > remaining:
            return Response({"amount": f"Сумма оплаты превышает остаток. Максимум: {remaining}."}, status=status.HTTP_400_BAD_REQUEST)

        # audit
        try:
            DealPayment.objects.create(
                company=deal.company,
                branch=deal.branch,
                deal=deal,
                installment=inst,
                kind=DealPayment.Kind.PAY,
                amount=pay_amt,
                paid_date=paid_date,
                idempotency_key=idem,
                created_by=request.user,
                note=note,
            )
        except IntegrityError:
            fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
            return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)

        # update installment
        new_paid = (current_paid + pay_amt).quantize(Decimal("0.01"))
        if new_paid >= total:
            inst.paid_amount = total
            inst.paid_on = paid_date
        else:
            inst.paid_amount = new_paid
            inst.paid_on = None
        inst.save(update_fields=["paid_amount", "paid_on"])

        fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
        return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)


# ===== REFUND (создаём DealPayment refund + уменьшаем paid_amount) =====

class ClientDealRefundAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/deals/<uuid:pk>/refund/
    POST /api/main/clients/<client_id>/deals/<uuid:pk>/refund/

    body:
    {
      "installment_id": "<uuid>" | null,
      "amount": "5000.00" | null,      # если null — вернуть всё по взносу
      "date": "2025-11-10" | null,
      "idempotency_key": "<uuid>",     # ОБЯЗАТЕЛЬНО
      "note": "..." | ""
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        client_id = kwargs.get("client_id")

        deal_qs = self._filter_qs_company_branch(
            ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch())
        ).filter(pk=pk)
        if not _is_owner_like(request.user):
            deal_qs = deal_qs.filter(client__salesperson=request.user)

        if client_id:
            deal_qs = deal_qs.filter(client_id=client_id)

        deal = get_object_or_404(deal_qs)

        if deal.kind != ClientDeal.Kind.DEBT:
            return Response(
                {"detail": "Возврат доступен только для сделок типа 'debt'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inp = DealRefundInputSerializer(data=request.data)
        inp.is_valid(raise_exception=True)
        data = inp.validated_data

        idem = data["idempotency_key"]
        paid_date = data.get("date") or timezone.localdate()
        note = data.get("note", "") or ""
        amount = data.get("amount", None)

        if DealPayment.objects.filter(deal=deal, idempotency_key=idem).exists():
            fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
            return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)

        inst_qs = DealInstallment.objects.select_for_update().filter(deal=deal)

        inst_id = data.get("installment_id")
        if inst_id:
            inst = get_object_or_404(inst_qs, id=inst_id)
        else:
            inst = (
                inst_qs
                .filter(Q(paid_amount__gt=0) | Q(paid_on__isnull=False))
                .order_by("-number")
                .first()
            )
            if not inst:
                return Response({"detail": "Нет оплаченных взносов для возврата."}, status=status.HTTP_400_BAD_REQUEST)

        total = (inst.amount or Decimal("0")).quantize(Decimal("0.01"))
        current_paid = (inst.paid_amount or Decimal("0")).quantize(Decimal("0.01"))

        if current_paid <= 0:
            return Response({"detail": "По этому взносу нечего возвращать."}, status=status.HTTP_400_BAD_REQUEST)

        refund_amt = current_paid if amount is None else Decimal(str(amount)).quantize(Decimal("0.01"))
        if refund_amt <= 0:
            return Response({"amount": "Сумма возврата должна быть больше нуля."}, status=status.HTTP_400_BAD_REQUEST)
        if refund_amt > current_paid:
            return Response({"amount": f"Сумма возврата больше оплаченного. Максимум: {current_paid}."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            DealPayment.objects.create(
                company=deal.company,
                branch=deal.branch,
                deal=deal,
                installment=inst,
                kind=DealPayment.Kind.REFUND,
                amount=refund_amt,
                paid_date=paid_date,
                idempotency_key=idem,
                created_by=request.user,
                note=note,
            )
        except IntegrityError:
            fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
            return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)

        new_paid = (current_paid - refund_amt).quantize(Decimal("0.01"))
        inst.paid_amount = new_paid

        # если не полностью оплачен — paid_on не должен стоять
        if new_paid < total:
            inst.paid_on = None

        inst.save(update_fields=["paid_amount", "paid_on"])

        fresh = ClientDeal.objects.select_related("client").prefetch_related(*_deal_prefetch()).get(pk=deal.pk)
        return Response(ClientDealSerializer(fresh, context={"request": request}).data, status=status.HTTP_200_OK)


# ===== Clients with debts =====

class ClientWithDebtsListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = ClientSerializer

    def get_queryset(self):
        qs = self._filter_qs_company_branch(
            Client.objects.select_related("company", "branch").all()
        )
        qs = _filter_clients_visible_for_user(qs, self.request.user)
        # unpaid = paid_on is null (включая частично оплаченные)
        qs = qs.filter(
            deals__kind=ClientDeal.Kind.DEBT,
            deals__installments__paid_on__isnull=True,
        ).distinct()
        return qs
# ===========================
#  Bids & Social Applications
# ===========================
class BidListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = BidSerializers
    queryset = Bid.objects.select_related("company", "branch", "client").all()


class BidRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BidSerializers
    queryset = Bid.objects.select_related("company", "branch", "client").all()


class SocialApplicationsListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = SocialApplicationsSerializers
    queryset = SocialApplications.objects.select_related("company", "branch", "client").all()


class SocialApplicationsRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SocialApplicationsSerializers
    queryset = SocialApplications.objects.select_related("company", "branch", "client").all()


# ===========================
#  Transaction Records
# ===========================
class TransactionRecordListCreateView(generics.ListCreateAPIView, CompanyBranchRestrictedMixin):
    serializer_class = TransactionRecordSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = TransactionRecordFilter
    search_fields = ["name"]
    ordering_fields = ["date", "amount", "created_at", "id"]
    ordering = ["-date", "-created_at"]

    def get_queryset(self):
        user = self.request.user
        qs = TransactionRecord.objects.select_related("company", "department")
        if user.is_superuser:
            # суперюзер видит всё; но если нужно — можно ограничить по branch
            return qs
        return self._filter_qs_company_branch(qs)

    def perform_create(self, serializer):
        user = self.request.user
        company = _get_company(user)
        department = serializer.validated_data.get("department")

        if not user.is_superuser and not company:
            raise PermissionDenied("Нет прав создавать записи.")

        if company and department and department.company_id != company.id:
            raise PermissionDenied("Отдел принадлежит другой компании.")

        if user.is_superuser and not company and department is None:
            raise PermissionDenied("Укажите отдел, чтобы определить компанию записи.")

        # company/branch подставит миксин; если суперюзер без company — сериализатор подставит из department
        extra = {}
        if company is not None:
            extra["company"] = company
        self._save_with_company_branch(serializer, **extra)


class TransactionRecordRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView, CompanyBranchRestrictedMixin):
    serializer_class = TransactionRecordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = TransactionRecord.objects.select_related("company", "department")
        if user.is_superuser:
            return qs
        return self._filter_qs_company_branch(qs)


# ===========================
#  Contractor Works
# ===========================
class ContractorWorkListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
      GET  /api/main/contractor-works/
      POST /api/main/contractor-works/
    """
    serializer_class = ContractorWorkSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # department убран из фильтров
    filterset_fields = ["contractor_entity_type", "start_date", "end_date"]
    search_fields = [
        "title",
        "contractor_name",
        "contractor_phone",
        "contractor_entity_name",
        "description",
    ]
    ordering_fields = [
        "created_at",
        "updated_at",
        "amount",
        "start_date",
        "end_date",
        "planned_completion_date",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = ContractorWork.objects.select_related().all()
        # только company/branch-ограничение
        return self._filter_qs_company_branch(qs)

    @transaction.atomic
    def perform_create(self, serializer):
        # company/branch подставит миксин
        self._save_with_company_branch(serializer)


class ContractorWorkRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView,
):
    """
    GET    /api/main/contractor-works/<uuid:pk>/
    PATCH  /api/main/contractor-works/<uuid:pk>/
    PUT    /api/main/contractor-works/<uuid:pk>/
    DELETE /api/main/contractor-works/<uuid:pk>/
    """
    serializer_class = ContractorWorkSerializer

    def get_queryset(self):
        qs = ContractorWork.objects.select_related().all()
        return self._filter_qs_company_branch(qs)

    @transaction.atomic
    def perform_update(self, serializer):
        # company/branch подставит миксин
        self._save_with_company_branch(serializer)


# ===========================
#  Debts
# ===========================
class DebtListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/debts/?search=...&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    POST /api/main/debts/
    """
    serializer_class = DebtSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = DebtFilter
    search_fields = ["name", "phone"]
    ordering_fields = ["created_at", "updated_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return self._filter_qs_company_branch(
            Debt.objects.select_related("company", "branch").all()
        )


class DebtRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/main/debts/<uuid:pk>/
    """
    serializer_class = DebtSerializer

    def get_queryset(self):
        return self._filter_qs_company_branch(
            Debt.objects.select_related("company", "branch").all()
        )


class DebtPayAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/debts/<uuid:pk>/pay/
    Body: { "amount": "235.00", "paid_at": "2025-09-12", "note": "оплата с карты" }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        # выбираем долг в рамках company/branch по общей логике
        qs = self._filter_qs_company_branch(Debt.objects.all())
        debt = get_object_or_404(qs, pk=pk)

        ser = DebtPaymentSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        DebtPayment.objects.create(
            company=debt.company,
            debt=debt,
            amount=ser.validated_data["amount"],
            paid_at=ser.validated_data.get("paid_at"),
            note=ser.validated_data.get("note", ""),
        )
        return Response(DebtSerializer(debt, context={"request": request}).data, status=status.HTTP_201_CREATED)


class DebtPaymentListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/debts/<uuid:pk>/payments/?date_from=&date_to=
    """
    serializer_class = DebtPaymentSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = DebtPaymentFilter
    ordering_fields = ["paid_at", "created_at", "amount"]
    ordering = ["-paid_at", "-created_at"]

    def get_queryset(self):
        # платежи конкретного долга в рамках компании/филиала
        debt_qs = self._filter_qs_company_branch(
            Debt.objects.select_related("company", "branch").all()
        )
        debt = get_object_or_404(debt_qs, pk=self.kwargs["pk"])
        return DebtPayment.objects.filter(company=debt.company, debt=debt)


# ===========================
#  Object items / sales
# ===========================
class ObjectItemListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ObjectItemSerializer
    queryset = ObjectItem.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["date", "created_at", "updated_at", "price", "quantity", "name"]
    ordering = ["-date", "-created_at"]


class ObjectItemRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ObjectItemSerializer
    queryset = ObjectItem.objects.all()


class ObjectSaleListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ObjectSaleSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["note", "client__full_name", "client__phone"]
    ordering_fields = ["sold_at", "created_at", "subtotal", "status"]
    ordering = ["-sold_at", "-created_at"]

    def get_queryset(self):
        qs = ObjectSale.objects.select_related("client").prefetch_related("items").all()
        return self._filter_qs_company_branch(qs)

    def perform_create(self, serializer):
        client = serializer.validated_data.get("client")
        if client.company_id != self._company().id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        self._save_with_company_branch(serializer)


class ObjectSaleRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ObjectSaleSerializer

    def get_queryset(self):
        qs = ObjectSale.objects.select_related("client").prefetch_related("items").all()
        return self._filter_qs_company_branch(qs)


class ObjectSaleAddItemAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    POST /api/main/object-sales/<uuid:sale_id>/items/
    Body:
      { "object_item": "<uuid>", "unit_price": "200.00", "quantity": 2 }
    """
    def post(self, request, sale_id):
        sale_qs = self._filter_qs_company_branch(ObjectSale.objects.all())
        sale = get_object_or_404(sale_qs, id=sale_id)

        ser = ObjectSaleItemSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)

        obj_qs = self._filter_qs_company_branch(ObjectItem.objects.all())
        obj = get_object_or_404(obj_qs, id=ser.validated_data["object_item"].id)

        item = ObjectSaleItem.objects.create(
            sale=sale,
            object_item=obj,
            name_snapshot=obj.name,
            unit_price=ser.validated_data.get("unit_price") or obj.price,
            quantity=ser.validated_data["quantity"],
        )
        sale.recalc()
        return Response(ObjectSaleItemSerializer(item).data, status=status.HTTP_201_CREATED)


# ===========================
#  ItemMake
# ===========================
class ItemListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/items/
    POST /api/main/items/
    """
    serializer_class = ItemMakeSerializer
    queryset = ItemMake.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "supplier__full_name", "products__name"]
    filterset_fields = ["unit", "price", "quantity", "products", "supplier"]
    ordering_fields = ["created_at", "updated_at", "price", "quantity", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        return self._filter_qs_company_branch(qs).distinct()

    # perform_create — миксин


class ItemRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ItemMakeSerializer
    queryset = ItemMake.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        return qs


# ===========================
#  Supplier (Client.type=suppliers) -> products -> receipt (оприходование)
# ===========================
class SupplierListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/suppliers/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["full_name", "phone", "llc", "inn"]
    ordering_fields = ["created_at", "full_name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Client.objects.filter(type=Client.StatusClient.SUPPLIERS)
        return self._filter_qs_company_branch(qs)


class SupplierProductsListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/suppliers/<uuid:supplier_id>/products/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductListSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode", "article", "code"]
    ordering_fields = ["created_at", "updated_at", "name", "price", "quantity"]
    ordering = ["-created_at"]

    def get_queryset(self):
        supplier_id = self.kwargs.get("supplier_id")
        sup_qs = self._filter_qs_company_branch(Client.objects.all())
        supplier = get_object_or_404(sup_qs, id=supplier_id, type=Client.StatusClient.SUPPLIERS)

        prod_qs = self._filter_qs_company_branch(Product.objects.all())
        return prod_qs.filter(Q(suppliers=supplier) | Q(client_id=supplier.id)).distinct()


class SupplierReceiptAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    POST /api/main/suppliers/<uuid:supplier_id>/receipt/
    Body:
      {
        "items": [
          {"product": "<uuid>", "qty": 10},
          {"product": "<uuid>", "qty": 3}
        ]
      }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, supplier_id):
        company = self._company()
        branch = self._auto_branch()

        sup_qs = self._filter_qs_company_branch(Client.objects.all())
        supplier = get_object_or_404(sup_qs, id=supplier_id, type=Client.StatusClient.SUPPLIERS)

        ser = SupplierReceiptCreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)

        items = ser.validated_data["items"]
        product_ids = [it["product"].id for it in items]

        prod_qs = self._filter_qs_company_branch(Product.objects.all()).select_for_update()
        products = list(prod_qs.filter(id__in=product_ids))
        by_id = {p.id: p for p in products}

        missing = [str(pid) for pid in product_ids if pid not in by_id]
        if missing:
            raise ValidationError({"items": [f"Товары не найдены/не доступны: {', '.join(missing)}"]})

        # проверим принадлежность поставщику
        wrong_supplier = []
        for p in products:
            ok = False
            try:
                ok = (p.client_id == supplier.id) or p.suppliers.filter(id=supplier.id).exists()
            except Exception:
                ok = (p.client_id == supplier.id)
            if not ok:
                wrong_supplier.append(str(p.id))
        if wrong_supplier:
            raise ValidationError({"items": [f"Товары не принадлежат выбранному поставщику: {', '.join(wrong_supplier)}"]})

        # лог оприходования
        receipt = SupplierReceipt.objects.create(
            company=company,
            branch=branch,
            supplier=supplier,
            created_by=getattr(request, "user", None),
        )

        # обновляем цены (если переданы) и увеличиваем остатки
        receipt_items = []
        for it in items:
            pid = it["product"].id
            qty = int(it["qty"])
            upd = {"quantity": F("quantity") + qty}
            if "purchase_price" in it and it["purchase_price"] is not None:
                upd["purchase_price"] = it["purchase_price"]
            type(by_id[pid]).objects.filter(id=pid).update(**upd)

            receipt_items.append(
                SupplierReceiptItem(
                    receipt=receipt,
                    product=by_id[pid],
                    qty=qty,
                    purchase_price=it.get("purchase_price"),
                )
            )

        if receipt_items:
            SupplierReceiptItem.objects.bulk_create(receipt_items)

        # вернём актуальные данные по товарам
        refreshed = list(self._filter_qs_company_branch(Product.objects.all()).filter(id__in=product_ids))
        return Response(
            {
                "supplier": str(supplier.id),
                "receipt_id": str(receipt.id),
                "products": ProductListSerializer(refreshed, many=True, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )


class SupplierReceiptListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/suppliers/receipts/?supplier_id=<uuid>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierReceiptReadSerializer

    def get_queryset(self):
        qs = SupplierReceipt.objects.select_related("supplier", "company", "branch", "created_by").prefetch_related(
            "items",
            "items__product",
        )
        qs = self._filter_qs_company_branch(qs)

        qp = self.request.query_params
        supplier_id = (qp.get("supplier_id") or "").strip()
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)

        # date filter (created_at)
        df_raw = (qp.get("date_from") or qp.get("created_from") or "").strip()
        dt_raw = (qp.get("date_to") or qp.get("created_to") or "").strip()
        df = parse_date(df_raw) if df_raw else None
        dt = parse_date(dt_raw) if dt_raw else None
        if df:
            qs = qs.filter(created_at__date__gte=df)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

        return qs


# ===========================
#  Subreal / Acceptance / ReturnFromAgent
# ===========================
class ManufactureSubrealListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManufactureSubrealSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["agent", "product", "status", "created_at", "external_ref"]
    # если нужен трек-номер, добавьте "agent__track_number"
    search_fields = ["product__name", "agent__username", "agent__first_name", "agent__last_name"]
    ordering_fields = ["created_at", "qty_transferred", "qty_accepted", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            ManufactureSubreal.objects
            .select_related("company", "user", "agent", "product")
            .all()
        )
        return self._filter_qs_company_branch(qs)

    @transaction.atomic
    def perform_create(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        product = serializer.validated_data.get("product")
        agent = serializer.validated_data.get("agent")
        qty = int(serializer.validated_data.get("qty_transferred") or 0)
        is_sawmill = bool(serializer.validated_data.get("is_sawmill", False))

        # защита от подмены company
        if product and product.company_id != company.id:
            raise serializers.ValidationError({"product": "Товар другой компании."})
        if agent and getattr(agent, "company_id", None) != company.id:
            raise serializers.ValidationError({"agent": "Агент другой компании."})

        locked_qs = None
        if qty:
            if not product:
                raise serializers.ValidationError({"product": "Не выбран товар для списания количества."})
            locked_qs = type(product).objects.select_for_update().filter(pk=product.pk)
            current_qty = locked_qs.values_list("quantity", flat=True).first()
            if current_qty is None or current_qty < qty:
                raise serializers.ValidationError({
                    "qty_transferred": f"Недостаточно на складе: доступно {current_qty or 0}."
                })

        # создаём передачу
        obj = serializer.save(company=company, branch=branch, user=self.request.user)

        # минусуем склад (в той же транзакции)
        if qty and locked_qs is not None:
            locked_qs.update(quantity=F("quantity") - qty)
            prod_model = type(product)
            prod_id = product.pk

            def _send_webhook():
                from apps.main.services.webhooks import send_product_webhook

                try:
                    p = prod_model.objects.get(pk=prod_id)
                    send_product_webhook(p, "product.updated")
                except Exception:
                    logging.getLogger("crm.webhooks").error(
                        "Failed to send product.updated webhook after subreal create. product_id=%s",
                        prod_id,
                        exc_info=True,
                    )

            try:
                transaction.on_commit(_send_webhook)
            except Exception:
                _send_webhook()

        # идемпотентный авто-приём, если is_sawmill=True
        if is_sawmill:
            obj.refresh_from_db(fields=["qty_transferred", "qty_accepted", "status"])
            to_accept = int((obj.qty_transferred or 0) - (obj.qty_accepted or 0))
            if to_accept > 0 and obj.status == ManufactureSubreal.Status.OPEN:
                Acceptance.objects.create(
                    company=company,
                    branch=branch,
                    subreal=obj,
                    accepted_by=self._user(),
                    qty=to_accept,
                    accepted_at=timezone.now(),
                )

        return obj


# ===========================
#  Subreal: retrieve/update/destroy
# ===========================
class ManufactureSubrealRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    """
    GET    /api/main/subreals/<uuid:pk>/
    PATCH  /api/main/subreals/<uuid:pk>/
    PUT    /api/main/subreals/<uuid:pk>/
    DELETE /api/main/subreals/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManufactureSubrealSerializer

    def get_queryset(self):
        qs = (
            ManufactureSubreal.objects
            .select_related("company", "user", "agent", "product")
            .all()
        )
        return self._filter_qs_company_branch(qs)

    def perform_update(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        prod = serializer.validated_data.get("product")
        agent = serializer.validated_data.get("agent")

        if prod and prod.company_id != company.id:
            raise serializers.ValidationError({"product": "Товар другой компании."})
        if agent and getattr(agent, "company_id", None) != company.id:
            raise serializers.ValidationError({"agent": "Агент другой компании."})

        serializer.save(company=company, branch=branch)


# ===========================
#  Acceptance: list/create
# ===========================
class AcceptanceListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["subreal", "accepted_by", "accepted_at"]
    ordering_fields = ["accepted_at", "qty", "id"]
    ordering = ["-accepted_at"]

    def get_queryset(self):
        qs = (
            Acceptance.objects
            .select_related(
                "company",
                "subreal",
                "accepted_by",
                "subreal__agent",
                "subreal__product",
            )
            .all()
        )
        return self._filter_qs_company_branch(qs)

    def get_serializer_class(self):
        return (
            AcceptanceCreateSerializer
            if self.request.method == "POST"
            else AcceptanceReadSerializer
        )

    @transaction.atomic
    def perform_create(self, serializer):
        sub = serializer.validated_data["subreal"]
        locked = (
            ManufactureSubreal.objects
            .select_for_update()
            .get(pk=sub.pk)
        )

        if locked.status != ManufactureSubreal.Status.OPEN:
            raise serializers.ValidationError({"subreal": "Передача уже закрыта."})

        qty = serializer.validated_data["qty"]
        if qty > locked.qty_remaining:
            raise serializers.ValidationError({
                "qty": f"Можно принять максимум {locked.qty_remaining}."
            })

        serializer.save(company=self._company(), accepted_by=self._user())


# ===========================
#  Acceptance: retrieve/destroy
# ===========================
class AcceptanceRetrieveDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveDestroyAPIView):
    """
    GET    /api/main/acceptances/<uuid:pk>/
    DELETE /api/main/acceptances/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AcceptanceReadSerializer

    def get_queryset(self):
        qs = (
            Acceptance.objects
            .select_related(
                "company",
                "subreal",
                "accepted_by",
                "subreal__agent",
                "subreal__product",
            )
            .all()
        )
        return self._filter_qs_company_branch(qs)


# ===========================
#  Return: list/create
# ===========================
class ReturnFromAgentListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/returns/
    POST /api/main/returns/
    """
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["subreal", "returned_by", "returned_at", "status"]
    ordering_fields = ["returned_at", "qty", "id"]
    ordering = ["-returned_at"]

    def get_queryset(self):
        qs = (
            ReturnFromAgent.objects
            .select_related(
                "company",
                "subreal",
                "returned_by",
                "accepted_by",
                "subreal__agent",
                "subreal__product",
            )
            .all()
        )
        return self._filter_qs_company_branch(qs)

    def get_serializer_class(self):
        return ReturnCreateSerializer if self.request.method == "POST" else ReturnReadSerializer

    def create(self, request, *args, **kwargs):
        """
        POST /api/main/returns/
        Может создать несколько ReturnFromAgent, если qty покрывается несколькими subreal.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = serializer.save(company=self._company(), returned_by=self._user())

        if isinstance(created, list):
            out_ser = ReturnReadSerializer(created, many=True, context={"request": request})
            return Response(out_ser.data, status=status.HTTP_201_CREATED)

        out_ser = ReturnReadSerializer(created, context={"request": request})
        return Response(out_ser.data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(company=self._company(), returned_by=self._user())

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # Сводка по тем же фильтрам, что и список (для агента: ?returned_by=me → только его возвраты)
        qs = self.filter_queryset(self.get_queryset())
        pending = qs.filter(status=ReturnFromAgent.Status.PENDING).aggregate(
            pending_count=Count("id"),
            pending_qty=Coalesce(Sum("qty"), 0),
        )
        summary = {
            "pending_count": pending["pending_count"] or 0,
            "pending_qty": int(pending["pending_qty"] or 0),
        }
        if isinstance(response.data, dict):
            response.data["returns_summary"] = summary
        return response


# ===========================
#  Agent: мои возвраты (только свои, со сводкой)
# ===========================
class AgentMyReturnsListCreateAPIView(ReturnFromAgentListCreateAPIView):
    """
    GET  /api/main/agents/me/returns/
    POST /api/main/agents/me/returns/
    Список и создание возвратов только текущего агента. В ответе GET — returns_summary
    (pending_count, pending_qty) по возвратам агента.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(returned_by=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)

        # Доп.данные для UI возвратов: "на руках" по товарам (суммарно по всем партиям subreal).
        # Нужно, когда один и тот же товар был выдан в нескольких передачах: 30 + 40 -> показываем 70.
        try:
            company = self._company()
            branch = self._auto_branch()
            user = request.user

            subreals_qs = ManufactureSubreal.objects.filter(company=company, agent=user)
            if branch is not None:
                subreals_qs = subreals_qs.filter(branch=branch)
            else:
                subreals_qs = subreals_qs.filter(branch__isnull=True)

            base_rows = list(
                subreals_qs.values("product_id", "product__name")
                .annotate(
                    accepted=Coalesce(Sum("qty_accepted"), V(0)),
                    returned=Coalesce(Sum("qty_returned"), V(0)),
                )
            )

            sold_rows = list(
                AgentSaleAllocation.objects.filter(
                    company=company,
                    agent=user,
                    sale__status__in=[Sale.Status.PAID, Sale.Status.DEBT],
                )
                .values("product_id")
                .annotate(sold=Coalesce(Sum("qty"), V(0)))
            )
            sold_by_product = {r["product_id"]: int(r.get("sold") or 0) for r in sold_rows}

            pending_rows = list(
                ReturnFromAgent.objects.filter(
                    company=company,
                    status=ReturnFromAgent.Status.PENDING,
                    subreal__agent=user,
                )
                .values("subreal__product_id")
                .annotate(reserved=Coalesce(Sum("qty"), V(0)))
            )
            pending_by_product = {r["subreal__product_id"]: int(r.get("reserved") or 0) for r in pending_rows}

            on_hand = []
            for r in base_rows:
                pid = r.get("product_id")
                accepted = int(r.get("accepted") or 0)
                returned = int(r.get("returned") or 0)
                sold = int(sold_by_product.get(pid, 0) or 0)
                reserved = int(pending_by_product.get(pid, 0) or 0)
                qty_on_hand = max(accepted - returned - sold - reserved, 0)
                if qty_on_hand <= 0:
                    continue
                on_hand.append({
                    "product_id": str(pid) if pid else None,
                    "product_name": r.get("product__name") or "",
                    "qty_on_hand": qty_on_hand,
                })
            on_hand.sort(key=lambda x: x["qty_on_hand"], reverse=True)

            if isinstance(response.data, dict):
                response.data["on_hand_by_product"] = on_hand
        except Exception:
            # не ломаем endpoint из-за доп.раздела
            pass

        return response


# ===========================
#  Return: retrieve/destroy
# ===========================
class ReturnFromAgentRetrieveDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveDestroyAPIView
):
    """
    GET    /api/main/returns/<uuid:pk>/
    DELETE /api/main/returns/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReturnReadSerializer

    def get_queryset(self):
        qs = (
            ReturnFromAgent.objects
            .select_related(
                "company",
                "subreal",
                "returned_by",
                "accepted_by",
                "subreal__agent",
                "subreal__product",
            )
            .all()
        )
        return self._filter_qs_company_branch(qs)


# ===========================
#  Return: approve (идемпотентно)
# ===========================
class ReturnFromAgentApproveAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/returns/<uuid:pk>/approve/
    Подтверждение возврата (статус -> accepted, движение на склад).
    """
    permission_classes = [permissions.IsAuthenticated]

    def _relocate_return_subreal_if_needed(self, ret: ReturnFromAgent):
        """
        Если по текущей партии (subreal) уже нет остатка, пробуем найти другую партию
        того же товара у того же агента, где остатка хватает.
        """
        company_id = ret.company_id
        branch_id = ret.branch_id
        product_id = getattr(getattr(ret, "subreal", None), "product_id", None)
        agent_id = getattr(getattr(ret, "subreal", None), "agent_id", None)
        if not (company_id and product_id and agent_id):
            return

        current_subreal_id = ret.subreal_id
        try:
            current = ManufactureSubreal.objects.get(pk=current_subreal_id)
            on_hand_now = int(current.get_qty_on_hand_with_sales(company_id=company_id, exclude_pending_return_id=ret.pk) or 0)
        except Exception:
            on_hand_now = 0

        if on_hand_now >= int(ret.qty or 0):
            return

        candidates = ManufactureSubreal.objects.filter(
            company_id=company_id,
            agent_id=agent_id,
            product_id=product_id,
        )
        if branch_id is not None:
            candidates = candidates.filter(branch_id=branch_id)
        else:
            candidates = candidates.filter(branch__isnull=True)
        candidates = candidates.order_by("-created_at", "-id")

        need = int(ret.qty or 0)
        total = 0
        picked = None
        for s in candidates:
            on_hand = int(s.get_qty_on_hand_with_sales(company_id=company_id, exclude_pending_return_id=ret.pk) or 0)
            if on_hand <= 0:
                continue
            total += on_hand
            if picked is None and on_hand >= need:
                picked = s

        if picked is None:
            raise ValidationError({"qty": [f"Можно принять максимум {total}."]})

        if picked.pk != current_subreal_id:
            ret.subreal_id = picked.pk
            ret.save(update_fields=["subreal"])

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        try:
            ret = (
                ReturnFromAgent.objects
                .select_for_update()
                .select_related("subreal__product")
                .get(pk=pk, company_id=self._company().id)
            )
        except ReturnFromAgent.DoesNotExist:
            return Response({"detail": "Возврат не найден."}, status=status.HTTP_404_NOT_FOUND)

        # идемпотентность: если уже не pending — просто вернуть текущее состояние
        if ret.status != ReturnFromAgent.Status.PENDING:
            return Response(ReturnReadSerializer(ret).data, status=status.HTTP_200_OK)

        # Если партия, привязанная к возврату, уже "обнулилась", пробуем перепривязать к другой партии
        # того же товара у агента (остаток считаем в целом).
        self._relocate_return_subreal_if_needed(ret)

        ser = ReturnApproveSerializer(
            data=request.data,
            context={"request": request, "return_obj": ret},
        )
        ser.is_valid(raise_exception=True)
        ret = ser.save()
        return Response(ReturnReadSerializer(ret).data, status=status.HTTP_200_OK)


# ===========================
#  Return: bulk approve (owner/admin)
# ===========================
class ReturnFromAgentBulkApproveAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/returns/approve-bulk/
    Принимает сразу несколько возвратов одним запросом.

    Payload:
      - либо {"ids": ["uuid", ...]}
      - либо {"product_id": "<uuid>", "agent_id": "<uuid|optional>"} -> примет все PENDING по товару (и агенту, если задан)
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not _is_owner_like(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        company = self._company()
        data = request.data or {}
        ids = data.get("ids")
        product_id = data.get("product_id")
        agent_id = data.get("agent_id")

        if ids:
            qs = ReturnFromAgent.objects.select_for_update().filter(company=company, status=ReturnFromAgent.Status.PENDING, id__in=ids)
        elif product_id:
            qs = ReturnFromAgent.objects.select_for_update().filter(
                company=company,
                status=ReturnFromAgent.Status.PENDING,
                subreal__product_id=product_id,
            )
            if agent_id:
                qs = qs.filter(subreal__agent_id=agent_id)
        else:
            raise ValidationError({"detail": "Передай ids[] или product_id."})

        qs = qs.select_related("subreal__product")

        approved = []
        errors = []
        helper = ReturnFromAgentApproveAPIView()
        helper.request = request
        helper.args = ()
        helper.kwargs = {}

        for ret in qs.order_by("returned_at", "id"):
            try:
                helper._relocate_return_subreal_if_needed(ret)
                ser = ReturnApproveSerializer(data={}, context={"request": request, "return_obj": ret})
                ser.is_valid(raise_exception=True)
                approved_ret = ser.save()
                approved.append(approved_ret)
            except Exception as e:
                errors.append({"id": str(ret.id), "error": str(e)})

        return Response({
            "approved_count": len(approved),
            "errors_count": len(errors),
            "errors": errors,
            "items": ReturnReadSerializer(approved, many=True).data,
        }, status=status.HTTP_200_OK)


# ===========================
#  Return: reject (идемпотентно)
# ===========================
class ReturnFromAgentRejectAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    POST /api/main/returns/<uuid:pk>/reject/
    Отклонение возврата (статус -> rejected).
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        try:
            ret = (
                ReturnFromAgent.objects
                .select_for_update()
                .select_related("subreal__product")
                .get(pk=pk, company_id=self._company().id)
            )
        except ReturnFromAgent.DoesNotExist:
            return Response({"detail": "Возврат не найден."}, status=status.HTTP_404_NOT_FOUND)

        # идемпотентность: если уже не pending — просто вернуть текущее состояние
        if ret.status != ReturnFromAgent.Status.PENDING:
            return Response(ReturnReadSerializer(ret).data, status=status.HTTP_200_OK)

        ser = ReturnRejectSerializer(
            data=request.data,
            context={"request": request, "return_obj": ret},
        )
        ser.is_valid(raise_exception=True)
        ret = ser.save()
        return Response(ReturnReadSerializer(ret).data, status=status.HTTP_200_OK)


# ===========================
#  Subreal: bulk create
# ===========================
class ManufactureSubrealBulkCreateAPIView(APIView, CompanyBranchRestrictedMixin):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        ser = BulkSubrealCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        ser.is_valid(raise_exception=True)

        agent = ser.validated_data["agent"]
        items = ser.validated_data["items"]
        user = self._user()
        company = self._company()
        branch = self._auto_branch()
        transfer_ref = str(uuid4())

        created_objs = []

        for idx, item in enumerate(items):
            product = item["product"]
            qty = int(item["qty_transferred"])
            is_sawmill = bool(item.get("is_sawmill", False))

            locked_qs = type(product).objects.select_for_update().filter(pk=product.pk)
            current_qty = locked_qs.values_list("quantity", flat=True).first()
            if current_qty is None or current_qty < qty:
                raise serializers.ValidationError({
                    "items": f"Недостаточно на складе для {product.name}: доступно {current_qty or 0}."
                })

            # списываем со склада
            locked_qs.update(quantity=F("quantity") - qty)
            prod_model = type(product)
            prod_id = product.pk

            def _send_webhook():
                from apps.main.services.webhooks import send_product_webhook

                try:
                    p = prod_model.objects.get(pk=prod_id)
                    send_product_webhook(p, "product.updated")
                except Exception:
                    logging.getLogger("crm.webhooks").error(
                        "Failed to send product.updated webhook after subreal bulk create. product_id=%s",
                        prod_id,
                        exc_info=True,
                    )

            try:
                transaction.on_commit(_send_webhook)
            except Exception:
                _send_webhook()

            # Уникальный external_ref на строку: в БД uniq (company, external_ref) при non-null ref.
            sub = ManufactureSubreal.objects.create(
                company=company,
                branch=branch,
                user=user,
                agent=agent,
                product=product,
                external_ref=f"{transfer_ref}:{idx}",
                qty_transferred=qty,
                is_sawmill=is_sawmill,
            )
            created_objs.append(sub)

            # авто-принятие для распила / пилорамы
            if is_sawmill:
                sub.refresh_from_db(fields=["qty_transferred", "qty_accepted", "status"])
                to_accept = int((sub.qty_transferred or 0) - (sub.qty_accepted or 0))
                if to_accept > 0 and sub.status == ManufactureSubreal.Status.OPEN:
                    Acceptance.objects.create(
                        company=company,
                        branch=branch,
                        subreal=sub,
                        accepted_by=user,
                        qty=to_accept,
                        accepted_at=timezone.now(),
                    )

        out = ManufactureSubrealSerializer(
            created_objs,
            many=True,
            context={"request": request},
        ).data
        return Response(out, status=status.HTTP_201_CREATED)


# ===========================
#  Agent: my products (GET/PATCH)
# ===========================
class AgentMyProductsListAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    GET  /api/main/agents/me/products/
    PATCH /api/main/agents/me/products/
      — частичное обновление qty_accepted/qty_returned
    """
    permission_classes = [permissions.IsAuthenticated]

    # -------- Helpers --------
    def _build_queryset(self, request):
        accepted_returns_qs = ReturnFromAgent.objects.filter(
            status=ReturnFromAgent.Status.ACCEPTED
        )
        alloc_qs = AgentSaleAllocation.objects.only("id", "subreal_id", "qty")

        base = (
            ManufactureSubreal.objects
            .filter(agent_id=request.user.id)
            .select_related("product")
            .prefetch_related(
                "acceptances",
                Prefetch(
                    "returns",
                    queryset=accepted_returns_qs,
                    to_attr="accepted_returns",
                ),
                Prefetch(
                    "sale_allocations",
                    queryset=alloc_qs,
                    to_attr="prefetched_allocs",
                ),
            )
            .annotate(sold_qty=Coalesce(Sum("sale_allocations__qty"), V(0)))
            .order_by("product_id", "-created_at")
        )
        base = self._filter_qs_company_branch(base)

        term = (request.query_params.get("search") or "").strip()
        if term:
            q = (
                Q(product__name__icontains=term)
                | Q(product__barcode__icontains=term)
                | Q(product__article__icontains=term)
                | Q(product__code__icontains=term)
            )
            if term.isdigit():
                q |= Q(product__plu=int(term))
            base = base.filter(q)

        return base

    @staticmethod
    def _nz(v: Optional[int]) -> int:
        return int(v or 0)

    def _serialize_products(self, qs) -> List[Dict[str, Any]]:
        def _sold_for_sub(s):
            ann = self._nz(getattr(s, "sold_qty", 0))
            if ann:
                return ann
            return sum(self._nz(a.qty) for a in getattr(s, "prefetched_allocs", []))

        data = []

        for product_id, subreals_iter in groupby(qs, key=attrgetter("product_id")):
            subreals = list(subreals_iter)

            qty_on_hand = 0
            for s in subreals:
                accepted = self._nz(s.qty_accepted)
                returned = self._nz(s.qty_returned)
                sold = _sold_for_sub(s)
                qty_on_hand += max(accepted - returned - sold, 0)

            if qty_on_hand <= 0:
                continue

            movement_dates: List[datetime] = []
            for s in subreals:
                if s.created_at:
                    movement_dates.append(s.created_at)
                for acc in s.acceptances.all():
                    if getattr(acc, "accepted_at", None):
                        movement_dates.append(acc.accepted_at)
                for ret in getattr(s, "accepted_returns", []):
                    if getattr(ret, "accepted_at", None):
                        movement_dates.append(ret.accepted_at)

            last_movement_at = max(movement_dates) if movement_dates else None

            subreals_payload = []
            for s in subreals:
                accepted = self._nz(s.qty_accepted)
                returned = self._nz(s.qty_returned)
                sold = _sold_for_sub(s)
                subreals_payload.append({
                    "id": s.id,
                    "created_at": s.created_at,
                    "qty_transferred": self._nz(s.qty_transferred),
                    "qty_accepted": accepted,
                    "qty_returned": returned,
                    "qty_sold": sold,
                    "qty_on_hand": max(accepted - returned - sold, 0),
                })

            data.append({
                "product": product_id,
                "product_name": (
                    subreals[0].product.name
                    if (subreals and getattr(subreals[0], "product", None))
                    else ""
                ),
                "qty_on_hand": qty_on_hand,
                "last_movement_at": last_movement_at,
                "subreals": subreals_payload,
            })

        return data

    # -------- GET --------
    def get(self, request, *args, **kwargs):
        company_id = getattr(request.user, "company_id", None)
        if not company_id:
            return Response([], status=status.HTTP_200_OK)

        qs = self._build_queryset(request)
        data = self._serialize_products(qs)
        return Response(
            AgentProductOnHandSerializer(data, many=True).data,
            status=status.HTTP_200_OK,
        )

    # -------- PATCH --------
    def patch(self, request, *args, **kwargs):
        """
        Частичное обновление qty_accepted / qty_returned по конкретным передачам.
        Предпочтительно создавать Acceptance/Return события, а не трогать счётчики,
        но PATCH оставляем как "ручной корректор".
        """
        company_id = getattr(request.user, "company_id", None)
        if not company_id:
            return Response(
                {"detail": "No company bound."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data or {}
        if not isinstance(payload, dict) or "subreals" not in payload:
            raise ValidationError({"subreals": "Required list of updates."})

        items = payload["subreals"]
        if not isinstance(items, list) or not items:
            raise ValidationError({"subreals": "Must be a non-empty list."})

        ids: List[str] = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                raise ValidationError({f"subreals[{i}]": "Must be an object."})
            if "id" not in it:
                raise ValidationError({f"subreals[{i}].id": "Required."})

            # validate UUID
            try:
                ids.append(str(UUID(str(it["id"]))))
            except Exception:
                raise ValidationError({f"subreals[{i}].id": "Must be UUID string."})

            allowed_keys = {"qty_accepted", "qty_returned"}
            unknown = set(it.keys()) - ({"id"} | allowed_keys)
            if unknown:
                raise ValidationError({
                    f"subreals[{i}]": f"Unknown fields: {', '.join(sorted(unknown))}"
                })

            for f in allowed_keys & set(it.keys()):
                v = it[f]
                if v is None:
                    continue
                if not isinstance(v, int):
                    raise ValidationError({f"subreals[{i}].{f}": "Must be integer."})
                if v < 0:
                    raise ValidationError({f"subreals[{i}].{f}": "Must be >= 0."})

        base_qs = ManufactureSubreal.objects.filter(
            id__in=ids,
            agent_id=request.user.id,
        )
        base_qs = self._filter_qs_company_branch(base_qs)

        subreal_map = {
            str(s.id): s
            for s in base_qs.select_for_update()
        }
        missing = [sid for sid in ids if sid not in subreal_map]
        if missing:
            raise ValidationError({
                "subreals": f"Not found or not allowed: {missing}"
            })

        to_update = []
        with transaction.atomic():
            for it in items:
                sid = str(UUID(str(it["id"])))
                s = subreal_map[sid]

                new_accepted = s.qty_accepted or 0
                new_returned = s.qty_returned or 0
                transferred = s.qty_transferred or 0

                if "qty_accepted" in it and it["qty_accepted"] is not None:
                    new_accepted = it["qty_accepted"]
                if "qty_returned" in it and it["qty_returned"] is not None:
                    new_returned = it["qty_returned"]

                if new_accepted > transferred:
                    raise ValidationError({
                        f"id={s.id}": (
                            f"qty_accepted ({new_accepted}) "
                            f"must be <= qty_transferred ({transferred})"
                        )
                    })
                if new_returned > new_accepted:
                    raise ValidationError({
                        f"id={s.id}": (
                            f"qty_returned ({new_returned}) "
                            f"must be <= qty_accepted ({new_accepted})"
                        )
                    })

                changed = False
                if new_accepted != (s.qty_accepted or 0):
                    s.qty_accepted = new_accepted
                    changed = True
                if new_returned != (s.qty_returned or 0):
                    s.qty_returned = new_returned
                    changed = True
                if changed:
                    to_update.append(s)

            if to_update:
                ManufactureSubreal.objects.bulk_update(
                    to_update,
                    ["qty_accepted", "qty_returned"],
                )

        qs = self._build_queryset(request)
        data = self._serialize_products(qs)
        return Response(
            AgentProductOnHandSerializer(data, many=True).data,
            status=status.HTTP_200_OK,
        )


# ===========================
#  Owner: agents products
# ===========================
class OwnerAgentsProductsListAPIView(APIView, CompanyBranchRestrictedMixin):
    """
    GET /api/main/owner/agents/products/

    Возвращает по КАЖДОМУ агенту список его товаров "на руках"
    (как /agents/me/products), плюс данные агента.
    """
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _nz(v: Optional[int]) -> int:
        return int(v or 0)

    def _build_queryset(self, request):
        accepted_returns_qs = ReturnFromAgent.objects.filter(
            status=ReturnFromAgent.Status.ACCEPTED
        )
        alloc_qs = AgentSaleAllocation.objects.only(
            "id", "subreal_id", "qty"
        )

        base = (
            ManufactureSubreal.objects
            .select_related("product", "agent")
            .prefetch_related(
                "acceptances",
                Prefetch(
                    "returns",
                    queryset=accepted_returns_qs,
                    to_attr="accepted_returns",
                ),
                Prefetch(
                    "sale_allocations",
                    queryset=alloc_qs,
                    to_attr="prefetched_allocs",
                ),
            )
            .annotate(sold_qty=Coalesce(Sum("sale_allocations__qty"), V(0)))
            .order_by("agent_id", "product_id", "-created_at")
        )
        base = self._filter_qs_company_branch(base)

        # date filters (optional): ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
        q = getattr(request, "query_params", getattr(request, "GET", {}))
        date_from_raw = (q.get("date_from") or "").strip()
        date_to_raw = (q.get("date_to") or "").strip()
        if date_from_raw or date_to_raw:
            try:
                df = _date.fromisoformat(date_from_raw) if date_from_raw else None
            except Exception:
                df = None
            try:
                dt = _date.fromisoformat(date_to_raw) if date_to_raw else None
            except Exception:
                dt = None

            if df or dt:
                today = timezone.localdate()
                df = df or dt or today
                dt = dt or df
                if df > dt:
                    df, dt = dt, df

                dt_from = timezone.make_aware(datetime.combine(df, datetime.min.time()))
                dt_to = timezone.make_aware(datetime.combine(dt, datetime.max.time()))
                base = base.filter(created_at__range=(dt_from, dt_to))

        term = (request.query_params.get("search") or "").strip()
        if term:
            q = (
                Q(product__name__icontains=term)
                | Q(product__barcode__icontains=term)
                | Q(product__article__icontains=term)
                | Q(product__code__icontains=term)
            )
            if term.isdigit():
                q |= Q(product__plu=int(term))
            base = base.filter(q)

        return base

    def _serialize_products_for_agent(self, subreals_qs) -> List[Dict[str, Any]]:
        def _sold_for_sub(s):
            ann = self._nz(getattr(s, "sold_qty", 0))
            if ann:
                return ann
            return sum(
                self._nz(a.qty)
                for a in getattr(s, "prefetched_allocs", [])
            )

        data: List[Dict[str, Any]] = []

        for product_id, subreals_iter in groupby(
            subreals_qs,
            key=attrgetter("product_id"),
        ):
            subreals = list(subreals_iter)

            qty_on_hand = 0
            for s in subreals:
                accepted = self._nz(s.qty_accepted)
                returned = self._nz(s.qty_returned)
                sold = _sold_for_sub(s)
                qty_on_hand += max(accepted - returned - sold, 0)

            if qty_on_hand <= 0:
                continue

            movement_dates: List[datetime] = []
            for s in subreals:
                if s.created_at:
                    movement_dates.append(s.created_at)
                for acc in s.acceptances.all():
                    if getattr(acc, "accepted_at", None):
                        movement_dates.append(acc.accepted_at)
                for ret in getattr(s, "accepted_returns", []):
                    if getattr(ret, "accepted_at", None):
                        movement_dates.append(ret.accepted_at)

            last_movement_at = max(movement_dates) if movement_dates else None

            subreals_payload = []
            for s in subreals:
                accepted = self._nz(s.qty_accepted)
                returned = self._nz(s.qty_returned)
                sold = _sold_for_sub(s)
                subreals_payload.append({
                    "id": s.id,
                    "created_at": s.created_at,
                    "qty_transferred": self._nz(s.qty_transferred),
                    "qty_accepted": accepted,
                    "qty_returned": returned,
                    "qty_sold": sold,
                    "qty_on_hand": max(accepted - returned - sold, 0),
                })

            data.append({
                "product": product_id,
                "product_name": (
                    subreals[0].product.name
                    if (subreals and getattr(subreals[0], "product", None))
                    else ""
                ),
                "qty_on_hand": qty_on_hand,
                "last_movement_at": last_movement_at,
                "subreals": subreals_payload,
            })

        return data

    def get(self, request, *args, **kwargs):
        # если надо, можно тут навесить owner-only check
        qs = self._build_queryset(request)

        out: List[Dict[str, Any]] = []
        for agent_id, agent_subreals_iter in groupby(
            qs,
            key=attrgetter("agent_id"),
        ):
            agent_subreals = list(agent_subreals_iter)
            if not agent_subreals:
                continue

            agent = agent_subreals[0].agent  # select_related("agent")

            products_payload = self._serialize_products_for_agent(agent_subreals)
            if not products_payload:
                continue

            out.append({
                "agent": {
                    "id": agent.id,
                    "first_name": getattr(agent, "first_name", "") or "",
                    "last_name": getattr(agent, "last_name", "") or "",
                    "track_number": getattr(agent, "track_number", None),
                },
                "products": products_payload,
            })

        return Response(
            AgentWithProductsSerializer(out, many=True).data,
            status=status.HTTP_200_OK,
        )


# ===========================
#  Product images
# ===========================
class ProductImageListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/products/<uuid:product_id>/images/
    POST /api/main/products/<uuid:product_id>/images/
      form-data:
        image=<file>,
        alt="...",
        is_primary=true|false
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductImageSerializer

    def _base_qs(self):
        company = self._company()
        branch = self._auto_branch()

        qs = ProductImage.objects.select_related("product")

        if company is not None:
            qs = qs.filter(product__company=company)

        # новая логика: если branch задан → только этот филиал,
        # если branch None → не фильтруем по branch (вся компания)
        if branch is not None:
            qs = qs.filter(product__branch=branch)

        return qs

    def get_queryset(self):
        product_id = self.kwargs["product_id"]
        return self._base_qs().filter(product_id=product_id)

    @transaction.atomic
    def perform_create(self, serializer):
        # проверим, что продукт вообще доступен текущему пользователю
        pid = self.kwargs["product_id"]
        allowed = Product.objects.filter(id=pid)
        company = self._company()
        branch = self._auto_branch()

        if company is not None:
            allowed = allowed.filter(company=company)

        if branch is not None:
            allowed = allowed.filter(branch=branch)
        # branch is None → без ограничений по филиалу (вся компания)

        product = get_object_or_404(allowed)

        obj = serializer.save(product=product)


class ProductImageRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    """
    PATCH /api/main/products/<uuid:product_id>/images/<uuid:image_id>/
      { "alt": "...", "is_primary": true/false }
    DELETE /api/main/products/<uuid:product_id>/images/<uuid:image_id>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductImageSerializer
    lookup_url_kwarg = "image_id"

    def _base_qs(self):
        company = self._company()
        branch = self._auto_branch()
        qs = ProductImage.objects.select_related("product")

        if company is not None:
            qs = qs.filter(product__company=company)

        if branch is not None:
            qs = qs.filter(product__branch=branch)
        # branch None → все филиалы компании

        return qs

    def get_queryset(self):
        product_id = self.kwargs["product_id"]
        return self._base_qs().filter(product_id=product_id)

    @transaction.atomic
    def perform_update(self, serializer):
        obj = serializer.save()


# ===========================
#  Agent carts (корзины агента)
# ===========================
class AgentRequestCartListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AgentRequestCartSerializer

    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "client"]
    ordering_fields = ["created_at", "updated_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        items_qs = (
            AgentRequestItem.objects
            .select_related("product")                    # продукт одной пачкой
            .prefetch_related("product__images")          # и все фотки товара
        )

        qs = (
            AgentRequestCart.objects
            .select_related(
                "company",
                "branch",
                "agent",
                "client",
                "approved_by",
            )
            .prefetch_related(
                Prefetch("items", queryset=items_qs)
            )
        )

        qs = self._filter_qs_company_branch(qs)

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)

        return qs

    def perform_create(self, serializer):
        """
        Сам сериализатор должен:
        - подставить agent = request.user
        - проставить company/branch от пользователя
        """
        serializer.save()


class AgentRequestCartRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    """
    GET    /agent-carts/<uuid:pk>/
    PATCH  /agent-carts/<uuid:pk>/   (агент может менять client/note пока статус draft)
    DELETE /agent-carts/<uuid:pk>/   (удалить черновик)
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AgentRequestCartSerializer

    def get_queryset(self):
        items_qs = (
            AgentRequestItem.objects
            .select_related("product")                    # продукт одной пачкой
            .prefetch_related("product__images")          # и все фотки товара
        )

        qs = (
            AgentRequestCart.objects
            .select_related(
                "company",
                "branch",
                "agent",
                "client",
                "approved_by",
            )
            .prefetch_related(
                Prefetch("items", queryset=items_qs)
            )
        )

        qs = self._filter_qs_company_branch(qs)

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)

        return qs

    def perform_update(self, serializer):
        """
        В сериализаторе:
          - запрещаем апдейт, если статус != DRAFT
          - разрешаем менять только client / note
        """
        serializer.save()

    def perform_destroy(self, instance):
        """
        Удаляем корзину только если она DRAFT.
        Агент может удалять только свою корзину.
        Владелец может удалить любой черновик.
        """
        if instance.status != AgentRequestCart.Status.DRAFT:
            raise ValidationError("Удалять можно только корзину в статусе DRAFT.")

        user = self.request.user
        if not _is_owner_like(user) and instance.agent_id != user.id:
            raise ValidationError("Нельзя удалить чужую корзину.")

        instance.delete()


class AgentRequestCartSubmitAPIView(AgentCartLockMixin,
                                    CompanyBranchRestrictedMixin,
                                    APIView):
    """
    POST /agent-carts/<uuid:pk>/submit/
    Агент (или владелец вместо него) отправляет корзину.
    После этого корзина перестаёт редактироваться.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        # 1) лочим корзину безопасно (двухшагово, без FOR UPDATE с LEFT JOIN)
        cart = self._lock_cart_for_submit(request, pk)

        # 2) выполняем submit() через сериализатор
        ser = AgentRequestCartSubmitSerializer(
            data=request.data,
            context={"cart_obj": cart, "request": request},
        )
        ser.is_valid(raise_exception=True)
        cart = ser.save()  # cart.submit()

        # 3) возвращаем свежие данные
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out, status=status.HTTP_200_OK)


class AgentRequestCartApproveAPIView(AgentCartLockMixin,
                                     CompanyBranchRestrictedMixin,
                                     APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        # только владелец/админ
        if not _is_owner_like(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        # аккуратно лочим корзину (без join'ов)
        cart = self._lock_cart_for_owner_action(request, pk)

        ser = AgentRequestCartApproveSerializer(
            data=request.data,
            context={"cart_obj": cart, "request": request},
        )
        ser.is_valid(raise_exception=True)
        cart = ser.save()  # внутри вызывает cart.approve(by_user=request.user)

        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out, status=status.HTTP_200_OK)


class AgentRequestCartRejectAPIView(AgentCartLockMixin,
                                    CompanyBranchRestrictedMixin,
                                    APIView):
    """
    POST /agent-carts/<uuid:pk>/reject/
    Только владелец/админ.
    cart.reject(by_user=request.user):
      - статус -> REJECTED
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        # 1) только владелец / админ
        if not _is_owner_like(request.user):
            raise PermissionDenied("Forbidden")

        # 2) лочим корзину
        cart = self._lock_cart_for_owner_action(request, pk)

        # 3) выполняем reject()
        ser = AgentRequestCartRejectSerializer(
            data=request.data,
            context={"cart_obj": cart, "request": request},
        )
        ser.is_valid(raise_exception=True)
        cart = ser.save()

        # 4) назад корзину в сериализованном виде
        out = AgentRequestCartSerializer(cart, context={"request": request}).data
        return Response(out, status=status.HTTP_200_OK)


# ===========================
#  Позиции корзины агента
# ===========================
class AgentRequestItemListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /agent-cart-items/?cart=<uuid>
    POST /agent-cart-items/
      { "cart": "<uuid>", "product": "<uuid>", "quantity_requested": 5 }

    - агент видит только свои черновики
    - владелец видит всё в своей компании/филиале
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AgentRequestItemSerializer

    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["cart", "product"]
    ordering_fields = ["created_at", "updated_at", "quantity_requested"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            AgentRequestItem.objects
            .select_related(
                "cart",
                "cart__agent",
                "cart__company",
                "cart__branch",
                "product",
                "subreal",
            )
            .all()
        )
        qs = self._filter_qs_company_branch(
            qs,
            company_field="cart__company",
            branch_field="cart__branch",
        )

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)

        return qs

    def perform_create(self, serializer):
        """
        AgentRequestItemSerializer.create() должен проверять:
        - cart.status == DRAFT
        - cart принадлежит агенту (если юзер не владелец)
        - product принадлежит той же company/branch
        """
        serializer.save()


class AgentRequestItemRetrieveUpdateDestroyAPIView(
    CompanyBranchRestrictedMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    """
    PATCH  /agent-cart-items/<uuid:pk>/
    DELETE /agent-cart-items/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AgentRequestItemSerializer

    def get_queryset(self):
        qs = (
            AgentRequestItem.objects
            .select_related(
                "cart",
                "cart__agent",
                "cart__company",
                "cart__branch",
                "product",
                "subreal",
            )
            .all()
        )
        qs = self._filter_qs_company_branch(
            qs,
            company_field="cart__company",
            branch_field="cart__branch",
        )

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cart__agent=user)

        return qs

    def perform_update(self, serializer):
        """
        В сериализаторе:
          - запрещено менять, если cart.status != DRAFT
          - можно менять только product / quantity_requested
        """
        serializer.save()

    def perform_destroy(self, instance):
        """
        Удаляем позицию только если корзина DRAFT.
        Агент — только свою.
        Владелец — любую в своей компании/филиале.
        """
        cart = instance.cart
        if cart.status != AgentRequestCart.Status.DRAFT:
            raise ValidationError("Удалять позиции можно только в черновике.")

        user = self.request.user
        if not _is_owner_like(user) and cart.agent_id != user.id:
            raise ValidationError("Нельзя удалить позицию из чужой корзины.")

        instance.delete()

      
class AgentMyAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/agents/me/analytics/

    Квери:
      ?period=day|week|month|custom
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # компания из миксина (company/branch уже используются во всей системе)
        company = self._company()
        branch = self._auto_branch()
        agent = request.user

        # fallback, если вдруг _company() вернул None
        if company is None:
            company = getattr(agent, "owned_company", None) or getattr(agent, "company", None)

        if company is None or getattr(agent, "company_id", None) != getattr(company, "id", None):
            return Response(
                {"detail": "Профиль агента не привязан к компании."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        period_params = _parse_period(request)

        data = build_agent_analytics_payload(
            company=company,
            branch=branch,
            agent=agent,
            **period_params,
        )
        return Response(data, status=status.HTTP_200_OK)


class OwnerAgentAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/owners/agents/<uuid:agent_id>/analytics/

    Доступ: только владелец/админ (_is_owner_like).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, agent_id, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        company = self._company()
        branch = self._auto_branch()

        # fallback, если _company() ничего не дал
        if company is None:
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)

        if company is None:
            return Response(
                {"detail": "У вас не задана компания."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            agent = (
                User.objects
                .filter(company=company)
                .get(pk=agent_id)
            )
        except User.DoesNotExist:
            return Response({"detail": "Agent not found."}, status=status.HTTP_404_NOT_FOUND)

        period_params = _parse_period(request)

        data = build_agent_analytics_payload(
            company=company,
            branch=branch,
            agent=agent,
            **period_params,
        )
        return Response(data, status=status.HTTP_200_OK)
    
class OwnerOverallAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/owners/analytics/

    Доступ: только владелец/админ (_is_owner_like).
    Аналитика считается по всей компании (ветка/branch — как в твоём миксине).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        company = self._company()
        branch = self._auto_branch()

        # fallback, если _company() ничего не дал
        if company is None:
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)

        if company is None:
            return Response(
                {"detail": "У вас не задана компания."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        period_params = _parse_period(request)

        data = build_owner_analytics_payload(
            company=company,
            branch=branch,
            **period_params,
        )
        return Response(data, status=status.HTTP_200_OK)


class AnalyticsCardDetailsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/main/analytics/cards/details/?card=<key>

    Деталка для модалки по клику на карточку в аналитике.
    Доступ: любой авторизованный пользователь (агенты тоже).

    Поддерживаемые card:
      - stock_purchase_value (alias: stock_value)
      - stock_retail_value
      - raw_material_value
      - defective_items
      - discounts_total
      - transfers_count
      - items_transferred
      - acceptances_count
      - sales_count
      - sales_amount
      - items_on_hand_qty
      - items_on_hand_amount
      - revenue
      - cost_of_goods_sold
      - gross_profit
      - gross_margin_percent
      - accounts_receivable
      - accounts_payable
      - total_debt
      - users_count
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        card = (request.query_params.get("card") or "").strip()
        if not card:
            raise ValidationError({"card": "Required. Example: card=stock_purchase_value"})

        agent_id = (request.query_params.get("agent_id") or "").strip()

        # простая пагинация под модалку (не DRF pagination, чтобы фронту было проще)
        limit = _parse_int_nonneg(
            request.query_params.get("limit"), "limit", default=200, maximum=1000
        )
        offset = _parse_int_nonneg(
            request.query_params.get("offset"), "offset", default=0, maximum=1000000
        )

        company = self._company()
        if company is None:
            return Response({"detail": "У вас не задана компания."}, status=status.HTTP_400_BAD_REQUEST)

        # branch фильтруем так же, как products/list и items-make (через миксин)
        branch = self._auto_branch()

        # ----- stock: products -----
        if card in ("stock_purchase_value", "stock_value", "stock_retail_value"):
            qs = Product.objects.all().exclude(kind=Product.Kind.SERVICE)
            qs = self._filter_qs_company_branch(qs)
            qs = qs.order_by("name", "id")

            total_count = qs.count()
            page = list(qs[offset: offset + limit].values(
                "id", "name", "quantity", "purchase_price", "price", "unit", "kind",
            ))

            items = []
            total_purchase = Decimal("0.00")
            total_retail = Decimal("0.00")
            for p in page:
                qty = _to_dec(p.get("quantity"), default=Decimal("0"))
                purchase_price = _to_dec(p.get("purchase_price"), default=Decimal("0"))
                retail_price = _to_dec(p.get("price"), default=Decimal("0"))
                purchase_sum = (qty * purchase_price).quantize(_Q2, rounding=ROUND_HALF_UP)
                retail_sum = (qty * retail_price).quantize(_Q2, rounding=ROUND_HALF_UP)
                total_purchase += purchase_sum
                total_retail += retail_sum
                items.append({
                    "id": str(p["id"]),
                    "name": p["name"],
                    "unit": p.get("unit"),
                    "kind": p.get("kind"),
                    "quantity": str(qty),
                    "purchase_price": str(purchase_price),
                    "retail_price": str(retail_price),
                    "purchase_sum": str(purchase_sum),
                    "retail_sum": str(retail_sum),
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "purchase_sum": str(total_purchase.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "retail_sum": str(total_retail.quantize(_Q2, rounding=ROUND_HALF_UP)),
                },
                "items": items,
            })

        # ----- raw materials: item make -----
        if card == "raw_material_value":
            qs = ItemMake.objects.select_related("supplier").all()
            qs = self._filter_qs_company_branch(qs)
            qs = qs.order_by("name", "id")

            total_count = qs.count()
            page = list(qs[offset: offset + limit].values(
                "id", "name", "quantity", "unit", "price",
                "supplier_id", "supplier__full_name",
            ))

            items = []
            total_sum = Decimal("0.00")
            for it in page:
                qty = _to_dec(it.get("quantity"), default=Decimal("0"))
                price = _to_dec(it.get("price"), default=Decimal("0"))
                s = (qty * price).quantize(_Q2, rounding=ROUND_HALF_UP)
                total_sum += s
                items.append({
                    "id": str(it["id"]),
                    "name": it["name"],
                    "unit": it.get("unit"),
                    "quantity": str(qty),
                    "price": str(price),
                    "sum": str(s),
                    "supplier": (
                        {"id": str(it["supplier_id"]), "full_name": it.get("supplier__full_name")}
                        if it.get("supplier_id") else None
                    ),
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "sum": str(total_sum.quantize(_Q2, rounding=ROUND_HALF_UP)),
                },
                "items": items,
            })

        # ----- defective items: accepted returns from agents -----
        if card == "defective_items":
            qs = ReturnFromAgent.objects.filter(
                company=company,
                status=ReturnFromAgent.Status.ACCEPTED,
            ).select_related("subreal__product", "returned_by")

            # Агент видит только свои возвраты; владелец/админ — все
            if not _is_owner_like(request.user):
                qs = qs.filter(returned_by=request.user)
            elif agent_id:
                qs = qs.filter(returned_by_id=agent_id)

            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

            # Клиент берётся из продажи (Sale.client) по этому subreal через последнюю аллокацию.
            alloc_client_id_subq = (
                AgentSaleAllocation.objects.filter(
                    company_id=company.id,
                    subreal_id=OuterRef("subreal_id"),
                )
                .exclude(sale__client_id__isnull=True)
                .order_by("-created_at")
                .values("sale__client_id")[:1]
            )
            alloc_client_name_subq = (
                AgentSaleAllocation.objects.filter(
                    company_id=company.id,
                    subreal_id=OuterRef("subreal_id"),
                )
                .exclude(sale__client_id__isnull=True)
                .order_by("-created_at")
                .values("sale__client__full_name")[:1]
            )

            grouped_qs = (
                qs.annotate(
                    client_id=Subquery(alloc_client_id_subq),
                    client_name=Subquery(alloc_client_name_subq),
                )
                .values(
                    "subreal__product_id",
                    "subreal__product__name",
                    "returned_by_id",
                    "returned_by__first_name",
                    "returned_by__last_name",
                    "client_id",
                    "client_name",
                )
                .annotate(
                    qty=Coalesce(Sum("qty"), V(0)),
                    returns_count=Count("id"),
                )
                .order_by("-qty", "subreal__product__name")
            )

            total_count = grouped_qs.count()
            page = list(grouped_qs[offset: offset + limit])

            items = []
            for r in page:
                agent_name = (
                    f"{(r.get('returned_by__first_name') or '').strip()} {(r.get('returned_by__last_name') or '').strip()}".strip()
                    or "Пользователь"
                )
                items.append({
                    "product_id": str(r["subreal__product_id"]) if r.get("subreal__product_id") else None,
                    "product_name": r.get("subreal__product__name") or "",
                    "qty": int(r.get("qty") or 0),
                    "returns_count": int(r.get("returns_count") or 0),
                    "agent": {
                        "id": str(r["returned_by_id"]) if r.get("returned_by_id") else None,
                        "name": agent_name,
                    },
                    "client": (
                        {
                            "id": str(r["client_id"]) if r.get("client_id") else None,
                            "name": r.get("client_name") or "",
                        }
                        if r.get("client_id") or r.get("client_name")
                        else None
                    ),
                })
            total_qty = int(
                (grouped_qs.aggregate(s=Coalesce(Sum("qty"), V(0)))["s"]) or 0
            )

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "qty": total_qty,
                },
                "items": items,
            })

        # ----- transfers: ManufactureSubreal (transfers_count и items_transferred — один список) -----
        if card in ("transfers_count", "items_transferred"):
            p = _parse_period(request)
            date_from = p["date_from"]
            date_to = p["date_to"]

            if _is_owner_like(request.user):
                dt_from, dt_to_excl = _dt_range(date_from, date_to)
                qs = ManufactureSubreal.objects.filter(
                    company=company,
                    created_at__gte=dt_from,
                    created_at__lt=dt_to_excl,
                )
                agent_id = (request.query_params.get("agent_id") or "").strip()
                if agent_id:
                    qs = qs.filter(agent_id=agent_id)
            else:
                dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
                dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))
                qs = ManufactureSubreal.objects.filter(
                    company=company,
                    agent=request.user,
                    created_at__gte=dt_from,
                    created_at__lte=dt_to,
                )

            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

            qs = qs.select_related("agent", "product", "user").order_by("-created_at", "-id")
            total_count = qs.count()
            items_transferred_total = int(
                (qs.aggregate(s=Coalesce(Sum("qty_transferred"), V(0)))["s"]) or 0
            )
            page = qs[offset: offset + limit]

            items = []
            for tr in page:
                agent_u = tr.agent
                agent_name = (
                    f"{(getattr(agent_u, 'first_name', None) or '').strip()} {(getattr(agent_u, 'last_name', None) or '').strip()}".strip()
                    or getattr(agent_u, "username", None)
                    or "Пользователь"
                )
                prod = tr.product
                items.append({
                    "id": str(tr.id),
                    "created_at": timezone.localtime(tr.created_at).isoformat() if tr.created_at else None,
                    "status": tr.status,
                    "qty_transferred": int(tr.qty_transferred or 0),
                    "qty_accepted": int(tr.qty_accepted or 0),
                    "qty_returned": int(tr.qty_returned or 0),
                    "agent": {"id": str(tr.agent_id), "name": agent_name},
                    "product": {"id": str(tr.product_id), "name": getattr(prod, "name", None) or ""},
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "period": {"type": p["period"], "date_from": date_from, "date_to": date_to},
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {"items_transferred": items_transferred_total},
                "items": items,
            })

        # ----- acceptances: Acceptance (как summary acceptances_count в аналитике) -----
        if card == "acceptances_count":
            p = _parse_period(request)
            date_from = p["date_from"]
            date_to = p["date_to"]

            qs = Acceptance.objects.filter(company=company)
            if not _is_owner_like(request.user):
                qs = qs.filter(subreal__agent=request.user)
            elif agent_id:
                qs = qs.filter(subreal__agent_id=agent_id)

            if _is_owner_like(request.user):
                dt_from, dt_to_excl = _dt_range(date_from, date_to)
                qs = qs.filter(accepted_at__gte=dt_from, accepted_at__lt=dt_to_excl)
            else:
                dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
                dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))
                qs = qs.filter(accepted_at__gte=dt_from, accepted_at__lte=dt_to)

            if branch is not None:
                qs = qs.filter(subreal__branch=branch)
            else:
                qs = qs.filter(subreal__branch__isnull=True)

            qs = qs.select_related("subreal", "subreal__agent", "subreal__product", "accepted_by").order_by(
                "-accepted_at", "-id"
            )
            total_count = qs.count()
            qty_accepted_total = int((qs.aggregate(s=Coalesce(Sum("qty"), V(0)))["s"]) or 0)
            page = qs[offset: offset + limit]

            items = []
            for acc in page:
                sr = acc.subreal
                agent_u = sr.agent if sr else None
                agent_name = (
                    f"{(getattr(agent_u, 'first_name', None) or '').strip()} {(getattr(agent_u, 'last_name', None) or '').strip()}".strip()
                    or getattr(agent_u, "username", None)
                    or "Пользователь"
                ) if agent_u else "Пользователь"
                prod = sr.product if sr else None
                accepter = acc.accepted_by
                accepter_name = (
                    f"{(getattr(accepter, 'first_name', None) or '').strip()} {(getattr(accepter, 'last_name', None) or '').strip()}".strip()
                    or getattr(accepter, "username", None)
                    or "Пользователь"
                ) if accepter else "Пользователь"
                items.append({
                    "id": str(acc.id),
                    "accepted_at": timezone.localtime(acc.accepted_at).isoformat() if acc.accepted_at else None,
                    "qty": int(acc.qty or 0),
                    "accepted_by": {"id": str(accepter.id), "name": accepter_name} if accepter else None,
                    "agent": {"id": str(agent_u.id), "name": agent_name} if agent_u else None,
                    "subreal": {"id": str(sr.id), "status": sr.status} if sr else None,
                    "product": {"id": str(prod.id), "name": getattr(prod, "name", None) or ""} if prod else None,
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "period": {"type": p["period"], "date_from": date_from, "date_to": date_to},
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {"qty_accepted": qty_accepted_total},
                "items": items,
            })

        # ----- sales: список оплаченных продаж (sales_count / sales_amount в summary) -----
        if card in ("sales_count", "sales_amount"):
            p = _parse_period(request)
            date_from = p["date_from"]
            date_to = p["date_to"]

            if _is_owner_like(request.user):
                dt_from, dt_to_excl = _dt_range(date_from, date_to)
                qs = Sale.objects.filter(
                    company=company,
                    status=Sale.Status.PAID,
                    created_at__gte=dt_from,
                    created_at__lt=dt_to_excl,
                )
                if agent_id:
                    qs = qs.filter(user_id=agent_id)
            else:
                dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
                dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))
                qs = Sale.objects.filter(
                    company=company,
                    user=request.user,
                    status=Sale.Status.PAID,
                    created_at__gte=dt_from,
                    created_at__lte=dt_to,
                )

            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

            money_field = DecimalField(max_digits=12, decimal_places=2)
            zero_money = V(Decimal("0.00"), output_field=money_field)
            sales_amount_total = qs.aggregate(s=Coalesce(Sum("total"), zero_money))["s"] or Decimal("0.00")

            qs = qs.select_related("user", "client").order_by("-created_at", "-id")
            total_count = qs.count()
            page = qs[offset: offset + limit]

            items = []
            for sale in page:
                u = sale.user
                user_name = (
                    f"{(getattr(u, 'first_name', None) or '').strip()} {(getattr(u, 'last_name', None) or '').strip()}".strip()
                    or getattr(u, "username", None)
                    or "Пользователь"
                ) if u else "Пользователь"
                cl = sale.client
                items.append({
                    "id": str(sale.id),
                    "created_at": timezone.localtime(sale.created_at).isoformat() if sale.created_at else None,
                    "total": str((sale.total or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "discount_total": str((sale.discount_total or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "user": {"id": str(sale.user_id), "name": user_name} if sale.user_id else None,
                    "client": (
                        {"id": str(cl.id), "name": getattr(cl, "full_name", None) or ""}
                        if cl else None
                    ),
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "period": {"type": p["period"], "date_from": date_from, "date_to": date_to},
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "sales_amount": str(sales_amount_total.quantize(_Q2, rounding=ROUND_HALF_UP)),
                },
                "items": items,
            })

        # ----- остатки на руках у агента (как summary в analytics_agent) -----
        if card in ("items_on_hand_qty", "items_on_hand_amount"):
            if _is_owner_like(request.user):
                if not agent_id:
                    return Response({
                        "card": card,
                        "branch_id": str(getattr(branch, "id", "")) if branch else None,
                        "count": 0,
                        "offset": offset,
                        "limit": limit,
                        "totals": {
                            "qty_on_hand": 0,
                            "amount": "0.00",
                        },
                        "items": [],
                    })
                agent_obj = get_object_or_404(User.objects.filter(company=company), id=agent_id)
            else:
                agent_obj = request.user

            on_hand = _compute_agent_on_hand(company=company, branch=branch, agent=agent_obj)
            if card == "items_on_hand_qty":
                rows = list(on_hand["by_product_qty"])
            else:
                rows = list(on_hand["by_product_amount"])
            rows.sort(key=lambda r: ((r.get("product_name") or ""), str(r.get("product_id") or "")))
            total_count = len(rows)
            page = rows[offset: offset + limit]
            amt_total = Decimal(str(on_hand["total_amount"] or 0)).quantize(_Q2, rounding=ROUND_HALF_UP)
            items = []
            for r in page:
                row = {
                    "product_id": r.get("product_id"),
                    "product_name": r.get("product_name") or "",
                    "qty_on_hand": int(r.get("qty_on_hand") or 0),
                }
                if card == "items_on_hand_amount":
                    row["amount"] = str(
                        Decimal(str(r.get("amount") or 0)).quantize(_Q2, rounding=ROUND_HALF_UP)
                    )
                items.append(row)

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "qty_on_hand": int(on_hand["total_qty"] or 0),
                    "amount": str(amt_total),
                },
                "items": items,
            })

        # ----- discounts total: by employee and client -----
        if card == "discounts_total":
            # период берём из тех же query params, что и в аналитике
            p = _parse_period(request)
            date_from = p["date_from"]
            date_to = p["date_to"]
            dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
            dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

            money_field = DecimalField(max_digits=12, decimal_places=2)
            zero_money = V(Decimal("0.00"), output_field=money_field)

            qs = Sale.objects.filter(
                company=company,
                status=Sale.Status.PAID,
                created_at__range=(dt_from, dt_to),
            )

            # Агент видит только свои продажи; владелец/админ — все
            if not _is_owner_like(request.user):
                qs = qs.filter(user=request.user)
            elif agent_id:
                qs = qs.filter(user_id=agent_id)

            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

            grouped_qs = (
                qs.filter(discount_total__gt=0)
                .values(
                    "user_id",
                    "user__first_name",
                    "user__last_name",
                    "client_id",
                    "client__full_name",
                )
                .annotate(
                    sales_count=Count("id"),
                    discounts_total=Coalesce(Sum("discount_total"), zero_money),
                )
                .order_by("-discounts_total")
            )

            total_count = grouped_qs.count()
            page = list(grouped_qs[offset: offset + limit])

            items = []
            for r in page:
                user_name = (
                    f"{(r.get('user__first_name') or '').strip()} {(r.get('user__last_name') or '').strip()}".strip()
                    or "Пользователь"
                )
                items.append({
                    "user": {"id": str(r["user_id"]) if r.get("user_id") else None, "name": user_name},
                    "client": {
                        "id": str(r["client_id"]) if r.get("client_id") else None,
                        "name": r.get("client__full_name") or "Без имени",
                    },
                    "sales_count": int(r.get("sales_count") or 0),
                    "discounts_total": str((r.get("discounts_total") or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                })

            totals = grouped_qs.aggregate(s=Coalesce(Sum("discounts_total"), zero_money))["s"] or Decimal("0.00")

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "period": {"type": p["period"], "date_from": date_from, "date_to": date_to},
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {"discounts_total": str(totals.quantize(_Q2, rounding=ROUND_HALF_UP))},
                "items": items,
            })

        # ----- выручка / COGS / валовая / маржа (как summary в analytics_owner_production / analytics_agent) -----
        if card in (
            "revenue",
            "cost_of_goods_sold",
            "gross_profit",
            "gross_margin_percent",
        ):
            p = _parse_period(request)
            date_from = p["date_from"]
            date_to = p["date_to"]

            if _is_owner_like(request.user):
                dt_from, dt_to_excl = _dt_range(date_from, date_to)
                sales_qs = Sale.objects.filter(
                    company=company,
                    status=Sale.Status.PAID,
                    created_at__gte=dt_from,
                    created_at__lt=dt_to_excl,
                )
                if agent_id:
                    sales_qs = sales_qs.filter(user_id=agent_id)
            else:
                dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
                dt_to = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))
                sales_qs = Sale.objects.filter(
                    company=company,
                    user=request.user,
                    status=Sale.Status.PAID,
                    created_at__gte=dt_from,
                    created_at__lte=dt_to,
                )

            if branch is not None:
                sales_qs = sales_qs.filter(branch=branch)
            else:
                sales_qs = sales_qs.filter(branch__isnull=True)

            money_field = DecimalField(max_digits=12, decimal_places=2)
            zero_money = V(Decimal("0.00"), output_field=money_field)
            revenue_expr = ExpressionWrapper(
                (F("quantity") * F("unit_price")) - Coalesce(F("line_discount"), zero_money),
                output_field=money_field,
            )
            _unit_purchase = Coalesce(
                F("purchase_price_snapshot"),
                F("product__purchase_price"),
                V(Decimal("0"), output_field=money_field),
            )

            items_qs = SaleItem.objects.filter(sale__in=sales_qs).select_related("product")

            # Выручка = продажи (Sale.total), не сумма строк.
            # Это соответствует "просто продажи − закупка".
            sales_total_agg = sales_qs.aggregate(
                revenue=Coalesce(Sum("total"), zero_money)
            )
            rev_t = sales_total_agg["revenue"] or Decimal("0.00")

            full_agg = items_qs.aggregate(
                cost_of_goods_sold=Coalesce(
                    Sum(F("quantity") * _unit_purchase, output_field=money_field),
                    zero_money,
                ),
            )
            cogs_t = full_agg["cost_of_goods_sold"] or Decimal("0.00")
            gp_t = rev_t - cogs_t
            margin_t = (
                (gp_t / rev_t * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if rev_t > 0
                else Decimal("0.00")
            )

            grouped_qs = (
                items_qs.values("product_id", "product__name")
                .annotate(
                    revenue=Coalesce(Sum(revenue_expr, output_field=money_field), zero_money),
                    cost_of_goods_sold=Coalesce(
                        Sum(F("quantity") * _unit_purchase, output_field=money_field),
                        zero_money,
                    ),
                )
                .annotate(
                    gross_profit=ExpressionWrapper(
                        F("revenue") - F("cost_of_goods_sold"),
                        output_field=money_field,
                    ),
                )
            )

            if card == "revenue":
                grouped_qs = grouped_qs.filter(revenue__gt=0).order_by("-revenue", "product_id")
            elif card == "cost_of_goods_sold":
                grouped_qs = grouped_qs.filter(cost_of_goods_sold__gt=0).order_by("-cost_of_goods_sold", "product_id")
            elif card == "gross_profit":
                grouped_qs = grouped_qs.filter(
                    Q(revenue__gt=0) | Q(cost_of_goods_sold__gt=0)
                ).order_by("-gross_profit", "product_id")
            else:
                grouped_qs = grouped_qs.filter(revenue__gt=0).order_by("-gross_profit", "product_id")

            total_count = grouped_qs.count()
            page = list(grouped_qs[offset: offset + limit])

            items = []
            for r in page:
                rev = r.get("revenue") or Decimal("0.00")
                cogs = r.get("cost_of_goods_sold") or Decimal("0.00")
                gp = r.get("gross_profit") if r.get("gross_profit") is not None else (rev - cogs)
                if not isinstance(gp, Decimal):
                    gp = Decimal(str(gp))
                m_pct = (
                    (gp / rev * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if rev > 0
                    else Decimal("0.00")
                )
                items.append({
                    "product_id": str(r["product_id"]) if r.get("product_id") else None,
                    "product_name": r.get("product__name") or "",
                    "revenue": str(rev.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "cost_of_goods_sold": str(cogs.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "gross_profit": str(gp.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "gross_margin_percent": str(m_pct),
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "period": {"type": p["period"], "date_from": date_from, "date_to": date_to},
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "revenue": str(rev_t.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "cost_of_goods_sold": str(cogs_t.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "gross_profit": str(gp_t.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "gross_margin_percent": str(margin_t),
                },
                "items": items,
            })

        # ----- total_debt: остаток по ClientDeal(kind=debt) -----
        if card == "total_debt":
            money_field_td = DecimalField(max_digits=12, decimal_places=2)
            zero_money_td = V(Decimal("0.00"), output_field=money_field_td)

            if _is_owner_like(request.user):
                deals_qs = ClientDeal.objects.filter(company=company, kind=ClientDeal.Kind.DEBT)
                if branch is not None:
                    deals_qs = deals_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    deals_qs = deals_qs.filter(branch__isnull=True)
            else:
                clients_scope = Client.objects.filter(company=company, salesperson=request.user)
                if branch is not None:
                    clients_scope = clients_scope.filter(branch=branch)
                else:
                    clients_scope = clients_scope.filter(branch__isnull=True)
                deals_qs = ClientDeal.objects.filter(
                    company=company,
                    kind=ClientDeal.Kind.DEBT,
                    client__in=clients_scope,
                )
                if branch is not None:
                    deals_qs = deals_qs.filter(branch=branch)
                else:
                    deals_qs = deals_qs.filter(branch__isnull=True)

            paid_subq = (
                DealInstallment.objects.filter(deal_id=OuterRef("pk"))
                .values("deal_id")
                .annotate(s=Sum("paid_amount"))
                .values("s")[:1]
            )
            deals_ann = (
                deals_qs.select_related("client")
                .annotate(paid=Coalesce(Subquery(paid_subq), V(Decimal("0.00"), output_field=money_field_td)))
                .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
                .filter(remaining__gt=0)
                .order_by("-remaining", "-id")
            )
            total_count = deals_ann.count()
            total_remaining = (
                deals_ann.aggregate(t=Coalesce(Sum("remaining"), zero_money_td))["t"] or Decimal("0.00")
            )
            page = deals_ann[offset: offset + limit]

            items = []
            for d in page:
                cl = d.client
                items.append({
                    "id": str(d.id),
                    "title": d.title,
                    "client": (
                        {"id": str(cl.id), "name": getattr(cl, "full_name", None) or ""}
                        if cl else None
                    ),
                    "amount": str((d.amount or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "prepayment": str((d.prepayment or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "paid": str((d.paid or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "remaining": str((d.remaining or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)),
                })

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {"total_debt": str(total_remaining.quantize(_Q2, rounding=ROUND_HALF_UP))},
                "items": items,
            })

        # ----- accounts_receivable: сделки DEBT + продажи в долг -----
        if card == "accounts_receivable":
            money_field_ar = DecimalField(max_digits=12, decimal_places=2)
            zero_money_ar = V(Decimal("0.00"), output_field=money_field_ar)

            if _is_owner_like(request.user):
                deals_qs = ClientDeal.objects.filter(company=company, kind=ClientDeal.Kind.DEBT)
                if branch is not None:
                    deals_qs = deals_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    deals_qs = deals_qs.filter(branch__isnull=True)
                sales_debt_qs = Sale.objects.filter(company=company, status=Sale.Status.DEBT)
                if branch is not None:
                    sales_debt_qs = sales_debt_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    sales_debt_qs = sales_debt_qs.filter(branch__isnull=True)
            else:
                clients_scope = Client.objects.filter(company=company, salesperson=request.user)
                if branch is not None:
                    clients_scope = clients_scope.filter(branch=branch)
                else:
                    clients_scope = clients_scope.filter(branch__isnull=True)
                deals_qs = ClientDeal.objects.filter(
                    company=company,
                    kind=ClientDeal.Kind.DEBT,
                    client__in=clients_scope,
                )
                if branch is not None:
                    deals_qs = deals_qs.filter(branch=branch)
                else:
                    deals_qs = deals_qs.filter(branch__isnull=True)
                sales_debt_qs = Sale.objects.filter(
                    company=company,
                    user=request.user,
                    status=Sale.Status.DEBT,
                )
                if branch is not None:
                    sales_debt_qs = sales_debt_qs.filter(branch=branch)
                else:
                    sales_debt_qs = sales_debt_qs.filter(branch__isnull=True)

            paid_subq_ar = (
                DealInstallment.objects.filter(deal_id=OuterRef("pk"))
                .values("deal_id")
                .annotate(s=Sum("paid_amount"))
                .values("s")[:1]
            )
            deal_rows = list(
                deals_qs.select_related("client")
                .annotate(paid=Coalesce(Subquery(paid_subq_ar), V(Decimal("0.00"), output_field=money_field_ar)))
                .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
                .filter(remaining__gt=0)
                .values("id", "title", "remaining", "client_id", "client__full_name")
            )
            sale_rows = list(
                sales_debt_qs.select_related("client", "user")
                .order_by("-total", "-id")
                .values("id", "total", "created_at", "client_id", "client__full_name", "user_id")
            )

            merged = []
            for r in deal_rows:
                rem = r.get("remaining") or Decimal("0.00")
                merged.append({
                    "_sort": rem,
                    "kind": "client_deal",
                    "id": str(r["id"]),
                    "title": r.get("title") or "",
                    "amount": str(rem.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "client": {
                        "id": str(r["client_id"]) if r.get("client_id") else None,
                        "name": r.get("client__full_name") or "",
                    },
                })
            for r in sale_rows:
                tot = r.get("total") or Decimal("0.00")
                merged.append({
                    "_sort": tot,
                    "kind": "sale_debt",
                    "id": str(r["id"]),
                    "total": str(tot.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "created_at": timezone.localtime(r["created_at"]).isoformat() if r.get("created_at") else None,
                    "client": {
                        "id": str(r["client_id"]) if r.get("client_id") else None,
                        "name": r.get("client__full_name") or "Без имени",
                    },
                    "user_id": str(r["user_id"]) if r.get("user_id") else None,
                })
            merged.sort(key=lambda x: x["_sort"], reverse=True)
            for x in merged:
                x.pop("_sort", None)

            client_deals_total = sum(
                (Decimal(str(r["remaining"])) if r.get("remaining") is not None else Decimal("0"))
                for r in deal_rows
            )
            pos_total = sum(
                (Decimal(str(r["total"])) if r.get("total") is not None else Decimal("0"))
                for r in sale_rows
            )
            ar_total = (client_deals_total + pos_total).quantize(_Q2, rounding=ROUND_HALF_UP)

            total_count = len(merged)
            page = merged[offset: offset + limit]

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {
                    "accounts_receivable": str(ar_total),
                    "accounts_receivable_client_deals": str(client_deals_total.quantize(_Q2, rounding=ROUND_HALF_UP)),
                    "accounts_receivable_pos_sales": str(pos_total.quantize(_Q2, rounding=ROUND_HALF_UP)),
                },
                "items": page,
            })

        # ----- accounts_payable (owner): по контрагентам-поставщикам склада -----
        if card == "accounts_payable":
            if not _is_owner_like(request.user):
                return Response({
                    "card": card,
                    "branch_id": str(getattr(branch, "id", "")) if branch else None,
                    "count": 0,
                    "offset": offset,
                    "limit": limit,
                    "totals": {"accounts_payable": "0.00"},
                    "items": [],
                })
            try:
                from apps.warehouse.models import Document as WDoc, MoneyDocument as WMoney, Counterparty as WCP
            except Exception:
                return Response({
                    "card": card,
                    "branch_id": str(getattr(branch, "id", "")) if branch else None,
                    "count": 0,
                    "offset": offset,
                    "limit": limit,
                    "totals": {"accounts_payable": "0.00"},
                    "items": [],
                })

            cp_qs = WCP.objects.filter(company=company).filter(
                Q(type=WCP.Type.SUPPLIER) | Q(type=WCP.Type.BOTH)
            )
            if branch is not None:
                cp_qs = cp_qs.filter(branch=branch)
            else:
                cp_qs = cp_qs.filter(branch__isnull=True)

            trade_doc_types = (
                WDoc.DocType.SALE,
                WDoc.DocType.PURCHASE,
                WDoc.DocType.SALE_RETURN,
                WDoc.DocType.PURCHASE_RETURN,
            )
            doc_debit_types = (WDoc.DocType.SALE, WDoc.DocType.PURCHASE_RETURN)
            doc_credit_types = (WDoc.DocType.PURCHASE, WDoc.DocType.SALE_RETURN)

            money_field_ap = DecimalField(max_digits=12, decimal_places=2)
            zero_money_ap = V(Decimal("0.00"), output_field=money_field_ap)

            rows_out = []
            ap_sum = Decimal("0.00")
            for cp in cp_qs.order_by("name", "id"):
                docs_qs = WDoc.objects.filter(
                    status=WDoc.Status.POSTED,
                    doc_type__in=trade_doc_types,
                    counterparty=cp,
                    warehouse_from__company=company,
                )
                if branch is not None:
                    docs_qs = docs_qs.filter(warehouse_from__branch=branch)
                else:
                    docs_qs = docs_qs.filter(warehouse_from__branch__isnull=True)

                docs_agg = docs_qs.aggregate(
                    doc_debit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_debit_types)), zero_money_ap),
                    doc_credit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_credit_types)), zero_money_ap),
                )
                doc_debit = docs_agg["doc_debit"] or Decimal("0.00")
                doc_credit = docs_agg["doc_credit"] or Decimal("0.00")

                money_qs = WMoney.objects.filter(
                    company=company,
                    status=WMoney.Status.POSTED,
                    counterparty=cp,
                    doc_type__in=(
                        WMoney.DocType.MONEY_RECEIPT,
                        WMoney.DocType.MONEY_EXPENSE,
                    ),
                )
                if branch is not None:
                    money_qs = money_qs.filter(branch=branch)
                else:
                    money_qs = money_qs.filter(branch__isnull=True)

                money_agg = money_qs.aggregate(
                    m_rec=Coalesce(Sum("amount", filter=Q(doc_type=WMoney.DocType.MONEY_RECEIPT)), zero_money_ap),
                    m_paid=Coalesce(Sum("amount", filter=Q(doc_type=WMoney.DocType.MONEY_EXPENSE)), zero_money_ap),
                )
                m_rec = money_agg["m_rec"] or Decimal("0.00")
                m_paid = money_agg["m_paid"] or Decimal("0.00")

                balance = (doc_debit + m_paid) - (doc_credit + m_rec)
                if balance >= 0:
                    continue
                payable = (-balance).quantize(_Q2, rounding=ROUND_HALF_UP)
                ap_sum += payable
                rows_out.append({
                    "counterparty_id": str(cp.id),
                    "name": cp.name,
                    "accounts_payable": str(payable),
                })

            # CRM-поставщики: Client.type=SUPPLIERS + сделки ClientDeal(kind=DEBT) → обязательства компании
            try:
                from apps.main.models import Client, ClientDeal, DealInstallment
            except Exception:
                Client = None
                ClientDeal = None
                DealInstallment = None

            if Client and ClientDeal and DealInstallment:
                supplier_clients_qs = Client.objects.filter(company=company, type=Client.StatusClient.SUPPLIERS)
                if branch is not None:
                    supplier_clients_qs = supplier_clients_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    supplier_clients_qs = supplier_clients_qs.filter(branch__isnull=True)

                supplier_deals_qs = ClientDeal.objects.filter(
                    company=company,
                    kind=ClientDeal.Kind.DEBT,
                    client__in=supplier_clients_qs,
                )
                if branch is not None:
                    supplier_deals_qs = supplier_deals_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
                else:
                    supplier_deals_qs = supplier_deals_qs.filter(branch__isnull=True)

                supplier_paid_subq = (
                    DealInstallment.objects.filter(deal_id=OuterRef("pk"))
                    .values("deal_id")
                    .annotate(s=Sum("paid_amount"))
                    .values("s")[:1]
                )
                supplier_rows = (
                    supplier_deals_qs
                    .annotate(paid=Coalesce(Subquery(supplier_paid_subq), V(Decimal("0.00"), output_field=money_field_ap)))
                    .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
                    .values("client_id", "client__full_name")
                    .annotate(remaining_total=Coalesce(Sum("remaining"), zero_money_ap))
                )
                for r in supplier_rows:
                    rem = (r.get("remaining_total") or Decimal("0.00")).quantize(_Q2, rounding=ROUND_HALF_UP)
                    if rem <= 0:
                        continue
                    ap_sum += rem
                    rows_out.append({
                        "counterparty_id": str(r["client_id"]),
                        "name": r.get("client__full_name") or "Поставщик",
                        "accounts_payable": str(rem),
                    })

            rows_out.sort(key=lambda x: Decimal(x["accounts_payable"]), reverse=True)
            total_count = len(rows_out)
            page = rows_out[offset: offset + limit]

            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {"accounts_payable": str(ap_sum.quantize(_Q2, rounding=ROUND_HALF_UP))},
                "items": page,
            })

        if card == "users_count":
            qs = User.objects.filter(company=company).order_by("last_name", "first_name", "email", "id")
            total_count = qs.count()
            page = list(
                qs[offset: offset + limit].values(
                    "id", "email", "first_name", "last_name", "phone_number", "role", "is_active"
                )
            )
            items = [
                {
                    "id": str(u["id"]),
                    "email": u.get("email") or "",
                    "first_name": u.get("first_name") or "",
                    "last_name": u.get("last_name") or "",
                    "phone_number": u.get("phone_number"),
                    "role": u.get("role"),
                    "is_active": bool(u.get("is_active")),
                }
                for u in page
            ]
            return Response({
                "card": card,
                "branch_id": str(getattr(branch, "id", "")) if branch else None,
                "count": total_count,
                "offset": offset,
                "limit": limit,
                "totals": {},
                "items": items,
            })

        raise ValidationError({
            "card": f"Unsupported card: {card}",
            "supported": [
                "stock_purchase_value",
                "stock_retail_value",
                "raw_material_value",
                "stock_value",
                "defective_items",
                "discounts_total",
                "transfers_count",
                "items_transferred",
                "acceptances_count",
                "sales_count",
                "sales_amount",
                "items_on_hand_qty",
                "items_on_hand_amount",
                "revenue",
                "cost_of_goods_sold",
                "gross_profit",
                "gross_margin_percent",
                "accounts_receivable",
                "accounts_payable",
                "total_debt",
                "users_count",
            ],
        })
