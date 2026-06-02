from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from apps.utils import _is_owner_like
from apps.users.models import Branch, Company, User
from apps.warehouse import models as wm
from apps.warehouse.analytics import (
    _parse_period,
    build_agent_warehouse_analytics_payload,
    build_owner_agents_sales_analytics_payload,
    build_owner_partners_warehouse_analytics_list_payload,
    build_owner_partner_warehouse_analytics_payload,
    build_owner_warehouse_analytics_payload,
)
from apps.warehouse.views import CompanyBranchRestrictedMixin


def _resolve_partner_branch_scope(request, partner_company):
    """
    partner_branch=<uuid> — один филиал партнёра;
    без параметра — вся компания-партнёр (все филиалы).
    """
    branch_id = (request.query_params.get("partner_branch") or "").strip()
    if not branch_id:
        return None, True
    try:
        branch = Branch.objects.get(id=branch_id, company=partner_company)
    except (Branch.DoesNotExist, ValueError):
        raise PermissionDenied("Филиал партнёра не найден.")
    return branch, False


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


class WarehouseOwnerAgentsSalesAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/owner/agents/analytics/
    Сводная аналитика по агентам (продажи) за период.
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

        def _int(name: str, default: int):
            v = request.query_params.get(name)
            if v is None or v == "":
                return default
            try:
                return int(v)
            except Exception:
                return default

        limit = max(1, min(_int("limit", 200), 1000))
        offset = max(0, _int("offset", 0))
        order_by = (request.query_params.get("order_by") or "sales_amount").strip()

        data = build_owner_agents_sales_analytics_payload(
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
            group_by=period["group_by"],
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
        return Response(data)


class WarehouseOwnerPartnersAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/owner/partners/analytics/
    Сводная аналитика по всем компаниям-партнёрам (складское партнёрство).
    """

    def get(self, request, *args, **kwargs):
        if not _is_owner_like(request.user):
            raise PermissionDenied("Только владелец/админ.")

        company = self._company()
        if not company:
            raise PermissionDenied("Компания не найдена.")

        period = _parse_period(request)
        data = build_owner_partners_warehouse_analytics_list_payload(
            owner_company_id=str(company.id),
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
        )
        return Response(data)


class WarehouseOwnerPartnerAnalyticsAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET /api/warehouse/owner/partners/<partner_company_id>/analytics/
    Полная аналитика одной компании-партнёра (как owner/analytics, но по данным партнёра).
    Query: partner_branch=<uuid> — ограничить филиалом партнёра; иначе все филиалы.
    """

    def get(self, request, partner_company_id, *args, **kwargs):
        if not _is_owner_like(request.user):
            raise PermissionDenied("Только владелец/админ.")

        company = self._company()
        if not company:
            raise PermissionDenied("Компания не найдена.")

        if str(partner_company_id) == str(company.id):
            raise PermissionDenied("Укажите компанию-партнёра, не свою.")

        if not wm.has_active_stock_partnership_between_ids(company.id, partner_company_id):
            raise PermissionDenied("Нет активного партнёрства с этой компанией.")

        partner = Company.objects.filter(pk=partner_company_id).first()
        if not partner:
            raise PermissionDenied("Компания-партнёр не найдена.")

        partner_branch, all_branches = _resolve_partner_branch_scope(request, partner)
        period = _parse_period(request)
        group_by = (request.query_params.get("group_by") or period.get("group_by") or "day").strip()

        data = build_owner_partner_warehouse_analytics_payload(
            owner_company_id=str(company.id),
            partner_company_id=str(partner.id),
            branch_id=str(partner_branch.id) if partner_branch else None,
            period=period["period"],
            date_from=period["date_from"],
            date_to=period["date_to"],
            group_by=group_by,
            all_branches=all_branches,
        )
        return Response(data)
