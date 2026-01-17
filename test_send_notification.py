#!/usr/bin/env python3
"""
Скрипт для тестирования отправки WebSocket уведомлений
"""
import os
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from apps.cafe.models import Order, Table
from apps.cafe.views import (
    send_order_created_notification,
    send_table_status_changed_notification
)
from apps.users.models import Company
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

print("🧪 Тестирование отправки WebSocket уведомлений\n")
print("="*60)

# Получаем тестовые данные
company = Company.objects.first()
if not company:
    print("❌ Нет компаний в базе")
    exit(1)

print(f"✅ Компания: {company.name} ({company.id})")

# Проверяем Channel Layer
print("\n1️⃣  Проверка Channel Layer...")
try:
    channel_layer = get_channel_layer()
    if not channel_layer:
        print("   ❌ Channel Layer не настроен")
        exit(1)
    print("   ✅ Channel Layer найден")
    
    # Тест отправки сообщения
    group_name = f"cafe_tables_{company.id}"
    print(f"   🧪 Отправка тестового сообщения в группу: {group_name}")
    
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "table_status_changed",
            "payload": {
                "test": True,
                "message": "Тестовое уведомление",
                "company_id": str(company.id),
            }
        }
    )
    print("   ✅ Сообщение отправлено в channel layer")
    
except Exception as e:
    print(f"   ❌ Ошибка: {e}")
    traceback.print_exc()
    exit(1)

# Проверяем наличие заказов и столов
print("\n2️⃣  Проверка данных...")
order = Order.objects.filter(company=company).first()
table = Table.objects.filter(company=company).first()

if not order:
    print("   ⚠️  Нет заказов для теста")
else:
    print(f"   ✅ Заказ найден: {order.id}")

if not table:
    print("   ⚠️  Нет столов для теста")
else:
    print(f"   ✅ Стол найден: {table.id} (№{table.number})")

# Тестируем отправку уведомлений с выводом ошибок
print("\n3️⃣  Тестирование send_table_status_changed_notification...")
if table:
    try:
        # Временно заменяем функцию для вывода ошибок
        import apps.cafe.views as views_module
        
        # Сохраняем оригинальную функцию
        original_func = views_module.send_table_status_changed_notification
        
        def debug_send_table_status_changed_notification(table):
            """Версия с выводом ошибок"""
            try:
                channel_layer = get_channel_layer()
                if not channel_layer:
                    print("      ❌ Channel Layer не найден")
                    return
                
                company_id = str(table.company_id)
                branch_id = str(table.branch_id) if table.branch_id else None
                
                if branch_id:
                    group_name = f"cafe_tables_{company_id}_{branch_id}"
                else:
                    group_name = f"cafe_tables_{company_id}"
                
                print(f"      📤 Отправка в группу: {group_name}")
                
                from .serializers import TableSerializer
                serializer = TableSerializer(table)
                table_data = serializer.data
                
                async_to_sync(channel_layer.group_send)(
                    group_name,
                    {
                        "type": "table_status_changed",
                        "payload": {
                            "table": table_data,
                            "table_id": str(table.id),
                            "table_number": table.number,
                            "status": table.status,
                            "status_display": table.get_status_display(),
                            "company_id": company_id,
                            "branch_id": branch_id,
                        }
                    }
                )
                print("      ✅ Уведомление отправлено успешно")
            except Exception as e:
                print(f"      ❌ Ошибка при отправке: {e}")
                traceback.print_exc()
        
        # Временно заменяем функцию
        views_module.send_table_status_changed_notification = debug_send_table_status_changed_notification
        
        # Вызываем функцию
        from apps.cafe import views
        views.send_table_status_changed_notification(table)
        
        # Восстанавливаем оригинальную функцию
        views_module.send_table_status_changed_notification = original_func
        
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        traceback.print_exc()
else:
    print("   ⚠️  Пропущено (нет стола)")

print("\n" + "="*60)
print("\n💡 Если сообщение было отправлено, но не получено клиентом:")
print("   1. Убедитесь, что WebSocket подключен к правильной группе")
print("   2. Проверьте, что branch_id совпадает (если указан)")
print("   3. Проверьте логи Redis на наличие сообщений")
