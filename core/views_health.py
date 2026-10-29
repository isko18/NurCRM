import os
import subprocess

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

RETRY_AFTER_SECONDS = "30"


def _read_version() -> str:
    env = os.getenv("APP_VERSION")
    if env:
        return env
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True, timeout=2,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


VERSION = _read_version()


def health_check(request):
    """
    GET /api/health/ (и /health/) — без авторизации и лимита запросов (BE2-09).
    200 — сервер и база работают; 503 + Retry-After — база недоступна.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        resp = JsonResponse(
            {"status": "degraded", "db": "down", "code": "db_down", "detail": "База данных недоступна."},
            status=503,
        )
        resp["Retry-After"] = RETRY_AFTER_SECONDS
        return resp
    return JsonResponse({
        "status": "ok",
        "db": "ok",
        "time": timezone.localtime().isoformat(timespec="seconds"),
        "version": VERSION,
    })
