# Warehouse Agents Frontend API

Документ для фронта по агентам склада в модуле `warehouse`.

Важно: это агенты склада из `/api/warehouse/`, не POS-агенты из `/api/main/agents/`.

## 1. Базовая информация

Базовый префикс:

```http
/api/warehouse/
```

Все методы требуют авторизации:

```http
Authorization: Bearer <access_token>
```

Агент склада - это обычный пользователь, которому выдан доступ к складам компании через модель `CompanyWarehouseAgent`.

Основные статусы агента:

| Статус | Описание |
|--------|----------|
| `pending` | Агент отправил заявку, владелец еще не ответил |
| `active` | Агент принят и имеет доступ |
| `rejected` | Заявка отклонена |
| `removed` | Агент отстранен |

Дополнительные настройки:

| Поле | Описание |
|------|----------|
| `assigned_warehouse` | Если указан, агент работает только с этим складом |
| `common_access_enabled` | Разрешает агенту работать с общим остатком склада |
| `common_warehouse` | Склад, к общему остатку которого открыт доступ |

## 2. Как выдать доступ агенту

Есть два сценария:

1. Владелец или админ сам назначает пользователя агентом.
2. Пользователь сам отправляет заявку, владелец или админ ее принимает.

## 3. Сценарий A: владелец назначает агента

Использовать, когда не нужна предварительная заявка от агента.

```http
POST /api/warehouse/agents/company-memberships/
```

Body:

```json
{
  "user": "22222222-2222-2222-2222-222222222222",
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

Поля:

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|----------|
| `user` | UUID | да | Пользователь, которому выдаем доступ агента |
| `assigned_warehouse` | UUID/null | нет | Ограничить агента одним складом |
| `common_access_enabled` | boolean | нет | Включить доступ к общему остатку |
| `common_warehouse` | UUID/null | условно | Обязателен, если `common_access_enabled=true` |

Правила:

- если передан `assigned_warehouse`, агент видит и использует только этот склад;
- если `common_access_enabled=true`, нужно передать `common_warehouse`;
- если переданы и `assigned_warehouse`, и `common_warehouse`, они должны совпадать;
- склад должен принадлежать компании владельца/админа.

Пример ответа:

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "company": "550e8400-e29b-41d4-a716-446655440000",
  "company_name": "Nur Trade",
  "user": "22222222-2222-2222-2222-222222222222",
  "user_display": "Agent User",
  "status": "active",
  "note": "",
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111",
  "created_at": "2026-04-04T10:00:00Z",
  "updated_at": "2026-04-04T10:00:00Z",
  "decided_at": "2026-04-04T10:00:00Z",
  "decided_by": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "decided_by_display": "owner@example.com"
}
```

## 4. Сценарий B: агент отправляет заявку

### 4.1 Поиск компании

```http
GET /api/warehouse/agents/companies/search/?search=<query>
```

Пример ответа:

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Nur Trade",
    "slug": "nur-trade"
  }
]
```

### 4.2 Отправить заявку

```http
POST /api/warehouse/agents/company-requests/
```

Body:

```json
{
  "company": "550e8400-e29b-41d4-a716-446655440000",
  "note": "Хочу работать по вашему складу"
}
```

Ответ:

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "company": "550e8400-e29b-41d4-a716-446655440000",
  "company_name": "Nur Trade",
  "user": "22222222-2222-2222-2222-222222222222",
  "user_display": "Agent User",
  "status": "pending",
  "note": "Хочу работать по вашему складу",
  "assigned_warehouse": null,
  "common_access_enabled": false,
  "common_warehouse": null,
  "created_at": "2026-04-04T10:00:00Z",
  "updated_at": "2026-04-04T10:00:00Z",
  "decided_at": null,
  "decided_by": null,
  "decided_by_display": null
}
```

### 4.3 Владелец принимает, отклоняет или отстраняет

```http
POST /api/warehouse/agents/company-requests/{request_id}/accept/
POST /api/warehouse/agents/company-requests/{request_id}/reject/
POST /api/warehouse/agents/company-requests/{request_id}/remove/
```

Принять можно только `pending` заявку. Отстранить можно только `active` агента.

### 4.4 Настроить склад и общий доступ

После принятия заявки владелец может ограничить агента конкретным складом и включить общий доступ:

```http
PATCH /api/warehouse/agents/company-requests/{request_id}/common-access/
```

Body:

```json
{
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

Отключить общий доступ:

```json
{
  "common_access_enabled": false,
  "common_warehouse": null
}
```

## 5. Список заявок и агентов

```http
GET /api/warehouse/agents/company-requests/
GET /api/warehouse/agents/company-requests/?status=pending
GET /api/warehouse/agents/company-requests/?status=active
GET /api/warehouse/agents/company-requests/?status=rejected
GET /api/warehouse/agents/company-requests/?status=removed
```

Поведение:

- агент видит свои заявки;
- владелец/админ видит заявки и активных агентов своей компании.

## 6. Заявки агента на товар

Агент запрашивает товар со склада через `agent-carts`.

Флоу:

```text
draft -> submitted -> approved / rejected
```

Эндпоинты:

| Метод | URL | Назначение |
|-------|-----|------------|
| GET | `/api/warehouse/agent-carts/` | Список заявок |
| POST | `/api/warehouse/agent-carts/` | Создать заявку |
| GET | `/api/warehouse/agent-carts/{id}/` | Детали заявки |
| PATCH | `/api/warehouse/agent-carts/{id}/` | Изменить черновик |
| DELETE | `/api/warehouse/agent-carts/{id}/` | Удалить черновик |
| POST | `/api/warehouse/agent-carts/{id}/submit/` | Отправить владельцу |
| POST | `/api/warehouse/agent-carts/{id}/approve/` | Одобрить заявку |
| POST | `/api/warehouse/agent-carts/{id}/reject/` | Отклонить заявку |
| POST | `/api/warehouse/agent-carts/{id}/create-sale/` | Создать SALE-документ по заявке |

Позиции заявки:

| Метод | URL | Назначение |
|-------|-----|------------|
| GET | `/api/warehouse/agent-cart-items/?cart={cart_id}` | Позиции заявки |
| POST | `/api/warehouse/agent-cart-items/` | Добавить позицию |
| PATCH | `/api/warehouse/agent-cart-items/{id}/` | Изменить позицию |
| DELETE | `/api/warehouse/agent-cart-items/{id}/` | Удалить позицию |

Создать заявку:

```json
{
  "warehouse": "11111111-1111-1111-1111-111111111111",
  "note": "Нужен товар на завтра"
}
```

Добавить позицию:

```json
{
  "cart": "cart-uuid",
  "product": "product-uuid",
  "quantity_requested": "5.000"
}
```

При одобрении заявки система:

- списывает товар со склада;
- добавляет товар в личный остаток агента `AgentStockBalance`;
- переводит заявку в `approved`.

## 7. Остатки агента

Агент смотрит свои товары:

```http
GET /api/warehouse/agents/me/products/
```

Query-параметры:

| Параметр | Описание |
|----------|----------|
| `search` | Поиск по названию, артикулу, штрихкоду |
| `product_group` | Фильтр по группе товара |
| `page` | Страница |
| `page_size` | Размер страницы |
| `order_by` | `date` или `-date` |

Владелец смотрит остатки всех агентов:

```http
GET /api/warehouse/owner/agents/products/
```

Поведение:

- если у агента `common_access_enabled=true`, `/agents/me/products/` показывает общий остаток склада;
- если общий доступ выключен, показывает личный остаток агента.

## 8. Документы агента

Агентские документы:

```http
GET /api/warehouse/agent/documents/
POST /api/warehouse/agent/documents/
GET /api/warehouse/agent/documents/{id}/
PATCH /api/warehouse/agent/documents/{id}/
DELETE /api/warehouse/agent/documents/{id}/
```

Проведение:

```http
POST /api/warehouse/documents/{id}/post/
POST /api/warehouse/documents/{id}/unpost/
```

Правила:

- агентские документы не могут быть `TRANSFER` и `INVENTORY`;
- если `use_common_stock=false`, списание идет с личного остатка агента;
- если `use_common_stock=true`, списание идет с общего склада;
- личный остаток агента нельзя увести в минус.

## 9. Продажа по заявке агента

После одобрения заявки можно создать SALE-документ:

```http
POST /api/warehouse/agent-carts/{cart_id}/create-sale/
```

Body:

```json
{
  "counterparty": "counterparty-uuid",
  "post": false,
  "payment_kind": "cash",
  "discount_percent": "0.00",
  "discount_amount": "0.00",
  "comment": "Продажа по заявке агента"
}
```

Правила:

- заявка должна быть в статусе `approved`;
- `counterparty` должен принадлежать этому агенту;
- по одной заявке можно создать только один SALE-документ;
- если `post=true`, документ сразу проводится.

## 10. Контрагенты агента

Контрагенты идут через общий CRUD:

```http
GET /api/warehouse/crud/counterparties/
POST /api/warehouse/crud/counterparties/
GET /api/warehouse/crud/counterparties/{id}/
PATCH /api/warehouse/crud/counterparties/{id}/
DELETE /api/warehouse/crud/counterparties/{id}/
```

У контрагента есть поле `agent`.

Правила:

- агент видит своих контрагентов;
- для агентского документа контрагент должен принадлежать этому агенту;
- агент должен быть сотрудником компании или активным агентом компании.

## 11. Аналитика агента

Агент смотрит свою аналитику:

```http
GET /api/warehouse/agents/me/analytics/
```

Владелец смотрит аналитику конкретного агента:

```http
GET /api/warehouse/owner/agents/{agent_id}/analytics/
```

Сводка по агентам:

```http
GET /api/warehouse/owner/agents/analytics/
```

Общая аналитика владельца:

```http
GET /api/warehouse/owner/analytics/
```

Типовые query-параметры:

| Параметр | Описание |
|----------|----------|
| `period` | `day`, `week`, `month`, `custom` |
| `date` | Дата для периода |
| `date_from` | Начало периода для `custom` |
| `date_to` | Конец периода для `custom` |
| `group_by` | Группировка |
| `limit` | Лимит для списков |
| `offset` | Смещение |
| `order_by` | Сортировка |

## 12. Рекомендуемые экраны фронта

### Экран владельца: агенты

1. Получить активных агентов:

```http
GET /api/warehouse/agents/company-requests/?status=active
```

2. Назначить нового агента:

```http
POST /api/warehouse/agents/company-memberships/
```

3. Изменить склад или общий доступ:

```http
PATCH /api/warehouse/agents/company-requests/{id}/common-access/
```

4. Отстранить агента:

```http
POST /api/warehouse/agents/company-requests/{id}/remove/
```

### Экран агента: стать агентом

1. Найти компанию:

```http
GET /api/warehouse/agents/companies/search/?search=...
```

2. Отправить заявку:

```http
POST /api/warehouse/agents/company-requests/
```

3. Проверять статус:

```http
GET /api/warehouse/agents/company-requests/
```

### Экран агента: мои товары

```http
GET /api/warehouse/agents/me/products/?search=...
```

### Экран агента: заявка на товар

1. Создать cart:

```http
POST /api/warehouse/agent-carts/
```

2. Добавить items:

```http
POST /api/warehouse/agent-cart-items/
```

3. Отправить заявку:

```http
POST /api/warehouse/agent-carts/{id}/submit/
```

### Экран владельца: выдача товара агенту

1. Получить заявки:

```http
GET /api/warehouse/agent-carts/?status=submitted
```

2. Одобрить или отклонить:

```http
POST /api/warehouse/agent-carts/{id}/approve/
POST /api/warehouse/agent-carts/{id}/reject/
```

## 13. Короткая схема работы

```text
Пользователь
  -> ищет компанию
  -> отправляет заявку
  -> владелец принимает
  -> агент получает active-доступ
  -> агент создает заявку на товар
  -> владелец одобряет
  -> товар попадает в AgentStockBalance
  -> агент продает товар через документы
  -> проведение списывает AgentStockBalance или общий склад
```

## 14. Частые ошибки

| Код | Когда бывает |
|-----|--------------|
| `400` | Не передан `user`, `company`, `warehouse`, неверный склад или товар |
| `400` | `common_access_enabled=true`, но нет `common_warehouse` |
| `400` | `common_warehouse` не совпадает с `assigned_warehouse` |
| `400` | Повторная заявка после `rejected` |
| `403` | Метод вызывает не владелец/админ |
| `403` | Агент пытается работать с чужой заявкой |
| `404` | Заявка не найдена или не в нужном статусе |

## 15. Главное для фронта

- Для прямого назначения агента используйте `POST /api/warehouse/agents/company-memberships/`.
- Для заявок агента используйте `GET/POST /api/warehouse/agents/company-requests/`.
- Для ограничения одним складом передавайте `assigned_warehouse`.
- Для продаж с общего склада включайте `common_access_enabled=true` и задавайте `common_warehouse`.
- Для товаров агента используйте `GET /api/warehouse/agents/me/products/`.
- Для запроса товара используйте `agent-carts` и `agent-cart-items`.
- Для продажи по заявке используйте `POST /api/warehouse/agent-carts/{id}/create-sale/`.
