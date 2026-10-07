"""Геокодирование адресов магазинов через Nominatim (OpenStreetMap): ≤1 запроса/с, свой User-Agent."""
import logging
import time
from decimal import Decimal

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import AppShopSettings

logger = logging.getLogger("clientapp.geocode")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MAX_ATTEMPTS = 3


def user_agent():
    return getattr(settings, "NOMINATIM_USER_AGENT", "") or "NurCRM-ClientApp/1.0 (+https://app.nurcrm.kg)"


def effective_address(row: AppShopSettings) -> str:
    if row.address:
        return row.address.strip()
    if row.branch_id:
        return (row.branch.address or "").strip()
    return (row.company.address or "").strip()


def _acquire_slot(max_wait=5.0) -> bool:
    """Глобальный лимит Nominatim: один запрос в секунду на весь сервер."""
    deadline = time.monotonic() + max_wait
    while True:
        try:
            if cache.add("capp:nominatim_slot", 1, timeout=1):
                return True
        except Exception:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def geocode_query(address: str):
    """(lat, lon) или None. Исключения не пробрасывает (кроме для повтора — RuntimeError)."""
    if not _acquire_slot():
        raise RuntimeError("nominatim busy")
    params = {"q": address, "format": "jsonv2", "limit": 1, "accept-language": "ru"}
    countries = getattr(settings, "NOMINATIM_COUNTRY_CODES", "kg")
    if countries:
        params["countrycodes"] = countries
    with httpx.Client(timeout=10.0, headers={"User-Agent": user_agent()}) as client:
        resp = client.get(NOMINATIM_URL, params=params)
    if resp.status_code == 429:
        raise RuntimeError("nominatim 429")
    resp.raise_for_status()
    data = resp.json() or []
    if not data:
        return None
    return Decimal(str(data[0]["lat"])).quantize(Decimal("0.000001")), Decimal(str(data[0]["lon"])).quantize(
        Decimal("0.000001")
    )


def needs_geocode(row: AppShopSettings) -> bool:
    if row.geocode_status == AppShopSettings.GeocodeStatus.MANUAL:
        return False
    addr = effective_address(row)
    if not addr:
        return False
    if addr != row.geocoded_address:
        return True
    return row.latitude is None and row.geocode_attempts < MAX_ATTEMPTS and row.geocode_status != "not_found"


def geocode_row(row_id) -> str:
    row = AppShopSettings.objects.select_related("company", "branch").filter(pk=row_id).first()
    if row is None or not needs_geocode(row):
        return "skip"
    addr = effective_address(row)
    attempts = row.geocode_attempts + 1 if addr == row.geocoded_address else 1
    try:
        point = geocode_query(addr)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("geocode failed for %s: %s", row_id, exc)
        AppShopSettings.objects.filter(pk=row.pk).update(
            geocode_status=AppShopSettings.GeocodeStatus.ERROR, geocoded_address=addr, geocode_attempts=attempts
        )
        return "error"
    now = timezone.now()
    if point is None:
        AppShopSettings.objects.filter(pk=row.pk).update(
            geocode_status=AppShopSettings.GeocodeStatus.NOT_FOUND,
            geocoded_address=addr,
            geocode_attempts=attempts,
            geocoded_at=now,
        )
        return "not_found"
    AppShopSettings.objects.filter(pk=row.pk).update(
        latitude=point[0],
        longitude=point[1],
        geocode_status=AppShopSettings.GeocodeStatus.OK,
        geocoded_address=addr,
        geocode_attempts=attempts,
        geocoded_at=now,
    )
    from .services import invalidate_shops_cache

    invalidate_shops_cache()
    return "ok"
