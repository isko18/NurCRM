"""Аутентификация, формат ошибок и лимиты API приложения клиентов (/api/v1/)."""
import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from .models import AppToken, hash_secret

logger = logging.getLogger("clientapp")

TOKEN_IDLE_DAYS = 180
LAST_USED_TOUCH_SECONDS = 300


# ----------------------------------------------------------------------
# Ошибки: { "error": "код", "message": "текст" }
# ----------------------------------------------------------------------


class AppAPIError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Ошибка запроса."
    default_code = "bad_request"

    def __init__(self, code: str, message: str, http_status: int = 400, extra: dict = None):
        self.status_code = http_status
        self.error_code = code
        self.extra = extra or {}
        super().__init__(detail=message, code=code)


def _first_message(detail):
    if isinstance(detail, dict):
        for value in detail.values():
            msg = _first_message(value)
            if msg:
                return msg
        return ""
    if isinstance(detail, (list, tuple)):
        for value in detail:
            msg = _first_message(value)
            if msg:
                return msg
        return ""
    return str(detail)


_CODE_BY_CLASS = (
    (exceptions.Throttled, "rate_limited"),
    (exceptions.NotAuthenticated, "unauthorized"),
    (exceptions.AuthenticationFailed, "unauthorized"),
    (exceptions.PermissionDenied, "forbidden"),
    (exceptions.NotFound, "not_found"),
    (exceptions.MethodNotAllowed, "method_not_allowed"),
    (exceptions.UnsupportedMediaType, "unsupported_media_type"),
    (exceptions.ParseError, "bad_json"),
    (exceptions.ValidationError, "validation_error"),
)


def app_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, AppAPIError):
        body = {"error": exc.error_code, "message": str(exc.detail), **exc.extra}
    else:
        code = "error"
        for cls, name in _CODE_BY_CLASS:
            if isinstance(exc, cls):
                code = name
                break
        if code == "error" and response.status_code == 404:
            code = "not_found"
        detail = getattr(exc, "detail", response.data)
        message = _first_message(detail) or "Ошибка запроса."
        body = {"error": code, "message": message}
        if isinstance(exc, exceptions.ValidationError) and isinstance(detail, dict):
            body["fields"] = {k: _first_message(v) for k, v in detail.items()}
        if isinstance(exc, exceptions.Throttled):
            body["message"] = "Слишком много запросов. Попробуйте позже."
            if exc.wait:
                body["retryAfter"] = int(exc.wait)
    response.data = body
    return response


# ----------------------------------------------------------------------
# Аутентификация: Authorization: Bearer nca_… (не JWT сотрудников)
# ----------------------------------------------------------------------


class AppTokenAuthentication(BaseAuthentication):
    keyword = b"bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword:
            return None
        if len(auth) != 2:
            raise exceptions.AuthenticationFailed("Неверный заголовок Authorization.")
        try:
            raw = auth[1].decode("ascii")
        except UnicodeError:
            raise exceptions.AuthenticationFailed("Неверный токен.")
        if not raw.startswith(AppToken.PREFIX):
            raise exceptions.AuthenticationFailed("Неверный токен.")
        token = (
            AppToken.objects.select_related("customer")
            .filter(token_hash=hash_secret(raw), revoked_at__isnull=True)
            .first()
        )
        if token is None or token.customer.deleted_at is not None:
            raise exceptions.AuthenticationFailed("Токен недействителен. Войдите заново.")
        now = timezone.now()
        last = token.last_used_at or token.created_at
        if last and now - last > timedelta(days=TOKEN_IDLE_DAYS):
            AppToken.objects.filter(pk=token.pk).update(revoked_at=now)
            raise exceptions.AuthenticationFailed("Токен истёк. Войдите заново.")
        if token.last_used_at is None or (now - token.last_used_at).total_seconds() > LAST_USED_TOUCH_SECONDS:
            AppToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return token.customer, token

    def authenticate_header(self, request):
        return 'Bearer realm="nurcrm-app"'


class IsAppCustomer(BasePermission):
    def has_permission(self, request, view):
        from .models import AppCustomer

        return isinstance(getattr(request, "user", None), AppCustomer) and request.user.deleted_at is None


# ----------------------------------------------------------------------
# Лимиты
# ----------------------------------------------------------------------


class _ScopedThrottle(SimpleRateThrottle):
    def get_rate(self):
        return self.rate

    def ident_for(self, request, view):
        return self.get_ident(request)

    def get_cache_key(self, request, view):
        ident = self.ident_for(request, view)
        if not ident:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ShopsThrottle(_ScopedThrottle):
    scope = "capp_shops"
    rate = "120/min"


class AuthStartIPThrottle(_ScopedThrottle):
    scope = "capp_auth_start_ip"
    rate = "10/10m"

    def parse_rate(self, rate):
        # DRF понимает только 1 единицу периода; "10/10m" → 10 запросов за 600 с
        num, period = rate.split("/")
        if period.endswith("m") and period[:-1].isdigit():
            return int(num), int(period[:-1]) * 60
        return super().parse_rate(rate)


class AuthStatusNonceThrottle(_ScopedThrottle):
    scope = "capp_auth_status_nonce"
    rate = "90/min"

    def ident_for(self, request, view):
        nonce = (request.query_params.get("nonce") or "")[:64]
        return hash_secret(nonce)[:32] if nonce else self.get_ident(request)


class AuthStatusIPThrottle(_ScopedThrottle):
    scope = "capp_auth_status_ip"
    rate = "600/min"


class CustomerThrottle(_ScopedThrottle):
    scope = "capp_customer"
    rate = "300/min"

    def ident_for(self, request, view):
        user = getattr(request, "user", None)
        return str(getattr(user, "pk", "") or "") or self.get_ident(request)


class ReferralApplyThrottle(CustomerThrottle):
    scope = "capp_referral_apply"
    rate = "5/hour"


class QrTokenThrottle(CustomerThrottle):
    scope = "capp_qr_token"
    rate = "30/min"


class WebhookThrottle(_ScopedThrottle):
    scope = "capp_tg_webhook"
    rate = "1800/min"

    def ident_for(self, request, view):
        return "global"


# ----------------------------------------------------------------------
# Базовые вьюхи
# ----------------------------------------------------------------------


class AppAPIView(APIView):
    """Вьюха API приложения: ошибки в формате приложения, токен приложения, лимит на клиента."""

    authentication_classes = [AppTokenAuthentication]
    permission_classes = [IsAppCustomer]
    throttle_classes = [CustomerThrottle]

    def get_exception_handler(self):
        return app_exception_handler


class PublicAppAPIView(AppAPIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ShopsThrottle]


def error_response(code: str, message: str, http_status: int, **extra):
    return Response({"error": code, "message": message, **extra}, status=http_status)
