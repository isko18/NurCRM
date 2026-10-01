# QuickStart: WhatsApp интеграция для NurCRM

Быстрая инструкция по установке и первому использованию.

## ⚡ 5 минут на установку

### Шаг 1: Обновить settings.py

Убедитесь, что `apps.construction` в INSTALLED_APPS:

```python
# core/settings.py
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    
    # Ваши приложения
    "apps.construction",  # ← Убедитесь что есть
    # ...остальные...
]
```

### Шаг 2: Добавить URLs

```python
# core/urls.py
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # ... другие urls ...
    
    # ← Добавить эту строку
    path("api/whatsapp/", include("apps.construction.integrations.whatsapp.urls")),
]
```

### Шаг 3: Миграции

```bash
python manage.py migrate
```

### Шаг 4: Создать конфигурацию

**Вариант A: Через Django Admin** (рекомендуется)

1. Откройте `/admin/`
2. Найдите "WhatsApp Интеграция" → "Конфигурации WhatsApp"
3. Нажмите "Добавить"
4. Заполните форму (см. пример ниже)
5. Сохраните

**Вариант B: Через Python (Django Shell)**

```bash
python manage.py shell
```

```python
from apps.construction.integrations.whatsapp.models import WhatsAppConfig
from apps.users.models import Company

company = Company.objects.first()

config = WhatsAppConfig.objects.create(
    company=company,
    phone_number="+79991234567",  # ← Ваш номер WhatsApp
    business_account_id="your_account_id",  # ← От Meta
    phone_number_id="your_phone_id",  # ← От Meta
    access_token="your_token",  # ← От Meta
    is_active=True,
)

print(f"Конфигурация создана: {config.id}")
print(f"Webhook URL: {config.webhook_url}")
```

## 📝 Примеры использования

### Пример 1: Отправить простое сообщение

```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

message = send_whatsapp_message(
    company_id="company-uuid",
    phone_number="+79991234567",
    message="Привет! 👋"
)

print(f"Отправлено: {message.whatsapp_message_id}")
```

### Пример 2: Отправить с привязкой к объекту

```python
# Когда создается лид
message = send_whatsapp_message(
    company_id=lead.company_id,
    phone_number=lead.phone_number,
    message="Спасибо за заявку!",
    content_type="Lead",
    object_id=lead.id,
)
```

### Пример 3: Автоматизация при создании объекта

```python
# apps/construction/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@receiver(post_save, sender=Order)
def send_order_notification(sender, instance, created, **kwargs):
    if created:
        send_whatsapp_message(
            company_id=instance.company_id,
            phone_number=instance.customer_phone,
            message=f"Заказ #{instance.number} принят!",
            content_type="Order",
            object_id=instance.id,
        )

# apps/construction/apps.py - добавить в ready()
def ready(self):
    from . import signals
```

## 🔗 Настройка Webhook

После создания конфигурации:

1. **Скопируйте Webhook URL** из Django Admin
   - Откройте конфигурацию
   - Найдите поле "URL вебхука"
   - Скопируйте URL

2. **Настройте в Meta Business Platform**
   - Перейдите https://business.facebook.com
   - Выберите WhatsApp Account
   - Settings → Webhook → Edit
   - Paste URL в "Callback URL"
   - Paste "NurCRM_WhatsApp_Webhook" в "Verify Token"
   - Subscribe to events: `messages`, `message_status`

3. **Проверьте подключение**
   - Откройте логи: `tail -f /path/to/logs/application.log`
   - Отправьте сообщение на номер из конфигурации
   - Проверьте что сообщение появилось в БД

## 🧪 Тестирование

### Проверить что работает

```bash
python manage.py shell
```

```python
from apps.construction.integrations.whatsapp.models import WhatsAppConfig

config = WhatsAppConfig.objects.filter(is_active=True).first()
print(f"Конфигурация: {config}")
print(f"Номер: {config.phone_number}")
print(f"Активна: {config.is_active}")

# Отправить тест
from apps.construction.integrations.whatsapp.services import WhatsAppService

service = WhatsAppService(config)
msg = service.send_message(
    phone_number="+79991234567",  # Ваш номер для теста
    message="Test message from NurCRM"
)

print(f"Статус: {msg.status}")
print(f"WhatsApp ID: {msg.whatsapp_message_id}")
```

### Проверить историю сообщений

```bash
# REST API
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/whatsapp/messages/?phone_number=%2B79991234567

# Или через Django Shell
from apps.construction.integrations.whatsapp.models import WhatsAppMessage

messages = WhatsAppMessage.objects.all().order_by("-created_at")[:10]
for msg in messages:
    print(f"{msg.created_at} | {msg.direction} | {msg.status} | {msg.content[:50]}")
```

## 🔑 Где получить credentials от Meta?

1. Откройте https://business.facebook.com
2. Перейдите в Apps → Ваше приложение
3. Settings → Basic → Copy App ID и App Secret
4. На главной странице Apps → WhatsApp → Configuration
5. Найдите раздел "Phone Numbers"
6. Скопируйте:
   - Phone Number ID
   - Business Phone Number (+7XXXXXXXXXX)
7. Generate Access Token (или используйте существующий)

## 📊 Проверка статистики через API

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/whatsapp/configs/{config_id}/stats/
```

Ответ:
```json
{
    "total_messages": 150,
    "inbound_messages": 75,
    "outbound_messages": 75,
    "failed_messages": 2,
    "contacts_count": 45,
    "is_active": true
}
```

## ⚠️ Частые проблемы

| Проблема | Решение |
|----------|---------|
| Access Token недействителен | Обновите токен в Meta Business Platform |
| Сообщение не отправляется | Проверьте формат номера: +7XXXXXXXXXX |
| Webhook не работает | Убедитесь что URL HTTPS и правильный Verify Token |
| Нет входящих сообщений | Проверьте subscription на events в Meta |
| "Приложение не может отправлять сообщения" | Номер должен быть в Business Account |

## 🚀 Следующие шаги

1. ✅ Установка завершена
2. → Добавьте сообщения в ваши models через signals
3. → Интегрируйте в views/forms/tasks
4. → Настройте templates (если используются)
5. → Мониторьте логи и метрики

## 📚 Полная документация

- [WHATSAPP_INTEGRATION.md](./WHATSAPP_INTEGRATION.md) - Полное руководство
- [WHATSAPP_CONSTRUCTION_EXAMPLES.md](./WHATSAPP_CONSTRUCTION_EXAMPLES.md) - Примеры для construction

## 💬 Поддержка

Если возникли вопросы:

1. Проверьте логи: `python manage.py shell`
2. Посмотрите messages в БД через админ-панель
3. Проверьте webhook события в Meta Business Platform
4. Обратитесь к разработчику

---

**Ready to go! 🎉**

Начните с простого примера и постепенно добавляйте функциональность.
