"""
BE2-11: Idempotency-Key для операций, которые касса повторяет после сбоя связи.

Повтор с тем же ключом в течение 7 суток → первый ответ + заголовок Idempotent-Replayed: true.
Тот же ключ с другим запросом → 409 idempotency_conflict; пока первый запрос выполняется → 409 in_progress.
Неуспешный ответ (4xx/5xx) не запоминается: ошибку можно исправить и повторить с тем же ключом.
Без заголовка адрес работает как раньше.
"""
import functools
import hashlib
import json
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from apps.integrations.models import IdempotencyRecord

TTL = timedelta(days=7)
MAX_KEY_LENGTH = 128


def _body_hash(request) -> str:
    try:
        raw = json.dumps(request.data, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        raw = repr(request.data)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _error(detail, code, http_status, **headers):
    resp = Response({"detail": detail, "code": code}, status=http_status)
    for k, v in headers.items():
        resp[k] = v
    return resp


def idempotent(handler):
    """Декоратор метода APIView (post/patch/put/delete)."""

    @functools.wraps(handler)
    def wrapper(view, request, *args, **kwargs):
        key = (request.headers.get("Idempotency-Key") or "").strip()
        company_id = getattr(getattr(request, "user", None), "company_id", None)
        if not key or company_id is None:
            return handler(view, request, *args, **kwargs)
        if len(key) > MAX_KEY_LENGTH:
            return _error("Idempotency-Key не длиннее 128 символов.", "invalid", status.HTTP_400_BAD_REQUEST)

        scope = f"{request.method} {request.path}"[:255]
        body_hash = _body_hash(request)
        IdempotencyRecord.objects.filter(
            company_id=company_id, key=key, created_at__lt=timezone.now() - TTL
        ).delete()
        try:
            with transaction.atomic():
                record, created = IdempotencyRecord.objects.get_or_create(
                    company_id=company_id, key=key, defaults={"scope": scope, "body_hash": body_hash}
                )
        except IntegrityError:
            record, created = IdempotencyRecord.objects.get(company_id=company_id, key=key), False

        if not created:
            if record.scope != scope or record.body_hash != body_hash:
                return _error(
                    "Этот Idempotency-Key уже использован для другого запроса.",
                    "idempotency_conflict", status.HTTP_409_CONFLICT,
                )
            if record.state != IdempotencyRecord.State.DONE:
                return _error(
                    "Запрос с этим Idempotency-Key ещё выполняется.",
                    "in_progress", status.HTTP_409_CONFLICT, **{"Retry-After": "1"},
                )
            resp = Response(record.response_body, status=record.status_code)
            resp["Idempotent-Replayed"] = "true"
            return resp

        try:
            response = handler(view, request, *args, **kwargs)
        except Exception:
            record.delete()
            raise
        if 200 <= response.status_code < 300 and hasattr(response, "data"):
            record.state = IdempotencyRecord.State.DONE
            record.status_code = response.status_code
            # Тем же кодировщиком, что и рендерер DRF, — повтор отдаёт байт-в-байт тот же JSON.
            record.response_body = json.loads(json.dumps(response.data, cls=JSONEncoder))
            record.save(update_fields=["state", "status_code", "response_body"])
        else:
            record.delete()
        return response

    return wrapper


def purge_expired() -> int:
    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=timezone.now() - TTL).delete()
    return deleted
