from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

from apps.main.serializers import ProductSerializer

logger = logging.getLogger("crm.webhooks")

# Предохранитель: когда приёмная сторона лежит, каждая отправка стоит
# retries * timeout + backoff (по умолчанию ~17.5 с). Раньше это висело прямо
# в запросе на кассе. Теперь отправка уходит в Celery, а этот счётчик не даёт
# воркерам молотить в мёртвый хост.
_BREAKER_KEY = "product_webhook:consecutive_failures"
_BREAKER_OPEN_KEY = "product_webhook:open_until"
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN = 600  # 10 минут


def _breaker_is_open() -> bool:
    try:
        return bool(cache.get(_BREAKER_OPEN_KEY))
    except Exception:
        return False


def _breaker_record_failure() -> None:
    try:
        fails = cache.get(_BREAKER_KEY) or 0
        fails += 1
        cache.set(_BREAKER_KEY, fails, _BREAKER_COOLDOWN)
        if fails >= _BREAKER_THRESHOLD:
            cache.set(_BREAKER_OPEN_KEY, True, _BREAKER_COOLDOWN)
            logger.error(
                "Product webhook breaker OPEN: %s подряд неудач, пауза %s с. "
                "Пропущенное досошлёт периодический catalog_webhook_sync.",
                fails,
                _BREAKER_COOLDOWN,
            )
    except Exception:
        pass


def _breaker_record_success() -> None:
    try:
        cache.delete(_BREAKER_KEY)
        cache.delete(_BREAKER_OPEN_KEY)
    except Exception:
        pass


def _build_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _allowed_company_id() -> str | None:
    """
    Optional safety gate: if SITE_WEBHOOK_COMPANY_ID is set, webhooks are sent only for that company.
    """
    raw = getattr(settings, "SITE_WEBHOOK_COMPANY_ID", None)
    if not raw:
        return None
    return str(raw).strip().lower()


def _is_company_allowed(company_id) -> bool:
    allowed = _allowed_company_id()
    if not allowed:
        return True
    if not company_id:
        return False
    return str(company_id).strip().lower() == allowed


def _send_payload(payload: dict, *, retries: int, timeout: int, backoff: float) -> None:
    url = getattr(settings, "SITE_WEBHOOK_URL", None)
    if not url:
        return

    if _breaker_is_open():
        logger.warning(
            "Product webhook пропущен (breaker открыт). event=%s", payload.get("event")
        )
        return

    secret = str(getattr(settings, "SITE_WEBHOOK_SECRET", "") or "")

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-CRM-Signature": _build_signature(secret, body),
    }

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or 0
                if 200 <= int(status) < 300:
                    _breaker_record_success()
                    return
                raise RuntimeError(f"Unexpected status code: {status}")
        except Exception:
            logger.error(
                "Product webhook failed. event=%s url=%s attempt=%s/%s",
                payload.get("event"),
                url,
                attempt + 1,
                retries,
                exc_info=True,
            )
            if attempt + 1 < retries:
                time.sleep(backoff ** attempt)

    _breaker_record_failure()


def send_product_webhook_data(
    data: dict,
    event: str,
    *,
    retries: int = 3,
    timeout: int = 5,
    backoff: float = 1.5,
    sync: bool = False,
) -> None:
    """
    Same webhook format as send_product_webhook(), but accepts already-serialized product data.
    Useful for delete events (when the instance is about to be removed).
    """
    try:
        if not _is_company_allowed((data or {}).get("company")):
            return
    except Exception:
        return

    payload = {"event": event, "data": data}

    # HTTP уходит в Celery: при недоступном приёмнике одна отправка стоит
    # ~17.5 с, а на чекауте их столько, сколько позиций в чеке. В запросе
    # этому не место. Если очередь недоступна — молча пропускаем, каталог
    # досинхронизирует периодический apps.main.tasks.catalog_webhook_sync.
    if sync:
        try:
            _send_payload(payload, retries=retries, timeout=timeout, backoff=backoff)
        except Exception:
            logger.error("Unexpected error sending product webhook payload. event=%s", event, exc_info=True)
        return

    try:
        from apps.main.tasks import deliver_product_webhook

        deliver_product_webhook.delay(payload, retries=retries, timeout=timeout, backoff=backoff)
    except Exception:
        logger.error("Не удалось поставить product webhook в очередь. event=%s", event, exc_info=True)


def send_product_webhook(
    product,
    event: str,
    *,
    retries: int = 3,
    timeout: int = 5,
    backoff: float = 1.5,
    sync: bool = False,
) -> None:
    """
    Sends product webhook. Never raises.

    Payload:
      {
        "event": "product.created" | "product.updated",
        "data": <Product JSON as in GET /api/main/products/list/>
      }
    """
    try:
        if not _is_company_allowed(getattr(product, "company_id", None)):
            return
    except Exception:
        return

    try:
        data = ProductSerializer(product, context={"request": None}).data
    except Exception:
        try:
            data = ProductSerializer(product, context={}).data
        except Exception:
            logger.error(
                "Failed to serialize product for webhook. product_id=%s event=%s",
                getattr(product, "id", None),
                event,
                exc_info=True,
            )
            return

    send_product_webhook_data(
        data, event, retries=retries, timeout=timeout, backoff=backoff, sync=sync
    )
