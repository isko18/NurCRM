from __future__ import annotations

import base64
import logging
from typing import Any

import requests
from django.core.cache import cache

from .crypto import decrypt_secret
from .exceptions import OneCAPIError
from .models import OneCIntegration

logger = logging.getLogger(__name__)


def _token_cache_key(company_id) -> str:
    return f"onec:bearer:{company_id}"


class OneCHttpClient:
    """
    HTTP-клиент к опубликованным сервисам 1С.

    - auth_type=basic → заголовок Basic на каждый запрос.
    - auth_type=token → Bearer из зашифрованного поля (кешируется).
    """

    def __init__(self, integration: OneCIntegration):
        self.integration = integration
        self.base = integration.effective_base_url()
        if not self.base:
            raise OneCAPIError("Не задан базовый URL 1С.", status_code=500)

    def _authorization(self) -> str:
        secret = decrypt_secret(self.integration.password_cipher)
        if self.integration.auth_type == OneCIntegration.AuthType.TOKEN:
            cached = cache.get(_token_cache_key(self.integration.company_id))
            if cached:
                return cached
            auth = f"Bearer {secret}"
            cache.set(_token_cache_key(self.integration.company_id), auth, timeout=300)
            return auth
        raw = f"{self.integration.login}:{secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def invalidate_token(self):
        cache.delete(_token_cache_key(self.integration.company_id))

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._authorization(),
        }

    def _parse(self, r: requests.Response) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:2000]}
        if r.status_code >= 400:
            raise OneCAPIError(f"HTTP {r.status_code} от 1С", status_code=r.status_code, payload=body)
        return body

    def request_json(self, method: str, path: str, *, json_body=None, allow_retry_on_401=True) -> dict:
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base}{path}"
        timeout = int(self.integration.request_timeout or 30)
        m = method.upper()

        if m == "GET":
            r = requests.get(url, headers=self._headers(), timeout=timeout)
        elif m == "POST":
            r = requests.post(url, headers=self._headers(), json=json_body or {}, timeout=timeout)
        else:
            raise OneCAPIError(f"Неподдерживаемый метод {method}", status_code=500)

        if r.status_code == 401 and allow_retry_on_401:
            self.invalidate_token()
            return self.request_json(method, path, json_body=json_body, allow_retry_on_401=False)

        return self._parse(r)
