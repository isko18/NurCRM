"""
Вьюхи раздела «Сводка» (Сводки продаж).

Эндпоинты:
- GET    /warehouse/summaries/                  — список (облегчённые карточки)
- POST   /warehouse/summaries/                  — создание (+ снапшот)
- GET    /warehouse/summaries/{id}/             — полный объект
- PATCH  /warehouse/summaries/{id}/             — обновление (пересборка при смене type/agents)
- DELETE /warehouse/summaries/{id}/             — удаление
- GET    /warehouse/summaries/calendar/?month=  — агрегат по дням месяца
- POST   /warehouse/summaries/{id}/regenerate/  — пересборка снапшота
"""

from datetime import datetime

from django.db.models import Count, Prefetch
from django.db.models.functions import TruncDate
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from . import models
from .views import CompanyBranchRestrictedMixin
from .serializers_summaries import (
    SummaryListSerializer,
    SummaryDetailSerializer,
    SummaryWriteSerializer,
)
from .services_summaries import build_summary_snapshot, next_summary_number
from apps.utils import _is_owner_like


class SummaryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _can_manage_summaries(user) -> bool:
    """Создавать/менять/удалять сводки могут владелец/админ и сотрудники компании, но не чистые агенты."""
    if _is_owner_like(user):
        return True
    return bool(getattr(user, "company_id", None))


class WarehouseSalesSummaryViewSet(CompanyBranchRestrictedMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = SummaryPagination
    lookup_field = "pk"

    # ------------------------------------------------------------------ queryset
    def _base_queryset(self):
        qs = (
            models.WarehouseSalesSummary.objects
            .select_related("warehouse", "created_by")
            .prefetch_related("agents")
        )
        return self._filter_qs_company_branch(qs)

    def get_queryset(self):
        qs = self._base_queryset()
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                Prefetch(
                    "documents",
                    queryset=models.WarehouseSalesSummaryDocument.objects.prefetch_related("items"),
                ),
                Prefetch("products", queryset=models.WarehouseSalesSummaryProduct.objects.all()),
            )
        if self.action == "list":
            qs = self._apply_list_filters(qs)
        return qs

    def _apply_list_filters(self, qs):
        params = self.request.query_params

        date = params.get("date")
        if date:
            qs = qs.filter(date=date)
        date_from = params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        agent = params.getlist("agent") or []
        if len(agent) == 1 and "," in agent[0]:
            agent = [a.strip() for a in agent[0].split(",") if a.strip()]
        if agent:
            qs = qs.filter(agents__id__in=agent).distinct()

        author = params.get("author")
        if author:
            qs = qs.filter(created_by_id=author)

        summary_type = params.get("type")
        if summary_type:
            qs = qs.filter(type=summary_type)

        search = params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)

        ordering = params.get("ordering")
        allowed_ordering = {"date", "-date", "created_at", "-created_at", "name", "-name"}
        if ordering in allowed_ordering:
            qs = qs.order_by(ordering)

        return qs

    # ------------------------------------------------------------------ serializers
    def get_serializer_class(self):
        if self.action == "list":
            return SummaryListSerializer
        if self.action in ("create", "update", "partial_update"):
            return SummaryWriteSerializer
        return SummaryDetailSerializer

    def _detail_response(self, summary, status_code=status.HTTP_200_OK):
        summary = (
            self.get_queryset().model.objects
            .select_related("warehouse", "created_by")
            .prefetch_related("agents", "documents__items", "products")
            .get(pk=summary.pk)
        )
        serializer = SummaryDetailSerializer(summary, context=self.get_serializer_context())
        return Response(serializer.data, status=status_code)

    # ------------------------------------------------------------------ create / update / delete
    def create(self, request, *args, **kwargs):
        if not _can_manage_summaries(request.user):
            raise PermissionDenied("Недостаточно прав для создания сводки.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        company = self._company()
        if company is None:
            raise ValidationError("Не удалось определить компанию пользователя.")
        warehouse = serializer.validated_data.get("warehouse")
        self._ensure_agent_can_access_warehouse(warehouse, field_name="warehouse")
        if warehouse is not None and warehouse.company_id != company.id:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании."})

        summary = serializer.save(
            company=company,
            branch=self._auto_branch(),
            created_by=request.user,
            number=next_summary_number(company),
        )
        build_summary_snapshot(summary)
        return self._detail_response(summary, status_code=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not _can_manage_summaries(request.user):
            raise PermissionDenied("Недостаточно прав для изменения сводки.")
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        prev_type = instance.type
        prev_agents = set(instance.agents.values_list("id", flat=True))

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        summary = serializer.save()

        new_agents = set(summary.agents.values_list("id", flat=True))
        if summary.type != prev_type or new_agents != prev_agents:
            build_summary_snapshot(summary)
        return self._detail_response(summary)

    def destroy(self, request, *args, **kwargs):
        if not _can_manage_summaries(request.user):
            raise PermissionDenied("Недостаточно прав для удаления сводки.")
        return super().destroy(request, *args, **kwargs)

    # ------------------------------------------------------------------ extra actions
    @action(detail=True, methods=["post"])
    def regenerate(self, request, *args, **kwargs):
        if not _can_manage_summaries(request.user):
            raise PermissionDenied("Недостаточно прав для пересборки сводки.")
        summary = self.get_object()
        build_summary_snapshot(summary)
        return self._detail_response(summary)

    @action(detail=False, methods=["get"])
    def calendar(self, request, *args, **kwargs):
        month = request.query_params.get("month")
        if not month:
            raise ValidationError({"month": "Укажите месяц в формате YYYY-MM."})
        try:
            month_date = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise ValidationError({"month": "Некорректный формат. Ожидается YYYY-MM."})

        qs = self._base_queryset().filter(
            date__year=month_date.year, date__month=month_date.month,
        )
        days = (
            qs.annotate(day=TruncDate("date"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("-day")
        )
        return Response({
            "month": month,
            "days": [
                {"date": row["day"].isoformat(), "count": row["count"]}
                for row in days
            ],
        })
