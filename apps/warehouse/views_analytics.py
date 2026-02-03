from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from apps.utils import _is_owner_like
from apps.warehouse.analytics import (
    _parse_period,
    build_agent_warehouse_analytics_payload,
    build_owner_warehouse_analytics_payload,
)
from apps.warehouse.views import CompanyBranchRestrictedMixin
from apps.users.models import User


class WarehouseAgentMyAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/agents/me/analytics/
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        company = self._company()
        branch = self._auto_branch()
        if not company:
            raise PermissionDenied("Компания не найдена.")

        period = _parse_period(request)
        data = build_agent_warehouse_analytics_payload(
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            agent_id=str(user.id),
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
            group_by=period["group_by"],
        )
        return Response(data)


class WarehouseOwnerAgentAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/owner/agents/<agent_id>/analytics/
    """
    def get(self, request, agent_id, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            raise PermissionDenied("Только владелец/админ.")

        company = self._company()
        branch = self._auto_branch()
        if not company:
            raise PermissionDenied("Компания не найдена.")

        agent = User.objects.filter(id=agent_id, company=company).first()
        if not agent:
            raise PermissionDenied("Агент не найден в компании.")

        period = _parse_period(request)
        data = build_agent_warehouse_analytics_payload(
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            agent_id=str(agent.id),
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
            group_by=period["group_by"],
        )
        return Response(data)


class WarehouseOwnerOverallAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/owner/analytics/
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        if not _is_owner_like(user):
            raise PermissionDenied("Только владелец/админ.")

        company = self._company()
        branch = self._auto_branch()
        if not company:
            raise PermissionDenied("Компания не найдена.")

        period = _parse_period(request)
        data = build_owner_warehouse_analytics_payload(
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
            group_by=period["group_by"],
        )
        return Response(data)
