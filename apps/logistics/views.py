# apps/logistics/views.py

from decimal import Decimal

from django.db.models import (
    Sum,
    Count,
    F,
    DecimalField,
    ExpressionWrapper,
)

from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Logistics
from .serializers import LogisticsSerializer

from apps.main.views import CompanyBranchRestrictedMixin


class LogisticsListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/logistics/      -> список логистики (фильтр по company/branch из миксина)
    POST /api/logistics/      -> создать запись (company/branch подставятся из миксина)
    """

    queryset = (
        Logistics.objects
        .select_related("company", "branch", "client", "created_by")
        .all()
    )
    serializer_class = LogisticsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        """
        created_by ставим здесь,
        company/branch уже обрабатывает _save_with_company_branch из миксина.
        """
        extra = {}
        user = getattr(self.request, "user", None)
        if user and user.is_authenticated:
            extra["created_by"] = user

        self._save_with_company_branch(serializer, **extra)


class LogisticsDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/logistics/<uuid:pk>/   -> одна запись
    PATCH  /api/logistics/<uuid:pk>/   -> обновление
    DELETE /api/logistics/<uuid:pk>/   -> удаление
    """

    queryset = (
        Logistics.objects
        .select_related("company", "branch", "client", "created_by")
        .all()
    )
    serializer_class = LogisticsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_update(self, serializer):
        self._save_with_company_branch(serializer)


class LogisticsAnalyticsView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/logistics/analytics/

    Возвращает агрегаты по статусам:
    - оформлен
    - в пути
    - завершен

    Учитывает company/branch через CompanyBranchRestrictedMixin.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Logistics.objects.all()
        return self._filter_qs_company_branch(qs)

    def get(self, request, *args, **kwargs):
        qs = self.get_queryset()

        # ?date_from=2025-01-01&date_to=2025-01-31 (если нужно)
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        total_price_expr = ExpressionWrapper(
            F("price_car") + F("price_service"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        aggregated = (
            qs.values("status")
            .annotate(
                orders=Count("id"),
                amount=Sum(total_price_expr),
            )
        )

        data = {
            Logistics.Status.DECORATED: {
                "status": Logistics.Status.DECORATED,
                "status_display": Logistics.Status.DECORATED.label,
                "orders": 0,
                "amount": Decimal("0.00"),
            },
            Logistics.Status.TRANSIT: {
                "status": Logistics.Status.TRANSIT,
                "status_display": Logistics.Status.TRANSIT.label,
                "orders": 0,
                "amount": Decimal("0.00"),
            },
            Logistics.Status.COMPLETED: {
                "status": Logistics.Status.COMPLETED,
                "status_display": Logistics.Status.COMPLETED.label,
                "orders": 0,
                "amount": Decimal("0.00"),
            },
        }

        for row in aggregated:
            status = row["status"]
            if status in data:
                data[status]["orders"] = row["orders"] or 0
                data[status]["amount"] = row["amount"] or Decimal("0.00")

        total_orders = sum(item["orders"] for item in data.values())
        total_amount = sum(item["amount"] for item in data.values())

        return Response(
            {
                "items": list(data.values()),
                "total": {
                    "orders": total_orders,
                    "amount": total_amount,
                },
            }
        )
