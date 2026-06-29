# apps/products/views_public.py
from decimal import Decimal

from django.db.models import Q, F, Value, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import NotFound
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Company
from ..models import Product
from .serializers_public import PublicCompanySerializer, PublicProductSerializer


class PublicCompanyAPIView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCompanySerializer
    lookup_field = "slug"
    queryset = Company.objects.all()


class PublicCompanyShowcaseAPIView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicProductSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["category", "brand", "is_weight", "stock"]
    search_fields = ["name", "barcode", "article", "code"]
    # final_price — цена с учётом скидки (как на витрине), доступна через аннотацию ниже.
    # Неизвестные значения ordering DRF игнорирует → откат к ordering по умолчанию (без 400).
    ordering_fields = ["created_at", "price", "name", "final_price", "discount_percent"]
    ordering = ["-created_at"]

    def get_company(self) -> Company:
        slug = self.kwargs.get("slug")
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise NotFound("Компания не найдена")

    def get_queryset(self):
        company = self.get_company()

        # Аннотация final_price = price * (1 - discount_percent/100) на уровне БД,
        # чтобы OrderingFilter сортировал по всей выборке (до пагинации), а не по странице.
        final_price_expr = ExpressionWrapper(
            Coalesce(F("price"), Value(Decimal("0")))
            * (Value(Decimal("1")) - Coalesce(F("discount_percent"), Value(Decimal("0"))) / Value(Decimal("100"))),
            output_field=DecimalField(max_digits=20, decimal_places=4),
        )

        qs = (
            Product.objects
            .filter(company=company)  # ✅ без status фильтра
            .select_related("brand", "category")
            .prefetch_related("images", "packages", "characteristics")
            .annotate(final_price=final_price_expr)
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
