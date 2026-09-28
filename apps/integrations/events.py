"""
События для внешних интеграций (вебхуки).

    emit_event(company_id, "sale.paid", {...})

Отправка идёт после коммита транзакции через Celery; ошибки здесь никогда не
ломают основную операцию (продажу, закрытие смены и т.д.).
"""
import ipaddress
import logging
import socket
import uuid
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

EVENTS = (
    "sale.paid",
    "sale.returned",
    "shift.closed",
    "stock.low",
    "company.updated",
    "appointment.created",
)


def validate_public_url(url: str) -> None:
    """Запрещаем вебхуки на внутренние адреса сервера (SSRF). ValueError — если нельзя."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Адрес должен начинаться с http:// или https://")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError("Не удаётся найти адрес вебхука.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("Адрес вебхука указывает во внутреннюю сеть.")


def _json_safe(value):
    from decimal import Decimal
    from datetime import date, datetime

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01"))) if value == value.quantize(Decimal("0.01")) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _dispatch(company_id, event: str, data: dict) -> None:
    from .models import WebhookEndpoint
    from .tasks import deliver_webhook

    endpoints = [
        ep for ep in WebhookEndpoint.objects.filter(company_id=company_id, is_active=True).only("id", "events")
        if event in (ep.events or [])
    ]
    if not endpoints:
        return
    envelope = {
        "id": f"evt_{uuid.uuid4().hex}",
        "event": event,
        "company": str(company_id),
        "at": timezone.localtime().isoformat(timespec="seconds"),
        "data": _json_safe(data),
    }
    for ep in endpoints:
        deliver_webhook.delay(str(ep.id), envelope)


def emit_event(company_id, event: str, data: dict) -> None:
    if not company_id or event not in EVENTS:
        return

    def _send():
        try:
            _dispatch(company_id, event, data)
        except Exception:
            logger.exception("webhook emit failed: %s", event)

    try:
        transaction.on_commit(_send)
    except Exception:
        logger.exception("webhook on_commit failed: %s", event)
