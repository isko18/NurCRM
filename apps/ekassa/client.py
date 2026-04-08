from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from apps.ekassa.crypto import decrypt_secret
from apps.ekassa.exceptions import EkassaAPIError
from apps.ekassa.models import EkassaIntegration

logger = logging.getLogger(__name__)


def _token_cache_key(company_id) -> str:
    return f"ekassa:access_token:{company_id}"


def _timeout() -> int:
    return int(getattr(settings, "EKASSA_REQUEST_TIMEOUT", 45) or 45)


class EkassaHttpClient:
    """
    Клиент HTTP API eKassa по документации Интеграция 1.14.
    Токен кешируется на стороне приложения; при смене настроек кеш сбрасывается (signals).
    """

    def __init__(self, integration: EkassaIntegration):
        self.integration = integration
        self.base = integration.effective_base_url()
        if not self.base:
            raise EkassaAPIError("Не задан базовый URL eKassa.", status_code=500)

    def _headers_json(self, authorization=None) -> dict:
        h = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if authorization:
            h["Authorization"] = authorization
        return h

    def login(self) -> str:
        pwd = decrypt_secret(self.integration.password_cipher)
        url = f"{self.base}/api/auth/login"
        r = requests.post(
            url,
            json={"email": self.integration.login_email.strip(), "password": pwd},
            headers=self._headers_json(),
            timeout=_timeout(),
        )
        data = self._parse_response(r)
        inner = data.get("data") or {}
        token = inner.get("access_token")
        if not token:
            raise EkassaAPIError("В ответе eKassa нет access_token.", status_code=r.status_code, payload=data)
        return f"Bearer {token}"

    def get_cached_bearer(self) -> str:
        key = _token_cache_key(self.integration.company_id)
        cached = cache.get(key)
        if cached:
            return cached
        auth = self.login()
        cache.set(key, auth, timeout=getattr(settings, "EKASSA_TOKEN_CACHE_SECONDS", 300))
        return auth

    def invalidate_token(self):
        cache.delete(_token_cache_key(self.integration.company_id))

    def _parse_response(self, r: requests.Response) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:2000]}
        if r.status_code >= 400:
            raise EkassaAPIError(
                f"HTTP {r.status_code} от eKassa",
                status_code=r.status_code,
                payload=body,
            )
        return body

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        allow_retry_on_401=True,
    ) -> dict:
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base}{path}"
        auth = self.get_cached_bearer()
        m = method.upper()
        if m == "GET":
            r = requests.get(url, headers=self._headers_json(auth), timeout=_timeout())
        elif m == "POST":
            r = requests.post(
                url,
                headers=self._headers_json(auth),
                json=json_body if json_body is not None else {},
                timeout=_timeout(),
            )
        else:
            raise EkassaAPIError(f"Неподдерживаемый метод {method}", status_code=500)

        if r.status_code == 401 and allow_retry_on_401:
            self.invalidate_token()
            return self.request_json(method, path, json_body=json_body, allow_retry_on_401=False)

        data = self._parse_response(r)
        if isinstance(data, dict) and data.get("status") == "Error":
            raise EkassaAPIError(
                str(data.get("message") or "Ошибка eKassa"),
                status_code=r.status_code,
                payload=data,
            )
        return data
