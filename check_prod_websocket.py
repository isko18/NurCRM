#!/usr/bin/env python3
"""
Проверка WebSocket на продакшене
"""
import os
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings

print("🔍 Проверка WebSocket на продакшене\n")
print("="*60)

# 1. Проверка настроек
print("\n1️⃣  Проверка настроек Django...")
print(f"   ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
print(f"   DEBUG: {settings.DEBUG}")
print(f"   ASGI_APPLICATION: {settings.ASGI_APPLICATION}")

# 2. Проверка Channel Layer
print("\n2️⃣  Проверка Channel Layer...")
try:
    channel_layer = get_channel_layer()
    if channel_layer:
        print(f"   ✅ Channel Layer: {channel_layer.__class__.__name__}")
        config = getattr(channel_layer, 'config', {})
        hosts = config.get('hosts', [])
        print(f"   Redis hosts: {hosts}")
        
        # Тест отправки
        try:
            async_to_sync(channel_layer.group_send)(
                "test_group_prod",
                {"type": "test_message", "test": True}
            )
            print("   ✅ Тест отправки сообщения прошел успешно")
        except Exception as e:
            print(f"   ❌ Ошибка отправки: {e}")
            traceback.print_exc()
    else:
        print("   ❌ Channel Layer не настроен")
except Exception as e:
    print(f"   ❌ Ошибка получения Channel Layer: {e}")
    traceback.print_exc()

# 3. Проверка Redis подключения
print("\n3️⃣  Проверка Redis...")
try:
    import redis
    from channels_redis.core import ChannelLayer
    
    config = channel_layer.config if channel_layer else {}
    hosts = config.get('hosts', ['redis://127.0.0.1:6379/0'])
    
    if hosts:
        redis_url = hosts[0] if isinstance(hosts[0], str) else f"redis://{hosts[0]['address'][0]}:{hosts[0]['address'][1]}"
        print(f"   Redis URL: {redis_url}")
        
        # Парсим URL
        if isinstance(hosts[0], str):
            from urllib.parse import urlparse
            parsed = urlparse(redis_url)
            host = parsed.hostname or '127.0.0.1'
            port = parsed.port or 6379
        else:
            host = hosts[0]['address'][0]
            port = hosts[0]['address'][1]
        
        # Тест подключения
        r = redis.Redis(host=host, port=port, db=0, socket_connect_timeout=5)
        result = r.ping()
        print(f"   ✅ Redis доступен: {result}")
        
        # Проверяем группы WebSocket
        keys = r.keys("asgi:group:cafe_*")
        print(f"   📊 Активных WebSocket групп: {len(keys)}")
        if keys:
            print(f"   Группы: {[k.decode() for k in keys[:5]]}")
    else:
        print("   ⚠️  Redis hosts не настроены")
        
except Exception as e:
    print(f"   ❌ Ошибка подключения к Redis: {e}")
    traceback.print_exc()

# 4. Проверка ASGI конфигурации
print("\n4️⃣  Проверка ASGI...")
try:
    from core.asgi import application, websocket_urlpatterns
    print(f"   ✅ ASGI приложение загружено")
    print(f"   📊 WebSocket маршрутов: {len(websocket_urlpatterns)}")
    for pattern in websocket_urlpatterns:
        print(f"      - {pattern.pattern.regex.pattern}")
except Exception as e:
    print(f"   ❌ Ошибка ASGI: {e}")
    traceback.print_exc()

print("\n" + "="*60)
print("\n💡 Если WebSocket не работает на продакшене:")
print("   1. Проверьте, что Redis доступен из Django процесса")
print("   2. Проверьте логи gunicorn: journalctl -u gunicorn -f")
print("   3. Проверьте nginx логи: tail -f /var/log/nginx/error.log")
print("   4. Убедитесь, что клиент использует правильный URL: wss://app.nurcrm.kg/ws/...")
print("   5. Проверьте, что JWT токен валиден и не истек")
