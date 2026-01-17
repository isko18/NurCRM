# WebSocket для Cafe - Быстрый старт в Postman

## 🚀 Минимальный URL для подключения

### Для заказов:
```
ws://localhost:8000/ws/cafe/orders/?token=YOUR_JWT_TOKEN
```

### Для столов:
```
ws://localhost:8000/ws/cafe/tables/?token=YOUR_JWT_TOKEN
```

## 📝 Пошаговая инструкция

### 1. Получите JWT токен

**POST** `http://localhost:8000/api/users/login/`

**Body:**
```json
{
  "email": "your_email@example.com",
  "password": "your_password"
}
```

**Скопируйте `access` токен из ответа**

### 2. Подключитесь к WebSocket в Postman

1. Создайте новый **WebSocket Request**
2. Вставьте URL:
   ```
   ws://localhost:8000/ws/cafe/orders/?token=ВАШ_ТОКЕН
   ```
3. Нажмите **Connect**

### 3. Что вы увидите при подключении:

```json
{
  "type": "connection_established",
  "company_id": "auto-detected-from-token",
  "branch_id": "auto-detected-from-token",
  "group": "cafe_orders_..."
}
```

### 4. Отправьте ping для проверки:

```json
{"action": "ping"}
```

**Ответ:**
```json
{"type": "pong"}
```

### 5. Создайте заказ через HTTP API

**POST** `http://localhost:8000/api/cafe/orders/`

**Headers:**
```
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json
```

**Body:**
```json
{
  "table": "table-uuid",
  "guests": 2,
  "items": [
    {
      "menu_item": "menu-item-uuid",
      "quantity": 2
    }
  ]
}
```

### 6. Наблюдайте события в WebSocket:

Вы получите:
- `order_created` - новый заказ создан
- `table_status_changed` - статус стола изменился (FREE → BUSY)

## ⚙️ Опционально: Выбор филиала (только для owner/admin)

Если вы owner/admin и хотите выбрать конкретный филиал:

```
ws://localhost:8000/ws/cafe/orders/?token=YOUR_JWT_TOKEN&branch_id=BRANCH_UUID
```

## ⚠️ Важно

- **`company_id` и `branch_id` определяются автоматически из JWT токена**
- Вам **НЕ нужно** передавать их вручную
- Для обычных сотрудников используется их филиал из профиля
- Для owner/admin можно указать `branch_id` для выбора конкретного филиала

## 🔍 Коды ошибок

- `4003` - Пользователь не аутентифицирован (невалидный токен)
- `4004` - У пользователя нет компании

## 📋 Примеры событий

### order_created
```json
{
  "type": "order_created",
  "data": {
    "order": { /* данные заказа */ },
    "company_id": "...",
    "branch_id": "..."
  }
}
```

### table_status_changed
```json
{
  "type": "table_status_changed",
  "data": {
    "table_id": "...",
    "table_number": 1,
    "status": "busy",
    "status_display": "Занят"
  }
}
```
