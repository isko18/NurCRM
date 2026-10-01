from decimal import Decimal

from django.db.models import Q, F, Value, ExpressionWrapper, DecimalField, Case, When
from django.db.models.functions import Coalesce, Lower
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Company
from ..models import Product
from .serializers_public import PublicCompanySerializer, PublicProductSerializer


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

    def filter_queryset(self, request, queryset, view):
        ordering_param = request.query_params.get(self.ordering_param)
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

        # Сортировка по умолчанию: сначала новые, затем по id для детерминированности
        default_ordering = getattr(view, "ordering", ["-created_at", "id"])
        return queryset.order_by(*default_ordering)


from rest_framework.pagination import PageNumberPagination


class ShowcasePagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


class PublicCompanyAPIView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCompanySerializer
    lookup_field = "slug"
    queryset = Company.objects.all()


class PublicCompanyShowcaseAPIView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicProductSerializer
    pagination_class = ShowcasePagination

    filter_backends = [DjangoFilterBackend, SearchFilter, ShowcaseOrderingFilter]
    filterset_fields = ["category", "brand", "is_weight", "stock"]
    search_fields = ["name", "barcode", "article", "code"]
    ordering_fields = ["name", "final_price", "discount_percent", "created_at", "price"]
    ordering = ["-created_at", "id"]


    def get_company(self) -> Company:
        slug = self.kwargs.get("slug")
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise NotFound("Компания не найдена")

    def get_queryset(self):
        company = self.get_company()

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
            .prefetch_related("images", "packages", "characteristics")
            .annotate(
                final_price=final_price_expr,
                clean_discount_percent=clean_discount_expr,
                ordering_name=ordering_name_expr,
            )
        )

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            qs = qs.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))

        return qs



class PublicCompanyProductDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicProductSerializer
    lookup_url_kwarg = "product_id"

    def get_company(self) -> Company:
        slug = self.kwargs.get("slug")
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise NotFound("Компания не найдена")

    def get_queryset(self):
        company = self.get_company()
        return (
            Product.objects
            .filter(company=company)  # ✅ без status фильтра
            .select_related("brand", "category")
            .prefetch_related("images", "packages", "characteristics")
        )
