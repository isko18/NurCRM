"""
Утилиты для кэширования данных.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Optional
from django.core.cache import cache
from django.conf import settings
import hashlib
import json


def _stable_repr(value: Any) -> str:
    """
    Стабильное представление значения для генерации ключа.
    Важно: не используем встроенный hash(), потому что он может быть разным
    между перезапусками Python (PYTHONHASHSEED).
    """
    if value is None:
        return "null"

    # Django model instance
    if hasattr(value, "pk") and value.pk is not None:
        return f"{value.__class__.__name__}:{value.pk}"

    # простые типы
    if isinstance(value, (str, int, float, bool)):
        return str(value)

    # контейнеры
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_stable_repr(x) for x in value) + "]"

    if isinstance(value, set):
        # set неупорядочен — сортируем представление
        return "{" + ",".join(sorted(_stable_repr(x) for x in value)) + "}"

    if isinstance(value, dict):
        # сортируем ключи, чтобы порядок не влиял
        items = []
        for k in sorted(value.keys(), key=lambda x: str(x)):
            items.append(f"{_stable_repr(k)}:{_stable_repr(value[k])}")
        return "{" + ",".join(items) + "}"

    # fallback: пытаемся json-нуть (некоторые объекты дадут ошибку)
    try:
        dumped = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return dumped
    except Exception:
        return str(value)


def cache_key(*args, **kwargs) -> str:
    """
    Генерирует детерминированный ключ кэша из args/kwargs.
    """
    parts = []
    for arg in args:
        parts.append(_stable_repr(arg))

    for k, v in sorted(kwargs.items(), key=lambda kv: kv[0]):
        parts.append(f"{k}={_stable_repr(v)}")

    raw = "|".join(parts)
    key_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"nurcrm:cache:{key_hash}"


def cached_result(
    timeout: Optional[int] = None,
    key_prefix: str = "",
    *,
    version: str = "v1",
    cache_none: bool = True,
):
    """
    Декоратор для кэширования результатов функций.

    params:
      - timeout: TTL; если None -> settings.CACHE_TIMEOUT_MEDIUM (default 300)
      - key_prefix: префикс домена (например "agent_products")
      - version: ручная версия ключа (полезно при изменении формата ответа)
      - cache_none: кэшировать ли None (обычно да, чтобы не лупить БД повторно)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            base = f"nurcrm:{key_prefix}:{func.__name__}:{version}"
            ck = cache_key(base, args, kwargs)

            hit = cache.get(ck)
            if hit is not None:
                return hit

            result = func(*args, **kwargs)

            # если None не надо кэшировать
            if result is None and not cache_none:
                return None

            ttl = timeout if timeout is not None else getattr(settings, "CACHE_TIMEOUT_MEDIUM", 300)
            cache.set(ck, result, ttl)
            return result

        return wrapper
    return decorator


def invalidate_cache_pattern(pattern: str) -> int:
    """
    Инвалидирует кэш по паттерну (требует django-redis).
    Использует SCAN вместо KEYS.

    pattern example:
      - "analytics:market:"
      - "products:list:"
      - "agent:products:"
    Возвращает количество удаленных ключей (0 если не удалось).
    """
    try:
        from django_redis import get_redis_connection
        redis_client = get_redis_connection("default")

        # мы явно используем "nurcrm:" в ключах -> матчим его
        match = f"nurcrm:{pattern}*"

        keys = list(redis_client.scan_iter(match=match, count=1000))
        if not keys:
            return 0

        # delete принимает *keys
        deleted = redis_client.delete(*keys)
        return int(deleted or 0)
    except Exception:
        # IGNORE_EXCEPTIONS=True -> Redis может быть недоступен, не валим API
        return 0


def cache_agent_analytics_key(
    company_id: str,
    branch_id: Optional[str],
    agent_id: str,
    period: str,
    date_from: str,
    date_to: str,
) -> str:
    """Генерирует ключ кэша для аналитики агента."""
    return f"nurcrm:analytics:agent:{company_id}:{branch_id or 'global'}:{agent_id}:{period}:{date_from}:{date_to}"


def cache_product_list_key(company_id: str, branch_id: Optional[str], filters_hash: str) -> str:
    """Генерирует ключ кэша для списка продуктов."""
    return f"nurcrm:products:list:{company_id}:{branch_id or 'global'}:{filters_hash}"


def cache_agent_products_key(agent_id: str, company_id: str, branch_id: Optional[str]) -> str:
    """Генерирует ключ кэша для продуктов агента."""
    return f"nurcrm:agent:products:{agent_id}:{company_id}:{branch_id or 'global'}"


def cache_market_analytics_key(company_id: str, branch_id: Optional[str], tab: str, query_hash: str) -> str:
    """
    Ключ для market analytics (то, что тебе нужно для analytics_market.py).
    """
    return f"nurcrm:analytics:market:{company_id}:{branch_id or 'global'}:{tab}:{query_hash}"
