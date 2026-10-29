from __future__ import annotations

from urllib.parse import urlparse

import requests
from django.http import HttpResponse, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated


ALLOWED_MEDIA_PREFIX = "https://app.nurcrm.kg/media/"


def _is_allowed_url(raw_url: str) -> bool:
    if not raw_url or not raw_url.startswith(ALLOWED_MEDIA_PREFIX):
        return False

    parsed = urlparse(raw_url)
    if parsed.scheme != "https":
        return False
    if parsed.netloc != "app.nurcrm.kg":
        return False
    if not parsed.path.startswith("/media/"):
        return False

    return True


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def media_proxy(request):
    raw_url = request.query_params.get("url")  # DRF Request
    if not _is_allowed_url(raw_url):
        resp = JsonResponse({"detail": "Invalid or non-whitelisted url."}, status=400)
        resp["Access-Control-Allow-Origin"] = "*"
        return resp

    try:
        upstream = requests.get(raw_url, stream=True, timeout=15, allow_redirects=False)
    except requests.RequestException:
        resp = JsonResponse({"detail": "Failed to fetch upstream content."}, status=502)
        resp["Access-Control-Allow-Origin"] = "*"
        return resp

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    resp = HttpResponse(upstream.content, content_type=content_type, status=upstream.status_code)
    resp["Access-Control-Allow-Origin"] = "*"
    return resp

