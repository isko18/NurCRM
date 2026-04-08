from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ekassa.exceptions import EkassaAPIError, EkassaConfigurationError
from apps.ekassa.serializers import (
    EkassaIntegrationReadSerializer,
    EkassaIntegrationWriteSerializer,
    default_settings_payload,
)
from apps.ekassa.services import client_for, get_integration, inject_fiscal_number
from apps.users.permissions import IsCompanyOwnerOrAdmin

logger = logging.getLogger(__name__)


def _resolve_company(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    c = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if c:
        return c
    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)
    return None


def _ekassa_timeout() -> int:
    return int(getattr(settings, "EKASSA_REQUEST_TIMEOUT", 45) or 45)


def _disabled_response():
    return Response(
        {
            "detail": "Интеграция eKassa выключена или не настроена.",
            "code": "ekassa_disabled",
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _handle_ekassa_error(exc: EkassaAPIError):
    payload = {"detail": str(exc), "code": "ekassa_upstream"}
    if exc.payload is not None:
        payload["ekassa"] = exc.payload
    code = exc.status_code if exc.status_code and exc.status_code < 500 else status.HTTP_502_BAD_GATEWAY
    return Response(payload, status=code)


class EkassaSettingsView(APIView):
    """
    GET — текущие настройки (без пароля).
    PATCH — обновление; пароль передавайте только при смене.
    """

    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get(self, request):
        company = _resolve_company(request.user)
        if not company:
            return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
        obj = get_integration(company)
        if obj is None:
            return Response(default_settings_payload())
        return Response(EkassaIntegrationReadSerializer(obj).data)

    def patch(self, request):
        company = _resolve_company(request.user)
        if not company:
            return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
        obj = get_integration(company)
        if obj is None:
            ser = EkassaIntegrationWriteSerializer(data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save(company=company)
            obj = get_integration(company)
            return Response(EkassaIntegrationReadSerializer(obj).data)
        ser = EkassaIntegrationWriteSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        obj.refresh_from_db()
        return Response(EkassaIntegrationReadSerializer(obj).data)


class EkassaPingView(APIView):
    """
    Проверка доступности хоста eKassa (/api/ping).
    Не требует включённой интеграции — используется URL из настроек компании или дефолт из settings.
    """

    permission_classes = [IsAuthenticated, IsCompanyOwnerOrAdmin]

    def get(self, request):
        company = _resolve_company(request.user)
        if not company:
            return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
        cfg = get_integration(company)
        base = ""
        if cfg:
            base = cfg.effective_base_url()
        if not base:
            base = (getattr(settings, "EKASSA_DEFAULT_BASE_URL", "") or "").strip().rstrip("/")
        if not base:
            return Response({"detail": "Не задан базовый URL eKassa."}, status=500)
        url = f"{base}/api/ping"
        try:
            r = requests.get(url, timeout=_ekassa_timeout())
        except requests.RequestException as e:
            logger.warning("eKassa ping failed: %s", e)
            return Response({"detail": "Не удалось связаться с eKassa.", "error": str(e)}, status=502)
        return Response({"url": url, "http_status": r.status_code, "body": r.text[:2000]})


def _proxy_post(request, path: str, body: dict[str, Any] | None = None):
    company = _resolve_company(request.user)
    if not company:
        return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
    try:
        cli = client_for(company)
    except EkassaConfigurationError:
        return _disabled_response()
    cfg = get_integration(company)
    fiscal = cfg.fiscal_number.strip() if cfg else ""
    payload = inject_fiscal_number(body, fiscal)
    try:
        data = cli.request_json("POST", path, json_body=payload)
    except EkassaAPIError as e:
        return _handle_ekassa_error(e)
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_pos(request):
    """POST /api/get_pos_by_fiscal_number"""
    return _proxy_post(request, "/api/get_pos_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_catalogue(request):
    """POST /api/get_base_catalogue_by_fiscal_number"""
    return _proxy_post(request, "/api/get_base_catalogue_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_shift_state(request):
    return _proxy_post(request, "/api/shift_state_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_shift_open(request):
    return _proxy_post(request, "/api/shift_open_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_shift_close(request):
    return _proxy_post(request, "/api/shift_close_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_shift_control(request):
    return _proxy_post(request, "/api/shift_control_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_cash_operation(request):
    return _proxy_post(request, "/api/cash_operation_by_fiscal_number", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_receipt(request):
    """POST /api/v2/receipt — новый чек или переотправка (см. документацию)."""
    return _proxy_post(request, "/api/v2/receipt", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_duplicate(request):
    return _proxy_post(request, "/api/duplicate", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_customers(request):
    return _proxy_post(request, "/api/customers", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_xpay_getqr(request):
    return _proxy_post(request, "/api/xpay/getqr", request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ekassa_xpay_status(request):
    return _proxy_post(request, "/api/xpay/status", request.data)


def _proxy_get(request, path: str):
    company = _resolve_company(request.user)
    if not company:
        return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
    try:
        cli = client_for(company)
    except EkassaConfigurationError:
        return _disabled_response()
    try:
        data = cli.request_json("GET", path)
    except EkassaAPIError as e:
        return _handle_ekassa_error(e)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_tax_systems(request):
    return _proxy_get(request, "/api/info/tax-systems")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_tax_rates(request):
    return _proxy_get(request, "/api/info/tax-rates")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_gns_departments(request):
    return _proxy_get(request, "/api/info/gns-departments")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_entrepreneurship_objects(request):
    return _proxy_get(request, "/api/info/entrepreneurship-objects")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_calc_item_attributes(request):
    return _proxy_get(request, "/api/info/calc-item-attributes")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_info_business_activities(request):
    return _proxy_get(request, "/api/info/business-activities")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ekassa_logout(request):
    """Завершить сессию токена на стороне eKassa (документация: GET /api/logout)."""
    company = _resolve_company(request.user)
    if not company:
        return Response({"detail": "Компания для текущего пользователя не найдена."}, status=404)
    try:
        cli = client_for(company)
    except EkassaConfigurationError:
        return _disabled_response()
    try:
        data = cli.request_json("GET", "/api/logout")
    except EkassaAPIError as e:
        return _handle_ekassa_error(e)
    finally:
        cli.invalidate_token()
    return Response(data)
