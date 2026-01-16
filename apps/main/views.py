from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction, IntegrityError
from django.db.models import Sum, Count, Avg, F, Q, Prefetch, Value as V
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from itertools import groupby
from typing import List, Optional, Dict, Any
from operator import attrgetter
from datetime import datetime, date as _date
from django.db.models.functions import Coalesce

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


from apps.users.models import Branch, User

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event,
    ProductBrand, ProductCategory, Warehouse, WarehouseEvent, Client,
    GlobalProduct, GlobalBrand, GlobalCategory, ClientDeal, Bid, SocialApplications, TransactionRecord,
    ContractorWork, DealInstallment, DebtPayment, Debt, ObjectSaleItem, ObjectSale, ObjectItem, ItemMake,
    ManufactureSubreal, Acceptance, ReturnFromAgent, AgentSaleAllocation, ProductImage,
    AgentRequestCart, AgentRequestItem, ProductPackage, ProductCharacteristics, DealPayment
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer, ClientSerializer, ClientDealSerializer, BidSerializers, SocialApplicationsSerializers,
    TransactionRecordSerializer, ContractorWorkSerializer, DebtSerializer, DebtPaymentSerializer,
    ObjectItemSerializer, ObjectSaleSerializer, ObjectSaleItemSerializer,
    BulkIdsSerializer, ItemMakeSerializer,
    ManufactureSubrealSerializer, AcceptanceCreateSerializer, ReturnCreateSerializer,
    BulkSubrealCreateSerializer, AcceptanceReadSerializer, ReturnApproveSerializer, ReturnReadSerializer,
    AgentProductOnHandSerializer, AgentWithProductsSerializer, GlobalProductReadSerializer,
    ProductImageSerializer,
    AgentRequestCartApproveSerializer, AgentRequestCartRejectSerializer,
    AgentRequestCartSerializer, AgentRequestCartSubmitSerializer, AgentRequestItemSerializer, DealPayInputSerializer, DealRefundInputSerializer
)
from django.db.models import ProtectedError
from apps.utils import product_images_prefetch, _is_owner_like
from apps.main.analytics_agent import build_agent_analytics_payload, _parse_period
from apps.main.analytics_owner_production import build_owner_analytics_payload
from apps.main.services import _parse_bool_like, _parse_date_to_aware_datetime, _parse_kind, _parse_int_nonneg, _parse_decimal
    


# ===========================
#  Company + Branch mixin (как в barber)
# ===========================
_Q2 = Decimal("0.01")


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
    return mp.quantize(_Q2, rounding=ROUND_HALF_UP)

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
                product_images_prefetch,  # как было, твой Prefetch для images
            )
        )
        return self._filter_qs_company_branch(qs)
    
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

            packages_to_create.append(
                ProductPackage(
                    product=product,
                    company=company,
                    branch=branch,
                    name=name,
                    quantity_in_package=qip,
                    unit=unit_pkg,
                )
            )

        if packages_to_create:
            ProductPackage.objects.bulk_create(packages_to_create)

        ser = self.get_serializer(product, context=self.get_serializer_context())
        return Response(ser.data, status=status.HTTP_201_CREATED)


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

        product = Product.objects.create(
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

        # item_make
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
                    status=status.HTTP_400_BAD_REQUEST
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

            packages_to_create.append(
                ProductPackage(
                    product=product,
                    company=company,
                    branch=branch,
                    name=name_pkg,
                    quantity_in_package=qip,
                    unit=unit_pkg,
                )
            )

        if packages_to_create:
            ProductPackage.objects.bulk_create(packages_to_create)

        # add to global product base (optional)
        if barcode:
            GlobalProduct.objects.get_or_create(
                barcode=barcode,
                defaults={"name": name, "brand": g_brand, "category": g_category},
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
            product_images_prefetch,
        )
        .all()
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
        return self._filter_qs_company_branch(qs)

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


class ProductCategoryRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.select_related("company", "branch", "parent").all()


class ProductBrandListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.select_related("company", "branch").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name"]
    filterset_fields = "__all__"


class ProductBrandRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.select_related("company", "branch").all()


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
        return self._filter_qs_company_branch(
            Client.objects.select_related("company", "branch").all()
        )


class ClientRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/clients/<uuid:pk>/
    PATCH  /api/main/clients/<uuid:pk>/
    PUT    /api/main/clients/<uuid:pk>/
    DELETE /api/main/clients/<uuid:pk>/
    """
    serializer_class = ClientSerializer

    def get_queryset(self):
        return self._filter_qs_company_branch(
            Client.objects.select_related("company", "branch").all()
        )


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

        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)

        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        client_id = self.kwargs.get("client_id")

        if not company:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})

        if client_id:
            client = get_object_or_404(Client, id=client_id, company=company)

            # клиент может быть общий (branch=None)
            if branch is not None and client.branch_id not in (None, branch.id):
                raise serializers.ValidationError({"client": "Клиент другого филиала."})

            serializer.save(company=company, branch=branch, client=client)
            return

        client = serializer.validated_data.get("client")
        if not client or client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент не найден в вашей компании."})

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

        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)

        return qs

    @transaction.atomic
    def perform_update(self, serializer):
        company = self._company()
        branch = self._auto_branch()

        if not company:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})

        new_client = serializer.validated_data.get("client")
        if new_client:
            if new_client.company_id != company.id:
                raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
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
            Debt.objects.select_related("company", "branch", "client").all()
        )


class DebtRetrieveUpdateDestroyAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/main/debts/<uuid:pk>/
    """
    serializer_class = DebtSerializer

    def get_queryset(self):
        return self._filter_qs_company_branch(
            Debt.objects.select_related("company", "branch", "client").all()
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
            Debt.objects.select_related("company", "branch", "client").all()
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
    search_fields = ["name", "products__name"]
    filterset_fields = ["unit", "price", "quantity", "products"]
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
#  Subreal / Acceptance / ReturnFromAgent
# ===========================
class ManufactureSubrealListCreateAPIView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManufactureSubrealSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["agent", "product", "status", "created_at"]
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

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(company=self._company(), returned_by=self._user())


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

        ser = ReturnApproveSerializer(
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

        created_objs = []

        for item in items:
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

            sub = ManufactureSubreal.objects.create(
                company=company,
                branch=branch,
                user=user,
                agent=agent,
                product=product,
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
        return self._filter_qs_company_branch(base)

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
        return self._filter_qs_company_branch(base)

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

        # если эта картинка помечена как основная —
        # снимаем is_primary со всех остальных
        if getattr(obj, "is_primary", False):
            ProductImage.objects.filter(product=product).exclude(pk=obj.pk).update(is_primary=False)


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

        # гарантируем, что только одна картинка у продукта будет is_primary=True
        if getattr(obj, "is_primary", False):
            ProductImage.objects.filter(product=obj.product).exclude(pk=obj.pk).update(is_primary=False)


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

    Query:
      ?period=day|week|month|custom
      ?date_from=YYYY-MM-DD
      ?date_to=YYYY-MM-DD
      ?date=YYYY-MM-DD (для period=day, если используешь)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        agent = request.user
        company = self._company()
        branch = self._auto_branch()

        # fallback, если вдруг _company() вернул None
        if company is None:
            company = getattr(agent, "owned_company", None) or getattr(agent, "company", None)

        # жёсткая защита от “не привязан” и “не та компания”
        if company is None:
            return Response(
                {"detail": "Профиль агента не привязан к компании."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent_company_id = getattr(agent, "company_id", None)
        company_id = getattr(company, "id", None)
        if agent_company_id is None or company_id is None or agent_company_id != company_id:
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

        # агент строго внутри компании владельца
        try:
            agent = User.objects.filter(company=company).get(pk=agent_id)
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
    Аналитика считается по всей компании (ветка/branch — как в миксине).
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