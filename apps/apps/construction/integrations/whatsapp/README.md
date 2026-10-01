# WhatsApp Integration for NurCRM

Переиспользуемая модульная интеграция с WhatsApp Business API для управления сообщениями в воронке продаж и других приложениях.

## 🎯 Основные возможности

✅ Отправка текстовых сообщений  
✅ Отправка сообщений по шаблонам  
✅ Получение входящих сообщений  
✅ Отслеживание доставки (sent, delivered, read)  
✅ Автоответы на входящие сообщения  
✅ Управление контактами и историей  
✅ REST API для интеграции  
✅ Django Admin интерфейс  
✅ Логирование всех операций  
✅ Модульная архитектура (легко переиспользуется)  

## 📁 Структура

```
apps/construction/integrations/whatsapp/
├── __init__.py              # Инициализация
├── models.py                # 3 основные модели
├── services.py              # Бизнес-логика
├── serializers.py           # DRF serializers
├── views.py                 # REST API endpoints
├── urls.py                  # URL routing
├── admin.py                 # Django admin
├── apps.py                  # App config
└── utils.py                 # Helper функции
```

## 🚀 Быстрый старт

### 1. Миграции
```bash
python manage.py migrate
```

### 2. Конфигурация через Admin
- Откройте `/admin/`
- WhatsApp Интеграция → Конфигурации WhatsApp
- Добавьте свою конфигурацию с credentials от Meta

### 3. Отправить сообщение
```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

message = send_whatsapp_message(
    company_id="your-uuid",
    phone_number="+79991234567",
    message="Привет!"
)
```

## 📚 Документация

| Документ | Содержание | Время |
|----------|-----------|-------|
| [WHATSAPP_QUICKSTART.md](./docs/WHATSAPP_QUICKSTART.md) | Установка за 5 минут | 15 мин |
| [WHATSAPP_INTEGRATION.md](./docs/WHATSAPP_INTEGRATION.md) | Полное руководство | 60 мин |
| [WHATSAPP_CONSTRUCTION_EXAMPLES.md](./docs/WHATSAPP_CONSTRUCTION_EXAMPLES.md) | Примеры для construction | 30 мин |
| [WHATSAPP_ARCHITECTURE.md](./docs/WHATSAPP_ARCHITECTURE.md) | Техническая архитектура | 45 мин |
| [WHATSAPP_CHECKLIST.md](./docs/WHATSAPP_CHECKLIST.md) | Контрольный список | 20 мин |

**Рекомендуемый порядок:** Quickstart → Examples → Integration → Architecture

## 🔌 Интеграция

### Через signals (автоматическая отправка)
```python
from django.db.models.signals import post_save
from .models import Order
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@receiver(post_save, sender=Order)
def notify_order(sender, instance, created, **kwargs):
    if created:
        send_whatsapp_message(
            company_id=instance.company_id,
            phone_number=instance.customer_phone,
            message=f"Заказ #{instance.id} создан!",
            content_type="Order",
            object_id=instance.id,
        )
```

### Через REST API (из фронтенда)
```bash
POST /api/whatsapp/send-message/
{
    "config_id": "uuid",
    "phone_number": "+79991234567",
    "message": "Текст"
}
```

### Через утилиты (везде)
```python
from apps.construction.integrations.whatsapp.utils import (
    send_whatsapp_message,
    send_whatsapp_template,
    get_contact_conversations,
)
```

## 🔐 Безопасность

- ✅ Django `IsAuthenticated` для API
- ✅ Token verification для webhooks
- ✅ Company-based access control
- ✅ Sensitive data protection
- ✅ HTTPS webhook URL

## 📊 Моделиданных

### WhatsAppConfig
Конфигурация подключения (credentials, настройки, webhook).

### WhatsAppMessage
История всех сообщений (входящих/исходящих, статусы, медиа).

### WhatsAppContact
Контакты для отправки (номер, имя, последний контакт, привязка к объектам).

## 🎛️ API Endpoints

```
GET/POST    /api/whatsapp/configs/              # Управление конфигурациями
GET         /api/whatsapp/configs/{id}/stats/   # Статистика
GET/POST    /api/whatsapp/messages/             # История сообщений
GET         /api/whatsapp/messages/by_phone/    # Сообщения контакта
GET/POST    /api/whatsapp/contacts/             # Управление контактами
POST        /api/whatsapp/send-message/         # Отправить сообщение
GET/POST    /api/whatsapp/webhook/              # Webhook от WhatsApp
```

## 🔑 Где получить credentials

1. https://business.facebook.com
2. Apps → Your App → WhatsApp
3. Configuration → Phone Numbers
4. Скопируйте Phone Number ID, Business Account ID, Access Token

## ⚙️ Настройка Webhook

1. Скопируйте URL вебхука из Django Admin
2. Meta Business Platform → Settings → Webhook
3. Paste URL и Verify Token
4. Subscribe на события: `messages`, `message_status`

## 📝 Примеры использования

### Отправка при создании Lead
```python
send_whatsapp_message(
    company_id=lead.company_id,
    phone_number=lead.phone_number,
    message=f"Спасибо за заявку, {lead.name}!",
    content_type="Lead",
    object_id=lead.id,
)
```

### Отправка квитанции
```python
send_whatsapp_message(
    company_id=cashbox.company_id,
    phone_number=manager.phone_number,
    message=f"Касса {cashbox.name}: +5000 RUB",
    content_type="Cashbox",
    object_id=cashbox.id,
)
```

### Получить историю
```python
from apps.construction.integrations.whatsapp.utils import get_contact_conversations

messages = get_contact_conversations(
    company_id=company_id,
    phone_number="+79991234567",
    limit=20,
)
```

## 📈 Масштабируемость

- ✅ Индексы на часто запрашиваемые поля
- ✅ JSONField для гибкости метаданных
- ✅ Поддержка Celery для асинхронности
- ✅ Batch processing для массовых рассылок
- ✅ Логирование для дебагинга

## 🔄 Переиспользование в других приложениях

Интеграция полностью модульна и легко используется в других приложениях:

```python
# Где угодно в коде
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

message = send_whatsapp_message(
    company_id=your_company_id,
    phone_number=customer_phone,
    message="Your message",
)
```

## 🧪 Тестирование

```bash
python manage.py shell

from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

msg = send_whatsapp_message(
    company_id="your-uuid",
    phone_number="+79991234567",
    message="Test message"
)

print(f"Status: {msg.status}")
print(f"ID: {msg.whatsapp_message_id}")
```

## 🐛 Troubleshooting

| Проблема | Решение |
|----------|---------|
| 401 Unauthorized | Проверьте Access Token в конфигурации |
| Invalid phone number | Используйте формат +7XXXXXXXXXX |
| Webhook not working | Проверьте URL и Verify Token в Meta |
| No incoming messages | Проверьте subscribe на `messages` event |

## 📞 Поддержка

- 📖 [Полная документация](./docs/WHATSAPP_INTEGRATION.md)
- 💡 [Примеры интеграции](./docs/WHATSAPP_CONSTRUCTION_EXAMPLES.md)
- 🏗️ [Архитектура](./docs/WHATSAPP_ARCHITECTURE.md)
- ✅ [Контрольный список](./docs/WHATSAPP_CHECKLIST.md)

## 📋 Требования

- Django 5.0+
- Django REST Framework 3.14+
- requests 2.28+
- Python 3.8+

## 📦 Установка

```bash
# 1. Убедитесь что requests установлен
pip install requests

# 2. Выполните миграции
python manage.py migrate

# 3. Добавьте URLs в core/urls.py
path("api/whatsapp/", include("apps.construction.integrations.whatsapp.urls")),

# 4. Создайте конфигурацию через Django Admin
# /admin/construction/whatsappconfig/add/
```

## 🎯 Дальнейшее развитие

- [ ] Отправка медиафайлов (images, documents, video)
- [ ] Запланированные сообщения
- [ ] Advanced analytics and reporting
- [ ] AI-powered responses
- [ ] Multi-channel support (Telegram, SMS)
- [ ] A/B testing messages
- [ ] CRM integration suite

## 📄 Лицензия

Часть проекта NurCRM.

## 👨‍💻 Разработка

**Версия:** 1.0.0  
**Дата:** 14 июля 2024  
**Статус:** ✅ Production Ready

---

**Начните работу:**
1. Прочитайте [WHATSAPP_QUICKSTART.md](./docs/WHATSAPP_QUICKSTART.md)
2. Создайте конфигурацию через Admin
3. Отправьте первое сообщение
4. Интегрируйте в ваши модели!

**Всё что нужно знать находится в `/docs/WHATSAPP_*.md` файлах.**
