# WhatsApp Integration - Техническая архитектура

## Обзор

Модульная система интеграции с WhatsApp Business API для NurCRM.

## Архитектура

```
apps/construction/integrations/whatsapp/
├── __init__.py              # Инициализация модуля
├── models.py                # WhatsAppConfig, WhatsAppMessage, WhatsAppContact
├── services.py              # WhatsAppService, WhatsAppAPIClient
├── serializers.py           # DRF сериализаторы
├── views.py                 # REST API views
├── urls.py                  # URL routing
├── admin.py                 # Django admin
├── apps.py                  # App config
└── utils.py                 # Утилиты и helper функции
```

## Компоненты

### 1. Models (models.py)

#### WhatsAppConfig
Хранит конфигурацию подключения к WhatsApp.

```
├── Связанные данные
│   ├── company (ForeignKey → Company)
│   └── branch (ForeignKey → Branch, nullable)
├── Credentials
│   ├── phone_number: +7XXXXXXXXXX
│   ├── business_account_id
│   ├── access_token (sensitive)
│   └── phone_number_id
├── Webhook
│   ├── webhook_url (auto-generated)
│   └── webhook_verify_token
├── Settings
│   ├── is_active
│   ├── auto_reply_enabled
│   └── auto_reply_message
└── Metadata
    ├── created_at
    ├── updated_at
    └── last_webhook_received
```

#### WhatsAppMessage
История всех сообщений (входящих и исходящих).

```
├── Reference
│   ├── config (ForeignKey → WhatsAppConfig)
│   └── whatsapp_message_id (unique)
├── Content
│   ├── phone_number
│   ├── message_type (text|image|document|audio|video|location)
│   ├── content
│   └── media_url
├── Status
│   ├── direction (inbound|outbound)
│   ├── status (pending|sent|delivered|read|failed)
│   └── sent_at
├── Linking
│   ├── content_type (optional)
│   └── object_id (optional)
└── Metadata
    └── metadata (JSON)
```

#### WhatsAppContact
Контакты для отправки сообщений.

```
├── Reference
│   ├── config (ForeignKey → WhatsAppConfig)
│   └── phone_number (unique per config)
├── Info
│   ├── name
│   ├── is_active
│   └── is_blocked
├── Linking
│   ├── content_type
│   └── object_id
└── Metadata
    ├── last_message_at
    └── metadata (JSON)
```

### 2. Services (services.py)

#### WhatsAppAPIClient
Низкоуровневый клиент для работы с WhatsApp API.

```python
Methods:
├── send_text_message(phone, message) → Dict
├── send_template_message(phone, template_name, parameters) → Dict
├── mark_as_read(message_id) → Dict
└── get_message_status(message_id) → Dict
```

#### WhatsAppService
Высокоуровневый сервис с бизнес-логикой.

```python
Methods:
├── send_message(phone, message, content_type, object_id) → WhatsAppMessage
├── send_template_message(phone, template_name, parameters) → WhatsAppMessage
├── handle_webhook(data) → WhatsAppMessage
├── _process_inbound_message(message_data) → WhatsAppMessage
└── _process_message_status(status_data) → WhatsAppMessage
```

**Особенности:**
- Автоматическое сохранение сообщений в БД
- Обработка входящих сообщений
- Отправка автоответов
- Обновление статусов
- Управление контактами

### 3. Views (views.py)

#### WhatsAppConfigViewSet
REST API для управления конфигурациями.

```
GET/POST /api/whatsapp/configs/
GET /api/whatsapp/configs/{id}/
PATCH /api/whatsapp/configs/{id}/
GET /api/whatsapp/configs/{id}/stats/
```

#### WhatsAppMessageViewSet
REST API для просмотра сообщений.

```
GET /api/whatsapp/messages/
GET /api/whatsapp/messages/{id}/
GET /api/whatsapp/messages/by_phone/?phone_number=+79991234567
```

#### WhatsAppContactViewSet
REST API для управления контактами.

```
GET/POST /api/whatsapp/contacts/
GET /api/whatsapp/contacts/{id}/
PATCH /api/whatsapp/contacts/{id}/
```

#### SendMessageView
API для отправки сообщения.

```
POST /api/whatsapp/send-message/
Body:
{
    "config_id": "uuid",
    "phone_number": "+79991234567",
    "message": "text",
    "content_type": "Lead",
    "object_id": "uuid"
}
```

#### WebhookView
API для получения вебхуков от WhatsApp.

```
GET /api/whatsapp/webhook/  # Verification
   ?hub.mode=subscribe
   &hub.challenge=xxx
   &hub.verify_token=xxx

POST /api/whatsapp/webhook/  # Events
Body: {
    "object": "whatsapp_business_account",
    "entry": [...]
}
```

### 4. Utils (utils.py)

Вспомогательные функции для использования в других приложениях.

```python
Functions:
├── get_whatsapp_service(company_id, branch_id) → WhatsAppService
├── send_whatsapp_message(company_id, phone_number, message, ...) → WhatsAppMessage
├── send_whatsapp_template(company_id, phone_number, template_name, ...) → WhatsAppMessage
└── get_contact_conversations(company_id, phone_number, limit) → List[WhatsAppMessage]
```

## Flow Диаграммы

### Отправка сообщения

```
send_whatsapp_message()
    ↓
WhatsAppService.send_message()
    ├── WhatsAppAPIClient.send_text_message()
    │   └── HTTP POST → WhatsApp API
    ├── WhatsAppMessage.objects.create()
    │   └── Сохранение в БД
    ├── WhatsAppContact.objects.get_or_create()
    │   └── Обновление контакта
    └── return WhatsAppMessage
```

### Получение входящего сообщения

```
WebhookView.post(webhook_data)
    ↓
WhatsAppConfig.get(phone_number_id=...)
    ↓
WhatsAppService.handle_webhook()
    ├── _process_inbound_message()
    │   ├── WhatsAppMessage.objects.create()
    │   ├── WhatsAppContact.get_or_create()
    │   ├── send_message() (auto-reply если включен)
    │   └── mark_as_read()
    └── _process_message_status()
        └── WhatsAppMessage.objects.update()
```

## Безопасность

### Аутентификация

- **REST API:** Django `IsAuthenticated` permission
- **Webhook:** Token verification (`webhook_verify_token`)
- **Access Token:** Хранится в БД, не выводится в API

### Permissions

```
API Endpoints:
├── WhatsAppConfigViewSet - требует company match
├── WhatsAppMessageViewSet - требует company match
├── WhatsAppContactViewSet - требует company match
├── SendMessageView - требует company match
└── WebhookView - публичен (защищен token verification)
```

### Data Isolation

- Все модели связаны с `company` и опционально с `branch`
- Queryset фильтруется по компании пользователя
- Нет доступа к данным других компаний

## Масштабируемость

### Асинхронные операции

Для массовой рассылки используйте Celery:

```python
@shared_task
def send_bulk_message(company_id, message_text, contacts):
    for contact in contacts:
        send_whatsapp_message(company_id, contact.phone, message_text)
```

### Caching

Используйте Redis для кеширования конфигураций:

```python
from django.core.cache import cache

config = cache.get_or_set(
    f"whatsapp_config_{company_id}",
    WhatsAppConfig.objects.get(...),
    timeout=3600
)
```

### Batch Processing

Для массовой обработки событий:

```python
messages = WhatsAppMessage.objects.filter(
    status="sent"
).batch_size(100)
```

## Интеграция с другими приложениями

### Паттерн 1: Signals

```python
from django.db.models.signals import post_save
from apps.leads.models import Lead

@receiver(post_save, sender=Lead)
def notify_on_lead_creation(sender, instance, created, **kwargs):
    if created:
        send_whatsapp_message(...)
```

### Паттерн 2: Celery Tasks

```python
@shared_task
def send_notification(lead_id):
    lead = Lead.objects.get(id=lead_id)
    send_whatsapp_message(...)
```

### Паттерн 3: Middleware

```python
class WhatsAppNotificationMiddleware:
    def process_view(self, request, ...):
        # После успешного действия
        send_whatsapp_message(...)
```

## Мониторинг и Логирование

### Logger

```python
import logging
logger = logging.getLogger("apps.construction.integrations.whatsapp.services")

logger.info("Сообщение отправлено")
logger.error("Ошибка отправки")
logger.warning("Конфигурация неактивна")
```

### Метрики

```python
# В Django Admin
stats = {
    "total_messages": messages.count(),
    "success_rate": delivered / total * 100,
    "response_time": avg_time,
}
```

## Тестирование

### Unit Tests

```python
class WhatsAppServiceTestCase(TestCase):
    def test_send_message(self):
        service = WhatsAppService(config)
        message = service.send_message("+79991234567", "Test")
        self.assertEqual(message.status, "sent")
    
    def test_handle_webhook(self):
        data = {"entry": [...]}
        service.handle_webhook(data)
        # Assert message created
```

### Integration Tests

```python
class WebhookTestCase(TestCase):
    def test_webhook_verification(self):
        response = self.client.get(
            "/api/whatsapp/webhook/",
            {
                "hub.verify_token": "token",
                "hub.challenge": "challenge"
            }
        )
        self.assertEqual(response.status_code, 200)
```

## Миграции и обновления

### Создание миграций

```bash
python manage.py makemigrations apps.construction.integrations.whatsapp
python manage.py migrate apps.construction.integrations.whatsapp
```

### Обновление моделей

1. Отредактируйте `models.py`
2. Создайте миграцию: `makemigrations`
3. Примените: `migrate`
4. Обновите serializers и views если нужно

## Производительность

### Indexes

```
Models имеют индексы на:
- WhatsAppMessage.config + created_at
- WhatsAppMessage.phone_number
- WhatsAppMessage.status
- WhatsAppContact.config + phone_number
```

### Query Optimization

```python
# Bad
messages = config.messages.all()

# Good
messages = config.messages.select_related("config").filter(
    direction="inbound"
).values_list("id", "content")
```

## Будущее развитие

- [ ] Поддержка медиафайлов (images, documents)
- [ ] Группировка сообщений в цепочки
- [ ] AI-powered responses
- [ ] Advanced analytics
- [ ] Scheduled messages
- [ ] Multi-channel (Telegram, SMS)
- [ ] A/B testing
- [ ] CRM integration

---

**Документ обновлен:** 14 июля 2024
