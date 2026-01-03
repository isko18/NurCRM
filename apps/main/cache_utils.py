"""
Утилиты для кэширования данных.
"""
from functools import wraps
from typing import Any, Callable, Optional
from django.core.cache import cache
from django.conf import settings
import hashlib
import json


def cache_key(*args, **kwargs) -> str:
    """
    Генерирует ключ кэша из аргументов.
    """
    key_parts = []
    for arg in args:
        if hasattr(arg, 'id'):
            key_parts.append(f"{arg.__class__.__name__}:{arg.id}")
        elif isinstance(arg, (str, int, float, bool)):
            key_parts.append(str(arg))
        else:
            key_parts.append(str(hash(str(arg))))
    
    for k, v in sorted(kwargs.items()):
        if hasattr(v, 'id'):
            key_parts.append(f"{k}:{v.__class__.__name__}:{v.id}")
        elif isinstance(v, (str, int, float, bool)):
            key_parts.append(f"{k}:{str(v)}")
        else:
            key_parts.append(f"{k}:{str(hash(str(v)))}")
    
    key_string = "|".join(key_parts)
    key_hash = hashlib.md5(key_string.encode()).hexdigest()
    return f"nurcrm:cache:{key_hash}"


def cached_result(timeout: Optional[int] = None, key_prefix: str = ""):
    """
    Декоратор для кэширования результатов функций.
    
    Usage:
        @cached_result(timeout=300, key_prefix="agent_products")
        def get_agent_products(agent, company):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Генерируем ключ кэша
            cache_key_str = f"nurcrm:{key_prefix}:{func.__name__}"
            if args or kwargs:
                cache_key_str = cache_key(cache_key_str, *args, **kwargs)
            
            # Пытаемся получить из кэша
            result = cache.get(cache_key_str)
            if result is not None:
                return result
            
            # Выполняем функцию
            result = func(*args, **kwargs)
            
            # Сохраняем в кэш
            timeout_value = timeout or getattr(settings, 'CACHE_TIMEOUT_MEDIUM', 300)
            cache.set(cache_key_str, result, timeout_value)
            
            return result
        return wrapper
    return decorator


def invalidate_cache_pattern(pattern: str):
    """
    Инвалидирует кэш по паттерну (требует django-redis).
    Использует SCAN для поиска ключей.
    """
    try:
        from django_redis import get_redis_connection
        redis_client = get_redis_connection("default")
        
        # Простой поиск по паттерну (для больших объемов лучше использовать SCAN)
        keys = redis_client.keys(f"nurcrm:{pattern}*")
        if keys:
            redis_client.delete(*keys)
    except ImportError:
        # Если django-redis не установлен, просто пропускаем
        pass
    except Exception:
        # Игнорируем ошибки инвалидации кэша
        pass


def cache_agent_analytics_key(company_id: str, branch_id: Optional[str], agent_id: str, period: str, date_from: str, date_to: str) -> str:
    """Генерирует ключ кэша для аналитики агента."""
    return f"nurcrm:analytics:agent:{company_id}:{branch_id or 'global'}:{agent_id}:{period}:{date_from}:{date_to}"


def cache_product_list_key(company_id: str, branch_id: Optional[str], filters_hash: str) -> str:
    """Генерирует ключ кэша для списка продуктов."""
    return f"nurcrm:products:list:{company_id}:{branch_id or 'global'}:{filters_hash}"


def cache_agent_products_key(agent_id: str, company_id: str, branch_id: Optional[str]) -> str:
    """Генерирует ключ кэша для продуктов агента."""
    return f"nurcrm:agent:products:{agent_id}:{company_id}:{branch_id or 'global'}"

