# apps/cafe/cache_utils.py
"""Утилиты кэша: сброс ответов аналитики кафе после изменения оплат/возвратов."""

from __future__ import annotations

from django.core.cache import cache


def invalidate_cafe_analytics_cache(company_id) -> None:
    """
    Удаляет из Redis ключи ответов GET-аналитики кафе для компании (паттерн совпадает с _cache_key в analytics.py).
    Иначе до истечения TTL выручка после возврата в API не обновляется.
    """
    cid = str(company_id)
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        match = f"*cafe:analytics*{cid}*"
        for key in r.scan_iter(match=match, count=256):
            try:
                r.delete(key)
            except Exception:
                pass
        return
    except Exception:
        pass
    try:
        dp = getattr(cache, "delete_pattern", None)
        if callable(dp):
            dp(f"*cafe:analytics*{cid}*")
    except Exception:
        pass
