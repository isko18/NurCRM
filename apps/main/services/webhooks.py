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


def send_product_webhook(product, event: str, *, retries: int = 3, timeout: int = 5, backoff: float = 1.5) -> None:
    """
    Sends product webhook. Never raises.

    Payload:
      {
        "event": "product.created" | "product.updated",
        "data": <Product JSON as in GET /api/main/products/list/>
      }
    """
    url = getattr(settings, "SITE_WEBHOOK_URL", None)
    if not url:
        return

    secret = str(getattr(settings, "SITE_WEBHOOK_SECRET", "") or "")

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

    payload = {"event": event, "data": data}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-CRM-Signature": _build_signature(secret, body),
    }

    req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or 0
                if 200 <= int(status) < 300:
                    return
                raise RuntimeError(f"Unexpected status code: {status}")
        except Exception:
            logger.error(
                "Product webhook failed. product_id=%s event=%s url=%s attempt=%s/%s",
                getattr(product, "id", None),
                event,
                url,
                attempt + 1,
                retries,
                exc_info=True,
            )
            if attempt + 1 < retries:
                time.sleep(backoff ** attempt)

