#!/usr/bin/env python3
"""
Быстрая проверка работы WebSocket через Django shell
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async
from asgiref.sync import async_to_sync
from apps.cafe.views import send_order_created_notification, send_table_status_changed_notification
from apps.cafe.models import Order, Table
from apps.users.models import Company, Branch
from decimal import Decimal

User = get_user_model()

print("🔍 Проверка WebSocket для cafe приложения\n")
print("="*60)

# 1. Проверка Channel Layer
print("\n1️⃣  Проверка Channel Layer (Redis)...")
try:
    channel_layer = get_channel_layer()
    if channel_layer:
        # Тест ping к Redis
        result = async_to_sync(channel_layer.group_send)(
            "test_group",
            {"type": "test_message"}
        )
        print("   ✅ Channel Layer работает (Redis подключен)")
    else:
        print("   ❌ Channel Layer не настроен")
except Exception as e:
    print(f"   ❌ Ошибка Channel Layer: {e}")

# 2. Проверка наличия данных
print("\n2️⃣  Проверка тестовых данных...")
try:
    user = User.objects.first()
    if not user:
        print("   ⚠️  Нет пользователей в базе")
    else:
        print(f"   ✅ Найден пользователь: {user.email}")
        
        company = getattr(user, 'company', None) or getattr(user, 'owned_company', None)
        if company:
            print(f"   ✅ Компания: {company.name}")
            
            branch = getattr(user, 'branch', None) or getattr(user, 'primary_branch', None)
            if branch:
                print(f"   ✅ Филиал: {branch.name}")
            
            # Проверяем наличие заказов и столов
            orders_count = Order.objects.filter(company=company).count()
            tables_count = Table.objects.filter(company=company).count()
            print(f"   📊 Заказов: {orders_count}, Столов: {tables_count}")
        else:
            print("   ⚠️  У пользователя нет компании")
except Exception as e:
    print(f"   ❌ Ошибка при проверке данных: {e}")

# 3. Проверка функций отправки уведомлений
print("\n3️⃣  Проверка функций отправки уведомлений...")
try:
    company = Company.objects.first()
    if company:
        order = Order.objects.filter(company=company).first()
        table = Table.objects.filter(company=company).first()
        
        if order:
            print(f"   ✅ Тестовый заказ найден: {order.id}")
            print("   🧪 Попытка отправить уведомление о создании заказа...")
            try:
                send_order_created_notification(order)
                print("   ✅ send_order_created_notification выполнена без ошибок")
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
        else:
            print("   ⚠️  Нет заказов для теста")
        
        if table:
            print(f"   ✅ Тестовый стол найден: {table.number}")
            print("   🧪 Попытка отправить уведомление об изменении статуса стола...")
            try:
                send_table_status_changed_notification(table)
                print("   ✅ send_table_status_changed_notification выполнена без ошибок")
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
        else:
            print("   ⚠️  Нет столов для теста")
    else:
        print("   ⚠️  Нет компаний в базе")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# 4. Проверка маршрутов
print("\n4️⃣  Проверка WebSocket маршрутов...")
try:
    from apps.cafe.routing import websocket_urlpatterns
    print(f"   ✅ Найдено {len(websocket_urlpatterns)} маршрутов:")
    for pattern in websocket_urlpatterns:
        print(f"      - {pattern.pattern.regex.pattern}")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# 5. Проверка ASGI приложения
print("\n5️⃣  Проверка ASGI конфигурации...")
try:
    from core.asgi import application
    print("   ✅ ASGI приложение загружено успешно")
except Exception as e:
    print(f"   ❌ Ошибка ASGI: {e}")

print("\n" + "="*60)
print("\n💡 Для полного тестирования подключения используйте:")
print("   python test_websocket.py --url ws://localhost:8000/ws/cafe/orders/ --token YOUR_JWT_TOKEN")
print("\n   Или через браузер с помощью WebSocket клиента")
