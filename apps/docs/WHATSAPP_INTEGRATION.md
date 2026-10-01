# WhatsApp Интеграция для NurCRM

## Описание

Переиспользуемая интеграция с WhatsApp Business API для управления сообщениями в воронке продаж и других приложениях.

**Версия:** 1.0.0  
**Расположение:** `apps/construction/integrations/whatsapp/`

## Функциональность

✅ Отправка текстовых сообщений  
✅ Отправка сообщений по шаблонам  
✅ Получение входящих сообщений через вебхуки  
✅ Отслеживание статуса доставки (sent, delivered, read)  
✅ Автоответы на входящие сообщения  
✅ Управление контактами  
✅ История всех сообщений  
✅ Django Admin интерфейс  
✅ REST API для интеграции  

## Установка

### 1. Добавить приложение в INSTALLED_APPS

**Файл:** `core/settings.py`

```python
INSTALLED_APPS = [
    # ... другие приложения ...
    "apps.construction",
    # ... остальные ...
]
```

Приложение `apps.construction.integrations.whatsapp` автоматически инициализируется как подприложение.

### 2. Добавить URLs в основной urlpatterns

**Файл:** `core/urls.py`

```python
from django.urls import path, include

urlpatterns = [
    # ... другие urls ...
    path("api/whatsapp/", include("apps.construction.integrations.whatsapp.urls")),
    # ... остальные urls ...
]
```

### 3. Выполнить миграции

```bash
python manage.py makemigrations
python manage.py migrate
```

### 4. Установить зависимость requests

```bash
pip install requests
```

## Конфигурация WhatsApp

### Получение credentials от WhatsApp

1. Перейдите на [Meta Business Platform](https://business.facebook.com)
2. Создайте Business Account или используйте существующий
3. Создайте WhatsApp Business Account
4. Получите следующие данные:
   - **Phone Number** - Номер телефона в формате +7XXXXXXXXXX
   - **Business Account ID** - ID вашего бизнес-аккаунта
   - **Phone Number ID** - ID номера телефона в WhatsApp
   - **Access Token** - Bearer token для доступа к API

### Создание конфигурации через Django Admin

1. Откройте Django Admin (`/admin/`)
2. Перейдите в "WhatsApp Интеграция → Конфигурации WhatsApp"
3. Нажмите "Добавить конфигурацию"
4. Заполните форму:
   - **Компания** - выберите компанию
   - **Филиал** - (опционально) выберите филиал
   - **Номер телефона WhatsApp** - в формате +7XXXXXXXXXX
   - **ID бизнес-аккаунта WhatsApp** - скопируйте из Meta Business Platform
   - **ID номера телефона в WhatsApp** - скопируйте из Meta Business Platform
   - **Access Token** - скопируйте из Meta Business Platform
   - **Активна** - отметьте, чтобы активировать
   - **Автоответы включены** - отметьте, если нужны автоответы
   - **Текст автоответа** - текст, который будет отправлен на входящие сообщения

5. Сохраните конфигурацию

### Настройка Webhook

После сохранения конфигурации:

1. Скопируйте URL вебхука из поля "URL вебхука" в админ-панели
2. Перейдите в Meta Business Platform → WhatsApp → Настройки
3. В разделе "Webhook" укажите:
   - **Callback URL:** скопированный URL
   - **Verify Token:** значение из поля "Токен верификации вебхука"

4. Подпишитесь на события:
   - `messages` - входящие сообщения
   - `message_status` - статус отправленных сообщений

## Использование

### 1. Отправка обычного сообщения

```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

# Отправить простое сообщение
message = send_whatsapp_message(
    company_id="your-company-uuid",
    phone_number="+79991234567",
    message="Привет! Это сообщение от NurCRM",
)

# Результат
# message.status == "sent"
# message.whatsapp_message_id == "wamid.xxx"
```

### 2. Отправка сообщения с привязкой к объекту

```python
# Привязка к лиду
message = send_whatsapp_message(
    company_id="company-uuid",
    phone_number="+79991234567",
    message="Ваш заказ готов!",
    content_type="Lead",
    object_id="lead-uuid",
)
```

### 3. Отправка сообщения по шаблону

```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_template

message = send_whatsapp_template(
    company_id="company-uuid",
    phone_number="+79991234567",
    template_name="order_confirmation",
    parameters=[
        {"type": "text", "text": "Order #12345"},
        {"type": "text", "text": "5000 RUB"},
    ],
)
```

### 4. Использование WhatsAppService напрямую

```python
from apps.construction.integrations.whatsapp.models import WhatsAppConfig
from apps.construction.integrations.whatsapp.services import WhatsAppService

# Получить конфигурацию
config = WhatsAppConfig.objects.get(company_id=company_id, branch_id=None)

# Создать сервис
service = WhatsAppService(config)

# Отправить сообщение
message = service.send_message(
    phone_number="+79991234567",
    message="Текст сообщения",
    content_type="Contact",
    object_id="contact-uuid",
)

# Обработать вебхук
message = service.handle_webhook(webhook_data)
```

### 5. Использование в signals/tasks

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.leads.models import Lead
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@receiver(post_save, sender=Lead)
def notify_lead_created(sender, instance, created, **kwargs):
    if created and instance.phone_number:
        send_whatsapp_message(
            company_id=instance.company_id,
            phone_number=instance.phone_number,
            message=f"Спасибо за вашу заявку, {instance.first_name}! Мы скоро свяжемся с вами.",
            content_type="Lead",
            object_id=instance.id,
        )
```

### 6. Использование в Celery задачах

```python
from celery import shared_task
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@shared_task
def send_welcome_message(company_id, phone_number, customer_name):
    message = send_whatsapp_message(
        company_id=company_id,
        phone_number=phone_number,
        message=f"Добро пожаловать, {customer_name}! Мы рады видеть вас.",
        content_type="Customer",
        object_id=customer_name,
    )
    return str(message.id) if message else None
```

## REST API

### Отправка сообщения

**Endpoint:** `POST /api/whatsapp/send-message/`

**Требуется:** Аутентификация (IsAuthenticated)

**Request Body:**
```json
{
    "config_id": "uuid-конфигурации",
    "phone_number": "+79991234567",
    "message": "Текст сообщения",
    "content_type": "Lead",
    "object_id": "uuid-объекта"
}
```

**Response (201):**
```json
{
    "id": "uuid",
    "config": "uuid",
    "whatsapp_message_id": "wamid.xxx",
    "phone_number": "+79991234567",
    "message_type": "text",
    "content": "Текст сообщения",
    "direction": "outbound",
    "status": "sent",
    "created_at": "2024-07-14T10:00:00Z"
}
```

### Получить сообщения контакта

**Endpoint:** `GET /api/whatsapp/messages/?phone_number=+79991234567&config_id=uuid`

**Response:**
```json
[
    {
        "id": "uuid",
        "phone_number": "+79991234567",
        "direction": "outbound",
        "status": "delivered",
        "content": "Текст сообщения",
        "created_at": "2024-07-14T10:00:00Z"
    }
]
```

### Получить список конфигураций

**Endpoint:** `GET /api/whatsapp/configs/`

**Response:**
```json
[
    {
        "id": "uuid",
        "company": "uuid",
        "phone_number": "+79991234567",
        "is_active": true,
        "auto_reply_enabled": true,
        "created_at": "2024-07-14T10:00:00Z"
    }
]
```

### Получить статистику

**Endpoint:** `GET /api/whatsapp/configs/{id}/stats/`

**Response:**
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

## Модели данных

### WhatsAppConfig
Хранит конфигурацию подключения к WhatsApp.

**Ключевые поля:**
- `phone_number` - Номер телефона (+7XXXXXXXXXX)
- `business_account_id` - ID бизнес-аккаунта
- `access_token` - Bearer token
- `phone_number_id` - ID номера в WhatsApp
- `is_active` - Активность конфигурации
- `auto_reply_enabled` - Включены ли автоответы
- `auto_reply_message` - Текст автоответа
- `webhook_url` - URL вебхука
- `last_webhook_received` - Время последнего вебхука

### WhatsAppMessage
Хранит все сообщения (входящие и исходящие).

**Ключевые поля:**
- `whatsapp_message_id` - ID сообщения в WhatsApp (уникальный)
- `phone_number` - Номер контакта
- `direction` - "inbound" или "outbound"
- `status` - pending, sent, delivered, read, failed
- `message_type` - text, image, document, audio, video, location
- `content` - Содержание сообщения
- `media_url` - URL медиафайла (если есть)
- `metadata` - JSON с дополнительными данными
- `content_type` - Тип связанного объекта
- `object_id` - ID связанного объекта

### WhatsAppContact
Хранит контакты для отправки сообщений.

**Ключевые поля:**
- `phone_number` - Номер телефона
- `name` - Имя контакта
- `is_active` - Активен ли контакт
- `is_blocked` - Заблокирован ли
- `last_message_at` - Время последнего сообщения
- `content_type` - Тип связанного объекта
- `object_id` - ID связанного объекта

## Примеры интеграции с construction

### Отправка сообщения при создании заказа

**Файл:** `apps/construction/signals.py`

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order  # или ваша модель заказа
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@receiver(post_save, sender=Order)
def send_order_notification(sender, instance, created, **kwargs):
    if created and instance.customer_phone:
        send_whatsapp_message(
            company_id=instance.company_id,
            phone_number=instance.customer_phone,
            message=f"Спасибо за заказ! Ваш номер заказа: {instance.order_number}",
            content_type="Order",
            object_id=instance.id,
        )
```

### Интеграция в существующий view

```python
from rest_framework import viewsets
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

class OrderViewSet(viewsets.ModelViewSet):
    def perform_create(self, serializer):
        order = serializer.save()
        
        # Отправляем сообщение
        send_whatsapp_message(
            company_id=self.request.user.company_id,
            phone_number=order.customer_phone,
            message=f"Заказ #{order.id} создан!",
            content_type="Order",
            object_id=order.id,
        )
```

## Логирование

Все операции логируются в `logger.getLogger("apps.construction.integrations.whatsapp.services")`.

**Примеры логов:**
```
INFO - Сообщение отправлено: wamid.xxxxx
INFO - Входящее сообщение обработано: wamid.xxxxx
ERROR - Ошибка отправки сообщения: Network error
ERROR - Ошибка обработки вебхука: Invalid data
```

## Обработка ошибок

### Сообщение не отправляется

1. Проверьте, что конфигурация активна
2. Проверьте валидность номера телефона (формат +7XXXXXXXXXX)
3. Проверьте access_token в конфигурации
4. Проверьте логи приложения

### Вебхук не работает

1. Убедитесь, что URL вебхука правильно настроен в Meta Business Platform
2. Убедитесь, что токен верификации совпадает
3. Проверьте логи обработки вебхуков
4. Убедитесь, что конфигурация активна

### Автоответы не работают

1. Проверьте, что "Автоответы включены" в конфигурации
2. Проверьте, что текст автоответа не пуст
3. Проверьте логи при обработке входящих сообщений

## Миграция и обновления

### Текущая версия БД

Модели используют:
- UUID для ID
- JSONField для дополнительных данных
- DateTimeField с timezone support

### Создание миграций

```bash
python manage.py makemigrations apps.construction.integrations.whatsapp
python manage.py migrate apps.construction.integrations.whatsapp
```

## Безопасность

⚠️ **ВАЖНО:**

1. **Access Token** - никогда не коммитьте в git. Используйте переменные окружения или секреты
2. **Webhook Verify Token** - должен быть уникальным и сложным
3. **Permissions** - используется `IsAuthenticated` для всех API endpoints
4. **HTTPS** - webhook URL должен быть HTTPS

## Переиспользование в других приложениях

Эта интеграция полностью модульна и может быть использована в любом приложении:

```python
# В любом другом приложении/модуле
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

message = send_whatsapp_message(
    company_id=company_id,
    phone_number=phone,
    message="Text",
    branch_id=branch_id,  # опционально
)
```

## Дальнейшее развитие

### Планируемые функции:

- [ ] Отправка изображений, документов, видео
- [ ] Группировка сообщений в цепочки
- [ ] Интеграция с ИИ для умных ответов
- [ ] Расширенная аналитика и отчеты
- [ ] Поддержка других каналов (Telegram, SMS)
- [ ] Запланированные сообщения
- [ ] A/B тестирование сообщений
- [ ] Интеграция с CRM контактами

## Поддержка

Для вопросов и проблем обратитесь к разработчику.

---

**Последнее обновление:** 14 июля 2024  
**Версия:** 1.0.0
