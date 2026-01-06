# apps/logistics/views.py

from decimal import Decimal

from django.db.models import (
    Sum,
    Count,
    F,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce, TruncDate
from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Logistics
from .serializers import LogisticsSerializer

from apps.main.views import CompanyBranchRestrictedMixin
from apps.main.serializers import _company_from_ctx, _active_branch

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


class LogisticsAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = _company_from_ctx(self)
        branch = _active_branch(self)

        qs = Logistics.objects.all()

        # Ограничение по компании/филиалу (как у тебя в сериализаторе)
        if company:
            qs = qs.filter(company=company)
        if branch:
            qs = qs.filter(branch=branch)

        # Доп. фильтры (по желанию)
        status = request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)

        date_from = request.query_params.get("date_from")  # YYYY-MM-DD
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = request.query_params.get("date_to")  # YYYY-MM-DD
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # -----------------------------
        # TOTALS (Все заказы)
        # -----------------------------
        totals = qs.aggregate(
            total_orders=Coalesce(Count("id"), 0),
            total_revenue=Coalesce(Sum("revenue"), 0),
            total_service=Coalesce(Sum("price_service"), 0),
            total_car=Coalesce(Sum("price_car"), 0),
            total_sale=Coalesce(Sum("sale_price"), 0),
        )

        # -----------------------------
        # BY STATUS
        # -----------------------------
        by_status_raw = list(
            qs.values("status")
            .annotate(
                orders=Coalesce(Count("id"), 0),
                revenue=Coalesce(Sum("revenue"), 0),
                service=Coalesce(Sum("price_service"), 0),
                car=Coalesce(Sum("price_car"), 0),
                sale=Coalesce(Sum("sale_price"), 0),
            )
            .order_by("status")
        )

        # Чтобы UI мог рисовать красиво (display)
        status_display_map = dict(Logistics.Status.choices)
        by_status = [
            {
                "status": row["status"],
                "status_display": status_display_map.get(row["status"], row["status"]),
                "orders": row["orders"],
                "revenue": row["revenue"],
                "service": row["service"],
                "car": row["car"],
                "sale": row["sale"],
            }
            for row in by_status_raw
        ]

        # -----------------------------
        # BY ARRIVAL DATE (Заказы по датам прибытия)
        # -----------------------------
        by_arrival_date = list(
            qs.exclude(arrival_date__isnull=True)
            .annotate(day=TruncDate("arrival_date"))
            .values("day")
            .annotate(orders=Coalesce(Count("id"), 0))
            .order_by("day")
        )

        # -----------------------------
        # CHARTS (готовые структуры)
        # -----------------------------
        charts = {
            "orders_by_status": [
                {"name": x["status_display"], "value": x["orders"], "status": x["status"]}
                for x in by_status
            ],
            "service_by_status": [
                {"name": x["status_display"], "value": x["service"], "status": x["status"]}
                for x in by_status
            ],
            "revenue_by_status": [
                {"name": x["status_display"], "value": x["revenue"], "status": x["status"]}
                for x in by_status
            ],
            "orders_by_arrival_date": [
                {"date": str(x["day"]), "value": x["orders"]}
                for x in by_arrival_date
            ],
        }

        # -----------------------------
        # RESPONSE (под твой UI)
        # -----------------------------
        return Response(
            {
                "totals": totals,
                "cards": {
                    # Можно прямо этим кормить карточки
                    "all": {
                        "title": "Все заказы",
                        "orders": totals["total_orders"],
                        "revenue": totals["total_revenue"],
                        "service": totals["total_service"],
                    },
                    "by_status": [
                        {
                            "title": x["status_display"],
                            "orders": x["orders"],
                            "revenue": x["revenue"],
                            "service": x["service"],
                        }
                        for x in by_status
                    ],
                },
                "tables": {
                    "by_status": by_status,
                    "by_arrival_date": by_arrival_date,
                },
                "charts": charts,
            }
        )