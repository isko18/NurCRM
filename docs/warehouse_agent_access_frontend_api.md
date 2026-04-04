# Warehouse Agent Access Frontend API

Документ для фронта по выдаче агенту доступа к складу в модуле `warehouse`.

Поддерживаются два сценария:
- владелец или админ сам назначает пользователя агентом;
- пользователь подает заявку, а владелец или админ принимает ее.

Связанные файлы в коде: `apps/warehouse/views.py`, `apps/warehouse/urls.py`, `apps/warehouse/serializers.py`, `apps/warehouse/services.py`, `apps/warehouse/models.py`.

---

## Обзор

Доступ агента к складу хранится в модели `CompanyWarehouseAgent`.

Основные состояния:
- `pending` - заявка создана, ждет решения;
- `active` - агент активен и имеет доступ к складам компании;
- `rejected` - заявка отклонена;
- `removed` - агент отстранен, доступ снят.

Дополнительно у активного агента можно:
- указать `assigned_warehouse`, чтобы агент работал только с одним конкретным складом компании;
- включить `common_access_enabled=true` и указать `common_warehouse`. Это открывает агенту доступ к общему остатку конкретного склада.

---

## Аутентификация

Все методы требуют авторизации:

```http
Authorization: Bearer <access_token>
```

Базовый префикс:

```http
/api/warehouse/
```

---

## Быстро Для Фронта

Если нужно выдать агенту доступ только к одному складу, фронт должен передавать поле `assigned_warehouse`.

Минимальный payload:

```json
{
  "user": "22222222-2222-2222-2222-222222222222",
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

Если одновременно нужен доступ к общему остатку, payload должен быть таким:

```json
{
  "user": "22222222-2222-2222-2222-222222222222",
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

Правила для фронта:
- `assigned_warehouse` - необязательное поле, но если оно передано, агент работает только с этим складом;
- `common_access_enabled=true` требует `common_warehouse`;
- если переданы и `assigned_warehouse`, и `common_warehouse`, они должны совпадать;
- после успешного назначения в ответе приходит `assigned_warehouse`, его нужно сохранять в состоянии экрана и показывать пользователю как выбранный склад доступа.

---

## Сценарий 1. Владелец сам выдает доступ агенту

Используйте этот сценарий, если не нужна предварительная заявка от агента.

### Эндпоинт

```http
POST /api/warehouse/agents/company-memberships/
```

### Body

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|----------|
| `user` | UUID | да | ID пользователя, которому выдаем доступ агента |
| `assigned_warehouse` | UUID \| null | нет | Если передан, агент получает доступ только к этому складу |
| `common_access_enabled` | boolean | нет | Включить доступ к общему остатку склада |
| `common_warehouse` | UUID \| null | условно | Обязателен, если `common_access_enabled=true` |

### Пример запроса

```json
{
  "user": "22222222-2222-2222-2222-222222222222",
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

### Что делает

- создает новую связь агента с компанией, если ее еще нет;
- если связь уже была, обновляет ее;
- приводит статус к `active`;
- если передан `assigned_warehouse`, ограничивает агенту доступ только этим складом;
- при передаче `common_access_enabled=true` открывает общий доступ к выбранному складу.

### Успешный ответ

Статус:
- `201 Created` - если агент назначен впервые;
- `200 OK` - если запись уже существовала и была обновлена.

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

### Ошибки

| Код | Описание |
|-----|----------|
| 400 | Не передан `user` |
| 400 | Пользователь не найден |
| 400 | `assigned_warehouse` принадлежит другой компании |
| 400 | Включен общий доступ, но не передан `common_warehouse` |
| 400 | `common_warehouse` не совпадает с `assigned_warehouse` |
| 400 | Указанный склад принадлежит другой компании |
| 403 | Запрос делает не владелец/админ или у пользователя нет компании |

Примеры:

```json
{"user": "Укажите пользователя (id)."}
```

```json
{"user": "Пользователь не найден."}
```

```json
{"common_warehouse": "Укажите склад, если включен общий доступ."}
```

```json
{"common_warehouse": "Склад принадлежит другой компании."}
```

```json
{"common_warehouse": "Общий доступ можно открыть только к назначенному складу агента."}
```

```json
{"detail": "Только владелец/админ."}
```

---

## Сценарий 2. Агент подает заявку, владелец ее принимает

Этот сценарий нужен, когда пользователь сам хочет стать агентом компании.

### Шаг 1. Найти компанию

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

### Шаг 2. Подать заявку

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

Успешный ответ:
- `201 Created` - новая заявка создана;
- `200 OK` - если такая заявка уже есть в статусе `pending`.

Пример ответа:

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "company": "550e8400-e29b-41d4-a716-446655440000",
  "company_name": "Nur Trade",
  "user": "22222222-2222-2222-2222-222222222222",
  "user_display": "Agent User",
  "status": "pending",
  "note": "Хочу работать по вашему складу",
  "common_access_enabled": false,
  "common_warehouse": null,
  "created_at": "2026-04-04T10:00:00Z",
  "updated_at": "2026-04-04T10:00:00Z",
  "decided_at": null,
  "decided_by": null,
  "decided_by_display": null
}
```

Ошибки:

| Код | Описание |
|-----|----------|
| 400 | Не указана компания |
| 400 | Компания не найдена |
| 400 | Пользователь уже активный агент этой компании |
| 400 | Заявка ранее была отклонена |
| 400 | Владелец/админ пытается отправить заявку |

### Шаг 3. Владелец принимает заявку

```http
POST /api/warehouse/agents/company-requests/<request_id>/accept/
```

Что происходит:
- статус меняется с `pending` на `active`;
- агент получает доступ к складам компании.

Пример ответа:

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "status": "active",
  "decided_at": "2026-04-04T10:05:00Z",
  "decided_by": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "decided_by_display": "owner@example.com"
}
```

### Шаг 4. При необходимости включить доступ к общему остатку

```http
PATCH /api/warehouse/agents/company-requests/<request_id>/common-access/
```

Body:

```json
{
  "assigned_warehouse": "11111111-1111-1111-1111-111111111111",
  "common_access_enabled": true,
  "common_warehouse": "11111111-1111-1111-1111-111111111111"
}
```

Что делает:
- если передан `assigned_warehouse`, ограничивает агента одним складом компании;
- включает агенту доступ к общему остатку склада;
- выбранный склад должен принадлежать той же компании.

### Отключить общий доступ

```json
{
  "common_access_enabled": false,
  "common_warehouse": null
}
```

---

## Снять доступ у агента

Если нужно отключить агента от компании:

```http
POST /api/warehouse/agents/company-requests/<request_id>/remove/
```

Что делает:
- переводит статус `active` -> `removed`;
- агент теряет доступ к складам этой компании.

Пример ответа:

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "status": "removed"
}
```

---

## Получение списка заявок и активных агентов

### Список заявок

```http
GET /api/warehouse/agents/company-requests/
```

Поведение:
- агент видит свои заявки;
- владелец или админ видит заявки своей компании.

### Фильтр по статусу

```http
GET /api/warehouse/agents/company-requests/?status=active
```

Поддерживаемые статусы:
- `pending`
- `active`
- `rejected`
- `removed`

Это удобно для экранов:
- "Входящие заявки";
- "Мои заявки";
- "Активные агенты";
- "Отстраненные агенты".

---

## Что означает `common_access_enabled`

Если задан `assigned_warehouse`, агент видит и использует только этот склад в модуле `warehouse`.

Если `common_access_enabled=true` и задан `common_warehouse`, агент может работать с общим остатком указанного склада.

Это отдельная настройка поверх обычного статуса `active`:
- `active` дает доступ к компании и ее складским сущностям;
- `assigned_warehouse` сужает этот доступ до одного склада;
- `common_access_enabled=true` дополнительно разрешает операции с общим остатком конкретного склада.

Если `common_access_enabled=false`, поле `common_warehouse` должно быть `null`.

---

## Рекомендуемый фронтовый флоу

### Вариант A. Владелец сам назначает агента

1. Фронт получает пользователя, которому нужно выдать доступ.
2. Фронт дает выбрать склад для общего доступа.
3. Фронт вызывает `POST /api/warehouse/agents/company-memberships/`.
4. После успеха обновляет список активных агентов через `GET /api/warehouse/agents/company-requests/?status=active`.

### Вариант B. Агент сам отправляет заявку

1. Пользователь ищет компанию через `GET /api/warehouse/agents/companies/search/`.
2. Пользователь отправляет заявку через `POST /api/warehouse/agents/company-requests/`.
3. Владелец открывает список `GET /api/warehouse/agents/company-requests/?status=pending`.
4. Владелец подтверждает заявку через `POST /api/warehouse/agents/company-requests/<id>/accept/`.
5. При необходимости владелец отдельно включает общий доступ через `PATCH /api/warehouse/agents/company-requests/<id>/common-access/`.

---

## Чеклист для фронта

- на форме прямого назначения обязательно передавать `user`;
- если агенту нужен доступ только к одному складу, передавать `assigned_warehouse`;
- если включили тумблер общего доступа, обязательно передавать `common_warehouse`;
- после успешного назначения или подтверждения обновлять список агентов;
- для редактирования общего доступа использовать `PATCH /agents/company-requests/<id>/common-access/`;
- для отключения агента использовать `POST /agents/company-requests/<id>/remove/`.

---

## URL names

- `warehouse-agents-companies-search`
- `warehouse-agents-company-requests`
- `warehouse-agents-company-request-accept`
- `warehouse-agents-company-request-reject`
- `warehouse-agents-company-request-remove`
- `warehouse-agents-company-request-common-access`
- `warehouse-agents-company-memberships`
