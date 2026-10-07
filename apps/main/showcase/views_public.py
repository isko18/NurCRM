from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, F, Value, ExpressionWrapper, DecimalField, Case, When, Prefetch, OuterRef, Subquery, Exists
from django.utils import timezone
from django.db.models.functions import Coalesce, Lower
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Company
from ..models import Product, ProductVariant, ProductPromotionTier, ShowcaseProductSettings
from .serializers_public import PublicCompanySerializer, PublicProductListSerializer, PublicProductSerializer
from . import services as showcase_svc
from .services import ShowcaseErrorMixin, resolve_public_company


class ShowcaseOrderingFilter(OrderingFilter):
    """
    Кастомный OrderingFilter для витрины:
    1. Whitelist полей: name, final_price, discount_percent, price, created_at (и их '-' варианты).
    2. При неизвестном значении ordering возвращает 400 Bad Request с {"detail": "..."}.
    3. Для name использует регистронезависимую сортировку Lower("name").
    4. Для устойчивой детерминированной пагинации добавляет вторичный tie-breaker (-created_at, id).
    """

    ALLOWED_FIELDS = {
        "name",
        "final_price",
        "discount_percent",
        "price",
        "created_at",
    }

    # Коды сортировки из документа вида (products.sort_options / default_sort) → поля.
    ALIASES = {
        "name_asc": "name", "name_desc": "-name", "price_asc": "final_price", "price_desc": "-final_price",
        "discount_desc": "-discount_percent", "discount_asc": "discount_percent", "new": "-created_at",
    }

    def filter_queryset(self, request, queryset, view):
        ordering_param = request.query_params.get(self.ordering_param)
        catalog = getattr(view, "_catalog", None)
        if not ordering_param and catalog is not None:
            ordering_param = catalog.default_sort
        if ordering_param in ("default", None, ""):
            ordering_param = None
        if ordering_param == "manual":
            # Ручной порядок: закреплённые первыми, затем sort_order, новые товары — в конце.
            return queryset.order_by(
                F("sc_pinned").desc(nulls_last=True), F("sc_sort").asc(nulls_last=True), F("created_at").desc(), F("id").asc()
            )
        if ordering_param in self.ALIASES:
            ordering_param = self.ALIASES[ordering_param]
        if ordering_param:
            fields = [p.strip() for p in ordering_param.split(",") if p.strip()]
            for f in fields:
                clean_f = f.lstrip("-")
                if clean_f not in self.ALLOWED_FIELDS:
                    raise ValidationError(
                        {"detail": f"Недопустимое значение ordering: '{f}'"}
                    )

            ordering_clauses = []
            for f in fields:
                descending = f.startswith("-")
                field_name = f.lstrip("-")

                if field_name == "name":
                    clause = F("ordering_name").desc(nulls_last=True) if descending else F("ordering_name").asc(nulls_last=True)
                elif field_name == "final_price":
                    clause = F("final_price").desc(nulls_last=True) if descending else F("final_price").asc(nulls_last=True)
                elif field_name == "discount_percent":
                    clause = F("clean_discount_percent").desc(nulls_last=True) if descending else F("clean_discount_percent").asc(nulls_last=True)
                elif field_name == "price":
                    clause = F("price").desc(nulls_last=True) if descending else F("price").asc(nulls_last=True)
                elif field_name == "created_at":
                    clause = F("created_at").desc(nulls_last=True) if descending else F("created_at").asc(nulls_last=True)
                else:
                    clause = f
                ordering_clauses.append(clause)

            # Добавляем tie-breaker для гарантии стабильности между страницами
            ordering_clauses.extend([F("created_at").desc(), F("id").asc()])
            return queryset.order_by(*ordering_clauses)

        # Сортировка по умолчанию: закреплённые первыми, затем новые, затем по id
        if catalog is not None:
            return queryset.order_by(F("sc_pinned").desc(nulls_last=True), F("created_at").desc(), F("id").asc())
        default_ordering = getattr(view, "ordering", ["-created_at", "id"])
        return queryset.order_by(*default_ordering)


from rest_framework.pagination import PageNumberPagination


class ShowcasePagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


class PublicCompanyAPIView(ShowcaseErrorMixin, generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCompanySerializer
    lookup_field = "slug"
    queryset = Company.objects.all()

    def get_object(self):
        return resolve_public_company(self.kwargs.get("slug"), self.request)


def _bool_qp(request, name):
    v = request.query_params.get(name)
    if v is None or v == "":
        return None
    return str(v).lower() in ("1", "true", "yes")


def annotate_showcase_settings(qs, company, catalog):
    """sc_hidden / sc_pinned / sc_sort / sc_badge — опубликованные (или черновые в предпросмотре) настройки."""
    pre = catalog.field_prefix
    sub = ShowcaseProductSettings.objects.filter(company=company, product=OuterRef("pk"))
    return qs.annotate(
        sc_pinned=Subquery(sub.values(f"{pre}pinned")[:1]),
        sc_sort=Subquery(sub.values(f"{pre}sort_order")[:1]),
        sc_badge=Subquery(sub.values(f"{pre}badge")[:1]),
    )


def on_sale_q():
    return Q(discount_percent__gt=0) | Q(stock=True) & Exists(ProductPromotionTier.objects.filter(product=OuterRef("pk")))


class _CatalogMixin(ShowcaseErrorMixin):
    def get_company(self) -> Company:
        if not hasattr(self, "_company"):
            self._company = resolve_public_company(self.kwargs.get("slug"), self.request)
        return self._company

    def get_catalog(self):
        if not hasattr(self, "_catalog"):
            self._catalog = showcase_svc.get_public_catalog(self.get_company(), self.request.query_params.get("preview"))
        return self._catalog

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        catalog = self.get_catalog()
        ctx["new_badge_days"] = catalog.new_badge_days
        ctx["showcase_company"] = self.get_company()
        return ctx


class PublicCompanyShowcaseAPIView(_CatalogMixin, generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicProductListSerializer
    pagination_class = ShowcasePagination

    filter_backends = [DjangoFilterBackend, SearchFilter, ShowcaseOrderingFilter]
    filterset_fields = ["category", "brand", "is_weight", "stock"]
    search_fields = ["name", "barcode", "article", "code"]
    ordering_fields = ["name", "final_price", "discount_percent", "created_at", "price"]
    ordering = ["-created_at", "id"]


    def get_queryset(self):
        company = self.get_company()
        catalog = self.get_catalog()

        # Аннотация final_price:
        # Если discount_percent > 0: price * (1 - discount_percent/100)
        # Иначе (скидки нет, или NULL, или 0): price (fallback на 0 при NULL)
        final_price_expr = Case(
            When(
                discount_percent__gt=Decimal("0"),
                then=ExpressionWrapper(
                    Coalesce(F("price"), Value(Decimal("0")))
                    * (Value(Decimal("1")) - Coalesce(F("discount_percent"), Value(Decimal("0"))) / Value(Decimal("100"))),
                    output_field=DecimalField(max_digits=20, decimal_places=4),
                ),
            ),
            default=Coalesce(F("price"), Value(Decimal("0"))),
            output_field=DecimalField(max_digits=20, decimal_places=4),
        )

        clean_discount_expr = Coalesce(
            F("discount_percent"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        ordering_name_expr = Lower(Coalesce(F("name"), Value("")))

        qs = (
            Product.objects
            .filter(company=company)  # ✅ без status фильтра
            .select_related("brand", "category")
            .prefetch_related(
                "images", "packages", "characteristics", "promotion_tiers",
                Prefetch("variants", queryset=ProductVariant.objects.filter(is_active=True)),
            )
            .annotate(
                final_price=final_price_expr,
                clean_discount_percent=clean_discount_expr,
                ordering_name=ordering_name_expr,
            )
        )

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            qs = qs.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))

        # Видимость и порядок витрины (ТЗ-BE-2026-05, п. 6.4/6.5/6.13): только опубликованное состояние.
        qs = showcase_svc.apply_catalog_visibility(qs, catalog)
        qs = annotate_showcase_settings(qs, company, catalog)
        if catalog.hide_zero_price:
            qs = qs.filter(price__gt=0)
        if catalog.hide_out_of_stock:
            qs = qs.exclude(kind=Product.Kind.PRODUCT, quantity__lte=0)

        on_sale = _bool_qp(self.request, "on_sale")
        if on_sale is True:
            qs = qs.filter(on_sale_q())
        elif on_sale is False:
            qs = qs.exclude(on_sale_q())
        is_new = _bool_qp(self.request, "is_new")
        if is_new is not None:
            border = timezone.now() - timedelta(days=catalog.new_badge_days or 0)
            qs = qs.filter(created_at__gte=border) if is_new else qs.exclude(created_at__gte=border)
        pinned = _bool_qp(self.request, "pinned")
        if pinned is True:
            qs = qs.filter(sc_pinned=True)
        return qs



class PublicCompanyProductDetailAPIView(_CatalogMixin, generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicProductSerializer
    lookup_url_kwarg = "product_id"

    def get_queryset(self):
        company = self.get_company()
        catalog = self.get_catalog()
        qs = (
            Product.objects
            .filter(company=company)  # ✅ без status фильтра
            .select_related("brand", "category")
            .prefetch_related(
                "images", "packages", "characteristics", "promotion_tiers",
                Prefetch("variants", queryset=ProductVariant.objects.filter(is_active=True)),
            )
        )
        qs = showcase_svc.apply_catalog_visibility(qs, catalog)
        return annotate_showcase_settings(qs, company, catalog)

