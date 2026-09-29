"""
Единый формат ошибок API (ТЗ-BE-2026-02, п. 2.1 и 2.4): тело — JSON с "detail" и "code".
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

STATUS_CODES = {
    400: "invalid",
    401: "not_authenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
}


def _django_validation_detail(exc: DjangoValidationError):
    if hasattr(exc, "error_dict"):
        return {field: [str(m) for m in msgs] for field, msgs in exc.message_dict.items()}
    return {"detail": " ".join(str(m) for m in exc.messages)}


def api_exception_handler(exc, context):
    # Ошибка правил модели (full_clean) — это ошибка данных, а не сбой сервера: 400, не 500.
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(_django_validation_detail(exc))

    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(response.data, dict):
        return response

    data = response.data
    # Ошибки по полям ({"field": [...]}) оставляем как есть: сайт показывает их ключи как поля.
    if "detail" in data and "code" not in data:
        code = getattr(data["detail"], "code", None)
        if not code or (code == "invalid" and response.status_code != status.HTTP_400_BAD_REQUEST):
            code = STATUS_CODES.get(response.status_code, "error")
        data["code"] = code
    return response
