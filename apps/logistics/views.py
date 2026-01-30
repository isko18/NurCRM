# apps/logistics/views.py

from decimal import Decimal

from django.db.models import (
    Sum,
    Count,
    F,
    DecimalField,
    Value,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce
from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.users.models import Company, Branch
from rest_framework.exceptions import ValidationError
from .models import Logistics, LogisticsExpense
from .serializers import LogisticsSerializer, LogisticsExpenseSerializer

from apps.main.views import CompanyBranchRestrictedMixin

DECIMAL_OUT = DecimalField(max_digits=12, decimal_places=2)
ZERO_DEC = Value(Decimal("0.00"), output_field=DECIMAL_OUT)

def get_company_from_request(request):
    company_id = request.query_params.get("company")
    if not company_id:
        return None
    try:
        return Company.objects.get(id=company_id)
    except Company.DoesNotExist:
        raise ValidationError({"company": "Компания не найдена."})


def get_branch_from_request(request, company=None):
    branch_id = request.query_params.get("branch")
    if not branch_id:
        return None
    qs = Branch.objects.all()
    if company:
        qs = qs.filter(company=company)
    try:
        return qs.get(id=branch_id)
    except Branch.DoesNotExist:
        raise ValidationError({"branch": "Филиал не найден или не принадлежит компании."})

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


class LogisticsExpenseListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/logistics/expenses/  -> список расходов (company/branch из миксина)
    POST /api/logistics/expenses/  -> создать расход (поля: name, amount)
    """

    serializer_class = LogisticsExpenseSerializer
    permission_classes = [permissions.IsAuthenticated]

    queryset = (
        LogisticsExpense.objects
        .select_related("company", "branch", "created_by")
        .all()
    )

    def get_queryset(self):
        return self._filter_qs_company_branch(super().get_queryset())

    def perform_create(self, serializer):
        extra = {}
        user = getattr(self.request, "user", None)
        if user and user.is_authenticated:
            extra["created_by"] = user
        self._save_with_company_branch(serializer, **extra)


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
        company = get_company_from_request(request)
        branch = get_branch_from_request(request, company=company)

        qs = Logistics.objects.all()

        if company:
            qs = qs.filter(company=company)
        if branch:
            qs = qs.filter(branch=branch)

        status = request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)

        date_from = request.query_params.get("date_from")  # YYYY-MM-DD
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = request.query_params.get("date_to")  # YYYY-MM-DD
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # Расходы (минусуются из карточки "Все заказы")
        exp_qs = LogisticsExpense.objects.all()
        if company:
            exp_qs = exp_qs.filter(company=company)
        if branch:
            exp_qs = exp_qs.filter(branch=branch)
        if date_from:
            exp_qs = exp_qs.filter(created_at__date__gte=date_from)
        if date_to:
            exp_qs = exp_qs.filter(created_at__date__lte=date_to)

        total_expenses = exp_qs.aggregate(
            total_expenses=Coalesce(Sum("amount"), ZERO_DEC, output_field=DECIMAL_OUT),
        )["total_expenses"]

        totals = qs.aggregate(
            total_orders=Coalesce(Count("id"), 0),
            total_revenue=Coalesce(Sum("revenue"), ZERO_DEC, output_field=DECIMAL_OUT),
            total_service=Coalesce(Sum("price_service"), ZERO_DEC, output_field=DECIMAL_OUT),
            total_car=Coalesce(Sum("price_car"), ZERO_DEC, output_field=DECIMAL_OUT),
            total_sale=Coalesce(Sum("sale_price"), ZERO_DEC, output_field=DECIMAL_OUT),
        )

        # net_revenue = прибыль по логистике - расходы
        net_revenue = (totals.get("total_revenue") or Decimal("0.00")) - (total_expenses or Decimal("0.00"))
        totals["total_expenses"] = total_expenses
        totals["net_revenue"] = net_revenue

        by_status_raw = list(
            qs.values("status")
            .annotate(
                orders=Coalesce(Count("id"), 0),
                revenue=Coalesce(Sum("revenue"), ZERO_DEC, output_field=DECIMAL_OUT),
                service=Coalesce(Sum("price_service"), ZERO_DEC, output_field=DECIMAL_OUT),
                car=Coalesce(Sum("price_car"), ZERO_DEC, output_field=DECIMAL_OUT),
                sale=Coalesce(Sum("sale_price"), ZERO_DEC, output_field=DECIMAL_OUT),
            )
            .order_by("status")
        )

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

        # arrival_date в модели — CharField, поэтому TruncDate использовать нельзя.
        # Группируем по строковому значению (обычно хранится как YYYY-MM-DD).
        by_arrival_date = list(
            qs.exclude(arrival_date__isnull=True)
            .exclude(arrival_date="")
            .values(day=F("arrival_date"))
            .annotate(orders=Coalesce(Count("id"), 0))
            .order_by("day")
        )

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

        return Response(
            {
                "totals": totals,
                "cards": {
                    "all": {
                        "title": "Все заказы",
                        "orders": totals["total_orders"],
                        "revenue": net_revenue,
                        "service": totals["total_service"],
                        "expenses": total_expenses,
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