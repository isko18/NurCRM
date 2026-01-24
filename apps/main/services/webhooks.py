from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request

from django.conf import settings

from apps.main.serializers import ProductSerializer

logger = logging.getLogger("crm.webhooks")


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


def send_product_webhook_data(data: dict, event: str, *, retries: int = 3, timeout: int = 5, backoff: float = 1.5) -> None:
    """
    Same webhook format as send_product_webhook(), but accepts already-serialized product data.
    Useful for delete events (when the instance is about to be removed).
    """
    try:
        if not _is_company_allowed((data or {}).get("company")):
            return
    except Exception:
        return
    try:
        _send_payload({"event": event, "data": data}, retries=retries, timeout=timeout, backoff=backoff)
    except Exception:
        logger.error("Unexpected error sending product webhook payload. event=%s", event, exc_info=True)


def send_product_webhook(product, event: str, *, retries: int = 3, timeout: int = 5, backoff: float = 1.5) -> None:
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

    send_product_webhook_data(data, event, retries=retries, timeout=timeout, backoff=backoff)
