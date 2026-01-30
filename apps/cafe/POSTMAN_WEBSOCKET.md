# WebSocket тестирование в Postman для Cafe

## Настройка WebSocket соединения в Postman

### 1. Создание нового WebSocket запроса

1. Откройте Postman
2. Нажмите **New** → **WebSocket Request**
3. Введите URL (см. ниже)

### 2. Endpoints для тестирования

#### A. WebSocket для заказов (Orders)

**URL:**
```
ws://localhost:8000/ws/cafe/orders/
```

**Query Parameters:**
```
token=<JWT_TOKEN>
branch_id=<UUID> (опционально, только для owner/admin)
```

**Полный URL пример (минимальный):**
```
ws://localhost:8000/ws/cafe/orders/?token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

**Полный URL пример (с выбором филиала для owner/admin):**
```
ws://localhost:8000/ws/cafe/orders/?token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...&branch_id=123e4567-e89b-12d3-a456-426614174001
```

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена пользователя
- Для обычных сотрудников используется их филиал
- Для owner/admin можно указать `branch_id` в query для выбора конкретного филиала

#### B. WebSocket для столов (Tables)

**URL:**
```
ws://localhost:8000/ws/cafe/tables/
```

**Query Parameters:**
```
token=<JWT_TOKEN>
branch_id=<UUID> (опционально, только для owner/admin)
```

**Полный URL пример (минимальный):**
```
ws://localhost:8000/ws/cafe/tables/?token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

**Полный URL пример (с выбором филиала для owner/admin):**
```
ws://localhost:8000/ws/cafe/tables/?token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...&branch_id=123e4567-e89b-12d3-a456-426614174001
```

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена пользователя
- Для обычных сотрудников используется их филиал
- Для owner/admin можно указать `branch_id` в query для выбора конкретного филиала

### 3. Получение JWT токена

Перед подключением к WebSocket нужно получить JWT токен через обычный HTTP запрос:

**POST** `http://localhost:8000/api/users/login/` (или ваш endpoint для авторизации)

**Body (JSON):**
```json
{
  "email": "waiter1@test.com",
  "password": "your_password"
}
```

**Response:**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh": "..."
}
```

Используйте значение `access` как `token` в WebSocket запросе.

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена пользователя
- Вам не нужно передавать эти параметры вручную
- Для owner/admin можно опционально указать `branch_id` для выбора конкретного филиала

### 4. Подключение в Postman

1. Вставьте URL с параметрами в поле адреса
2. Нажмите **Connect**
3. Если подключение успешно, вы увидите сообщение:
   ```json
   {
     "type": "connection_established",
     "company_id": "123e4567-e89b-12d3-a456-426614174000",
     "branch_id": "123e4567-e89b-12d3-a456-426614174001",
     "group": "cafe_orders_123e4567-e89b-12d3-a456-426614174000_123e4567-e89b-12d3-a456-426614174001"
   }
   ```

### 5. Отправка сообщений

#### Ping (проверка соединения)

**Отправить:**
```json
{
  "action": "ping"
}
```

**Ожидаемый ответ:**
```json
{
  "type": "pong"
}
```

### 6. Ожидаемые события

#### При создании заказа

**Событие:** `order_created`

**Сообщение:**
```json
{
  "type": "order_created",
  "data": {
    "order": {
      "id": "uuid",
      "table": "uuid",
      "client": "uuid",
      "waiter": "uuid",
      "guests": 2,
      "status": "open",
      "total_amount": "300.00",
      "created_at": "2024-01-01T12:00:00Z"
    },
    "company_id": "uuid",
    "branch_id": "uuid"
  }
}
```

#### При обновлении заказа

**Событие:** `order_updated`

**Сообщение:**
```json
{
  "type": "order_updated",
  "data": {
    "order": {
      "id": "uuid",
      "status": "closed",
      "total_amount": "300.00"
    },
    "company_id": "uuid",
    "branch_id": "uuid"
  }
}
```

#### При готовности блюда (задачи кухни)

**Событие:** `kitchen_task_ready`

**Сообщение:**
```json
{
  "type": "kitchen_task_ready",
  "data": {
    "task": {
      "id": "uuid",
      "status": "ready",
      "order": "uuid",
      "menu_item": "uuid",
      "table_number": 1
    },
    "task_id": "uuid",
    "order_id": "uuid",
    "table": 1,
    "menu_item": "Пицца",
    "unit_index": 1,
    "company_id": "uuid",
    "branch_id": "uuid"
  }
}
```

#### При изменении статуса стола

**Событие:** `table_status_changed`

**Сообщение:**
```json
{
  "type": "table_status_changed",
  "data": {
    "table": {
      "id": "uuid",
      "number": 1,
      "status": "busy",
      "places": 4
    },
    "table_id": "uuid",
    "table_number": 1,
    "status": "busy",
    "status_display": "Занят",
    "company_id": "uuid",
    "branch_id": "uuid"
  }
}
```

### 7. Пошаговая инструкция для тестирования

#### Шаг 1: Получите JWT токен

**HTTP Request:**
```
POST http://localhost:8000/api/auth/login/
Content-Type: application/json

{
  "email": "your_email@example.com",
  "password": "your_password"
}
```

Скопируйте `access` токен из ответа.

#### Шаг 2: Подключитесь к WebSocket

**WebSocket URL (минимальный):**
```
ws://localhost:8000/ws/cafe/orders/?token=<YOUR_JWT_TOKEN>
```

**WebSocket URL (с выбором филиала для owner/admin):**
```
ws://localhost:8000/ws/cafe/orders/?token=<YOUR_JWT_TOKEN>&branch_id=<BRANCH_ID>
```

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена
- Для обычных сотрудников используется их филиал из профиля
- Для owner/admin можно указать `branch_id` в query для выбора конкретного филиала

#### Шаг 4: Создайте заказ через HTTP API

**HTTP Request:**
```
POST http://localhost:8000/api/cafe/orders/
Authorization: Bearer <YOUR_JWT_TOKEN>
Content-Type: application/json

{
  "table": "<TABLE_UUID>",
  "client": "<CLIENT_UUID>",
  "waiter": "<WAITER_UUID>",
  "guests": 2,
  "items": [
    {
      "menu_item": "<MENU_ITEM_UUID>",
      "quantity": 2
    }
  ]
}
```

#### Шаг 5: Наблюдайте WebSocket сообщения

В Postman вы должны увидеть:
1. `connection_established` - при подключении
2. `order_created` - при создании заказа
3. `table_status_changed` - при изменении статуса стола (FREE → BUSY)
4. `kitchen_task_ready` - при переводе задачи кухни в статус READY

### 8. Примеры для разных сценариев

#### Сценарий 1: Создание заказа

1. Подключитесь к `ws://localhost:8000/ws/cafe/orders/`
2. Создайте заказ через HTTP API
3. Получите уведомления:
   - `order_created`
   - `table_status_changed` (status: "busy")

#### Сценарий 2: Закрытие заказа

1. Подключитесь к `ws://localhost:8000/ws/cafe/orders/`
2. Закройте заказ через HTTP API:
   ```
   PATCH http://localhost:8000/api/cafe/orders/<ORDER_ID>/
   {
     "status": "closed"
   }
   ```
3. Получите уведомления:
   - `order_updated`
   - `table_status_changed` (status: "free")

#### Сценарий 3: Отслеживание столов

1. Подключитесь к `ws://localhost:8000/ws/cafe/tables/`
2. Создайте/обновите заказ через HTTP API
3. Получите уведомление `table_status_changed` с актуальным статусом стола

#### Сценарий 4: Готовность блюда

1. Подключитесь к `ws://localhost:8000/ws/cafe/orders/`
2. Отметьте задачу кухни как готовую:
   ```
   POST http://localhost:8000/api/cafe/kitchen/tasks/<TASK_ID>/ready/
   ```
   или
   ```
   PATCH http://localhost:8000/api/cafe/kitchen/tasks/<TASK_ID>/
   {
     "status": "ready"
   }
   ```
3. Получите уведомление:
   - `kitchen_task_ready`

### 9. Troubleshooting

#### Ошибка: Connection closed with code 4003
- **Причина:** Пользователь не аутентифицирован
- **Решение:** Проверьте, что JWT токен валидный и не истек

#### Ошибка: Connection closed with code 4004
- **Причина:** У пользователя нет компании
- **Решение:** Убедитесь, что пользователь привязан к компании (через `company` или `owned_company`)

#### Нет сообщений после создания заказа
- **Причина:** Возможно, заказ создан для другой компании/филиала
- **Решение:** Убедитесь, что заказ создан для той же компании и филиала, что и пользователь из JWT токена

### 10. Полезные команды для тестирования

#### Проверка соединения
```json
{"action": "ping"}
```

#### Мониторинг всех событий
Подключитесь одновременно к:
- `ws://localhost:8000/ws/cafe/orders/`
- `ws://localhost:8000/ws/cafe/tables/`

Это позволит видеть все события в реальном времени.

### 11. Пример полного теста

1. **Получите токен:**
   ```
   POST /api/users/login/
   → Сохраните access токен
   ```

2. **Подключитесь к WebSocket (минимальный вариант):**
   ```
   ws://localhost:8000/ws/cafe/orders/?token=<TOKEN>
   ```
   
   Или с выбором филиала (для owner/admin):
   ```
   ws://localhost:8000/ws/cafe/orders/?token=<TOKEN>&branch_id=<BRANCH_ID>
   ```
   
   **Важно:** `company_id` и `branch_id` определяются автоматически из JWT токена!

3. **Отправьте ping:**
   ```json
   {"action": "ping"}
   ```

4. **Создайте заказ через HTTP:**
   ```
   POST /api/cafe/orders/
   ```

5. **Наблюдайте события в WebSocket:**
   - `order_created`
   - `table_status_changed`

6. **Закройте заказ:**
   ```
   PATCH /api/cafe/orders/<ID>/
   {"status": "closed"}
   ```

7. **Наблюдайте события:**
   - `order_updated`
   - `table_status_changed` (status: "free")
