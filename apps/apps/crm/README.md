# CRM система с интеграцией Wazzupp

CRM система с воронкой продаж и интеграцией WhatsApp/Instagram через Wazzupp API.

## Основные возможности

- **Воронка продаж**: Создание воронок с настраиваемыми стадиями
- **Контакты**: Управление контактами клиентов
- **Лиды**: Работа с потенциальными клиентами
- **Сделки**: Управление сделками и их стадиями
- **Интеграция Wazzupp**: Отправка и получение сообщений через WhatsApp/Instagram
- **Активности**: История взаимодействий с клиентами

## API Endpoints

### Воронки продаж

- `GET /api/crm/funnels/` - Список воронок
- `POST /api/crm/funnels/` - Создать воронку
- `GET /api/crm/funnels/{id}/` - Детали воронки
- `GET /api/crm/funnels/{id}/statistics/` - Статистика по воронке
- `PUT /api/crm/funnels/{id}/` - Обновить воронку
- `DELETE /api/crm/funnels/{id}/` - Удалить воронку

### Стадии воронки

- `GET /api/crm/funnel-stages/` - Список стадий
- `POST /api/crm/funnel-stages/` - Создать стадию
- `GET /api/crm/funnel-stages/{id}/` - Детали стадии
- `PUT /api/crm/funnel-stages/{id}/` - Обновить стадию
- `DELETE /api/crm/funnel-stages/{id}/` - Удалить стадию

### Контакты

- `GET /api/crm/contacts/` - Список контактов
- `POST /api/crm/contacts/` - Создать контакт
- `GET /api/crm/contacts/{id}/` - Детали контакта
- `PUT /api/crm/contacts/{id}/` - Обновить контакт
- `DELETE /api/crm/contacts/{id}/` - Удалить контакт

**Фильтры:**
- `phone` - поиск по телефону
- `email` - поиск по email
- `first_name`, `last_name` - поиск по имени
- `is_active` - активные/неактивные
- `is_client` - клиенты/не клиенты
- `source` - источник контакта
- `owner` - ответственный
- `branch` - филиал

### Лиды

- `GET /api/crm/leads/` - Список лидов
- `POST /api/crm/leads/` - Создать лид
- `GET /api/crm/leads/{id}/` - Детали лида
- `PUT /api/crm/leads/{id}/` - Обновить лид
- `DELETE /api/crm/leads/{id}/` - Удалить лид
- `POST /api/crm/leads/{id}/move_to_stage/` - Переместить в стадию
- `POST /api/crm/leads/{id}/convert_to_deal/` - Конвертировать в сделку

**Фильтры:**
- `title` - поиск по названию
- `funnel` - воронка
- `stage` - стадия
- `owner` - ответственный
- `source` - источник

### Сделки

- `GET /api/crm/deals/` - Список сделок
- `POST /api/crm/deals/` - Создать сделку
- `GET /api/crm/deals/{id}/` - Детали сделки
- `PUT /api/crm/deals/{id}/` - Обновить сделку
- `DELETE /api/crm/deals/{id}/` - Удалить сделку
- `POST /api/crm/deals/{id}/move_to_stage/` - Переместить в стадию
- `POST /api/crm/deals/{id}/mark_won/` - Пометить как выигранную
- `POST /api/crm/deals/{id}/mark_lost/` - Пометить как проигранную

**Фильтры:**
- `title` - поиск по названию
- `funnel` - воронка
- `stage` - стадия
- `owner` - ответственный
- `is_won` - выигранные
- `is_lost` - проигранные
- `expected_close_date` - ожидаемая дата закрытия

### Wazzupp аккаунты

- `GET /api/crm/wazzupp-accounts/` - Список аккаунтов
- `POST /api/crm/wazzupp-accounts/` - Создать аккаунт
- `GET /api/crm/wazzupp-accounts/{id}/` - Детали аккаунта
- `PUT /api/crm/wazzupp-accounts/{id}/` - Обновить аккаунт
- `DELETE /api/crm/wazzupp-accounts/{id}/` - Удалить аккаунт
- `POST /api/crm/wazzupp-accounts/{id}/check_connection/` - Проверить подключение
- `POST /api/crm/wazzupp-accounts/{id}/sync_messages/` - Синхронизировать сообщения
- `POST /api/crm/wazzupp-accounts/{id}/send_message/` - Отправить сообщение

**Пример создания аккаунта:**
```json
{
  "api_key": "your-api-key",
  "api_url": "https://api.wazzupp.com",
  "instance_id": "your-instance-id",
  "integration_type": "whatsapp"
}
```

**Пример отправки сообщения:**
```json
{
  "to": "79991234567",
  "message": "Привет! Это тестовое сообщение",
  "message_type": "text"
}
```

### Сообщения Wazzupp

- `GET /api/crm/wazzupp-messages/` - Список сообщений
- `GET /api/crm/wazzupp-messages/{id}/` - Детали сообщения

**Фильтры:**
- `account` - аккаунт Wazzupp
- `contact` - контакт
- `lead` - лид
- `is_incoming` - входящие/исходящие
- `is_read` - прочитанные/непрочитанные
- `message_type` - тип сообщения
- `status` - статус доставки

### Активности

- `GET /api/crm/activities/` - Список активностей
- `POST /api/crm/activities/` - Создать активность
- `GET /api/crm/activities/{id}/` - Детали активности
- `PUT /api/crm/activities/{id}/` - Обновить активность
- `DELETE /api/crm/activities/{id}/` - Удалить активность

**Типы активностей:**
- `call` - Звонок
- `meeting` - Встреча
- `email` - Email
- `message` - Сообщение
- `note` - Заметка
- `task` - Задача
- `stage_change` - Смена стадии

## Примеры использования

### Создание воронки продаж

```python
POST /api/crm/funnels/
{
  "name": "Продажи услуг",
  "description": "Воронка для продажи услуг",
  "is_active": true,
  "stages": [
    {"name": "Новый лид", "order": 1, "color": "#3498db"},
    {"name": "Квалификация", "order": 2, "color": "#9b59b6"},
    {"name": "Предложение", "order": 3, "color": "#f39c12"},
    {"name": "Закрыта успешно", "order": 4, "color": "#27ae60", "is_final": true, "is_success": true}
  ]
}
```

### Создание контакта

```python
POST /api/crm/contacts/
{
  "first_name": "Иван",
  "last_name": "Иванов",
  "phone": "+79991234567",
  "email": "ivan@example.com",
  "whatsapp": "+79991234567",
  "source": "WhatsApp"
}
```

### Создание лида

```python
POST /api/crm/leads/
{
  "contact": "uuid-контакта",
  "funnel": "uuid-воронки",
  "title": "Потенциальный клиент",
  "description": "Заинтересован в услугах",
  "estimated_value": 50000,
  "probability": 50,
  "source": "WhatsApp"
}
```

### Перемещение лида в стадию

```python
POST /api/crm/leads/{lead_id}/move_to_stage/
{
  "stage_id": "uuid-стадии"
}
```

### Конвертация лида в сделку

```python
POST /api/crm/leads/{lead_id}/convert_to_deal/
```

### Отправка сообщения через Wazzupp

```python
POST /api/crm/wazzupp-accounts/{account_id}/send_message/
{
  "to": "79991234567",
  "message": "Здравствуйте! Мы готовы обсудить ваше предложение.",
  "message_type": "text"
}
```

### Синхронизация сообщений

```python
POST /api/crm/wazzupp-accounts/{account_id}/sync_messages/
```

## Настройка Wazzupp

1. Создайте аккаунт в Wazzupp
2. Получите API ключ и URL API
3. Создайте инстанс в Wazzupp
4. Добавьте аккаунт в CRM через API или админку
5. Проверьте подключение через `check_connection`
6. Начните синхронизацию сообщений через `sync_messages`

## Модели данных

### SalesFunnel (Воронка продаж)
- `name` - Название воронки
- `company` - Компания
- `is_active` - Активна ли воронка
- `stages` - Стадии воронки

### FunnelStage (Стадия воронки)
- `funnel` - Воронка
- `name` - Название стадии
- `order` - Порядок
- `color` - Цвет стадии
- `is_final` - Финальная стадия
- `is_success` - Успешная стадия

### Contact (Контакт)
- `first_name`, `last_name`, `middle_name` - ФИО
- `phone`, `email`, `whatsapp`, `instagram` - Контакты
- `company_name` - Название компании клиента
- `source` - Источник контакта
- `is_active` - Активен
- `is_client` - Является клиентом

### Lead (Лид)
- `contact` - Контакт
- `funnel` - Воронка
- `stage` - Текущая стадия
- `title` - Название лида
- `estimated_value` - Оценочная стоимость
- `probability` - Вероятность закрытия (%)

### Deal (Сделка)
- `lead` - Лид (опционально)
- `contact` - Контакт
- `funnel` - Воронка
- `stage` - Текущая стадия
- `amount` - Сумма сделки
- `is_won` - Выиграна
- `is_lost` - Проиграна
- `expected_close_date` - Ожидаемая дата закрытия

### WazzuppAccount (Аккаунт Wazzupp)
- `api_key` - API ключ
- `api_url` - URL API
- `instance_id` - ID инстанса
- `integration_type` - Тип интеграции (whatsapp/instagram/telegram)
- `is_connected` - Подключен

### WazzuppMessage (Сообщение)
- `account` - Аккаунт Wazzupp
- `contact` - Контакт
- `message_id` - ID сообщения в Wazzupp
- `from_number`, `to_number` - От кого / Кому
- `message_type` - Тип сообщения
- `text` - Текст сообщения
- `is_incoming` - Входящее
- `status` - Статус доставки

### Activity (Активность)
- `contact`, `lead`, `deal` - Связи
- `activity_type` - Тип активности
- `title` - Название
- `description` - Описание
- `activity_date` - Дата активности
