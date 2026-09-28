import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
TIMEOUT_SECONDS = 10


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@shared_task(bind=True, max_retries=MAX_RETRIES, name="apps.integrations.tasks.deliver_webhook")
def deliver_webhook(self, endpoint_id: str, envelope: dict):
    """POST события на адрес вебхука. При ошибке — до 5 повторов с тем же id события."""
    from .events import validate_public_url
    from .models import WebhookEndpoint

    ep = WebhookEndpoint.objects.filter(id=endpoint_id, is_active=True).first()
    if ep is None:
        return "skipped"

    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    status_code = None
    error = ""
    try:
        validate_public_url(ep.url)
        req = urllib.request.Request(
            ep.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "NurCRM-Webhooks/1.0",
                "X-Signature": sign(ep.secret, body),
                "X-Event": envelope.get("event", ""),
                "X-Event-Id": envelope.get("id", ""),
            },
        )
        with _opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
            status_code = resp.status
    except urllib.error.HTTPError as e:
        status_code = e.code
        error = f"HTTP {e.code}"
    except Exception as e:  # сеть, таймаут, запрещённый адрес
        error = str(e)[:500] or e.__class__.__name__

    ok = status_code is not None and 200 <= status_code < 300
    WebhookEndpoint.objects.filter(pk=ep.pk).update(
        last_delivery_at=timezone.now(),
        last_status=status_code,
        last_error="" if ok else error,
    )
    if ok:
        return "ok"

    if self.request.retries < MAX_RETRIES:
        raise self.retry(countdown=30 * (2 ** self.request.retries))
    logger.warning("webhook %s gave up after retries: %s", endpoint_id, error)
    return "failed"
