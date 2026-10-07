"""
Версионный сброс кэша аналитики склада: у каждой компании свой счётчик версии,
он входит в ключ кэша. Любое изменение документов/денег/заявок увеличивает версию,
поэтому новая продажа видна сразу, а не через CACHE_TIMEOUT_ANALYTICS.
"""
from __future__ import annotations

from django.core.cache import cache

_KEY = "wh_analytics_ver:{}"


def analytics_version(company_id) -> int:
    try:
        return int(cache.get_or_set(_KEY.format(company_id), 1, None))
    except Exception:
        return 0


def bump_analytics_version(company_id) -> None:
    if not company_id:
        return
    key = _KEY.format(company_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, None)
    except Exception:
        pass
