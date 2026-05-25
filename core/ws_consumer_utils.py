"""Shared helpers for Channels WebSocket consumers."""
from __future__ import annotations

import logging

from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger("nurcrm.websocket.consumer")

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403


async def reject_websocket_unauthorized(consumer, *, close_code: int = WS_CLOSE_UNAUTHORIZED) -> None:
    """
    Reject unauthorized WebSocket cleanly.

    Important: accept() first so the client receives a WebSocket close code instead of HTTP 403,
    which prevents mobile clients from treating the failure as a generic HTTP error and
    entering an instant reconnect loop.
    """
    path = consumer.scope.get("path")
    auth_error = consumer.scope.get("ws_auth_error", "unauthorized")
    logger.warning(
        "websocket connect rejected path=%s error=%s close_code=%s",
        path,
        auth_error,
        close_code,
    )
    await consumer.accept()
    await consumer.close(code=close_code)


async def reject_websocket_forbidden(consumer, *, reason: str = "forbidden") -> None:
    logger.warning(
        "websocket connect forbidden path=%s reason=%s",
        consumer.scope.get("path"),
        reason,
    )
    await consumer.accept()
    await consumer.close(code=WS_CLOSE_FORBIDDEN)


def is_anonymous_scope_user(scope) -> bool:
    user = scope.get("user")
    return (not user) or isinstance(user, AnonymousUser)
