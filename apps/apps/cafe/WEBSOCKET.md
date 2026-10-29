# WebSocket для приложения Cafe

## Описание

В приложении `cafe` реализована поддержка WebSocket для получения уведомлений в реальном времени о создании и обновлении заказов и столов.

## Endpoints

### 1. WebSocket для заказов (Orders)

**URL:** `ws/cafe/orders/`

**Параметры подключения:**
- `token` (обязательный) - JWT токен для аутентификации
- `branch_id` (опциональный) - UUID филиала (только для owner/admin, для выбора конкретного филиала)

**Пример подключения (минимальный):**
```
ws://your-domain/ws/cafe/orders/?token=<JWT>
```

**Пример подключения (с выбором филиала для owner/admin):**
```
ws://your-domain/ws/cafe/orders/?token=<JWT>&branch_id=<uuid>
```

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена пользователя
- Для обычных сотрудников используется их филиал из профиля
- Для owner/admin можно указать `branch_id` в query для выбора конкретного филиала

**События:**

1. **connection_established** - Подтверждение подключения
   ```json
   {
     "type": "connection_established",
     "company_id": "uuid",
     "branch_id": "uuid",
     "group": "cafe_orders_company_id_branch_id"
   }
   ```

2. **order_created** - Уведомление о создании заказа
   ```json
   {
     "type": "order_created",
     "data": {
       "order": { /* данные заказа */ },
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```

3. **order_updated** - Уведомление об обновлении заказа
   ```json
   {
     "type": "order_updated",
     "data": {
       "order": { /* данные заказа */ },
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```

4. **kitchen_task_ready** - Уведомление о готовности блюда (задачи кухни)
   ```json
   {
     "type": "kitchen_task_ready",
     "data": {
       "task": { /* данные KitchenTask */ },
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

5. **table_status_changed** - Уведомление об изменении статуса стола (FREE/BUSY)
   ```json
   {
     "type": "table_status_changed",
     "data": {
       "table": { /* данные стола */ },
       "table_id": "uuid",
       "table_number": 1,
       "status": "busy",
       "status_display": "Занят",
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```
   
   **Важно:** Это событие отправляется автоматически при:
   - Создании заказа (стол становится BUSY)
   - Закрытии/отмене заказа (стол становится FREE)
   - Удалении заказа (если нет других открытых заказов на стол)

### 2. WebSocket для кухни (Kitchen / повара)

**URL:** `ws/cafe/kitchen/`

**Параметры подключения:**
- `token` (обязательный) - JWT токен для аутентификации
- `branch_id` (опциональный) - UUID филиала (только для owner/admin, для выбора конкретного филиала)

**Пример подключения (минимальный):**
```
ws://your-domain/ws/cafe/kitchen/?token=<JWT>
```

**Пример подключения (с выбором филиала для owner/admin):**
```
ws://your-domain/ws/cafe/kitchen/?token=<JWT>&branch_id=<uuid>
```

**События:**

1. **connection_established** - Подтверждение подключения
   ```json
   {
     "type": "connection_established",
     "company_id": "uuid",
     "branch_id": "uuid",
     "group": "cafe_kitchen_company_id_branch_id"
   }
   ```

2. **kitchen_task_ready** - Уведомление о готовности блюда (задачи кухни)
   ```json
   {
     "type": "kitchen_task_ready",
     "data": {
       "task": { /* данные KitchenTask */ },
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

### 3. WebSocket для столов (Tables)

**URL:** `ws/cafe/tables/`

**Параметры подключения:**
- `token` (обязательный) - JWT токен для аутентификации
- `branch_id` (опциональный) - UUID филиала (только для owner/admin, для выбора конкретного филиала)

**Пример подключения (минимальный):**
```
ws://your-domain/ws/cafe/tables/?token=<JWT>
```

**Пример подключения (с выбором филиала для owner/admin):**
```
ws://your-domain/ws/cafe/tables/?token=<JWT>&branch_id=<uuid>
```

**Важно:** 
- `company_id` и `branch_id` определяются автоматически из JWT токена пользователя
- Для обычных сотрудников используется их филиал из профиля
- Для owner/admin можно указать `branch_id` в query для выбора конкретного филиала

**События:**

1. **connection_established** - Подтверждение подключения
   ```json
   {
     "type": "connection_established",
     "company_id": "uuid",
     "branch_id": "uuid",
     "group": "cafe_tables_company_id_branch_id"
   }
   ```

2. **table_created** - Уведомление о создании стола
   ```json
   {
     "type": "table_created",
     "data": {
       "table": { /* данные стола */ },
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```

3. **table_updated** - Уведомление об обновлении стола
   ```json
   {
     "type": "table_updated",
     "data": {
       "table": { /* данные стола */ },
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```

4. **table_status_changed** - Уведомление об изменении статуса стола (FREE/BUSY)
   ```json
   {
     "type": "table_status_changed",
     "data": {
       "table": { /* данные стола */ },
       "table_id": "uuid",
       "table_number": 1,
       "status": "busy",
       "status_display": "Занят",
       "company_id": "uuid",
       "branch_id": "uuid"
     }
   }
   ```

## Пример использования (JavaScript)

```javascript
// Подключение к WebSocket для заказов
const token = 'your-jwt-token';
const branchId = 'your-branch-uuid'; // опционально, только для owner/admin

// Минимальный вариант (company и branch определяются из токена)
const ws = new WebSocket(
  `ws://your-domain/ws/cafe/orders/?token=${token}`
);

// С выбором филиала (для owner/admin)
// const ws = new WebSocket(
//   `ws://your-domain/ws/cafe/orders/?token=${token}&branch_id=${branchId}`
// );

ws.onopen = () => {
  console.log('WebSocket подключен');
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.type) {
    case 'connection_established':
      console.log('Подключение установлено:', data);
      break;
    case 'order_created':
      console.log('Создан новый заказ:', data.data.order);
      // Обновить UI со списком заказов
      break;
    case 'order_updated':
      console.log('Заказ обновлен:', data.data.order);
      // Обновить UI со списком заказов
      break;
    case 'kitchen_task_ready':
      console.log('Блюдо готово:', data.data);
      // Обновить ленту задач кухни / уведомить поваров
      break;
    case 'table_status_changed':
      console.log('Статус стола изменился:', data.data);
      // Обновить статус стола на карте зала
      // data.data.status: 'free' или 'busy'
      // data.data.table_number: номер стола
      updateTableStatus(data.data.table_id, data.data.status);
      break;
    default:
      console.log('Неизвестное событие:', data);
  }
};

// Функция для обновления статуса стола в UI
function updateTableStatus(tableId, status) {
  const tableElement = document.querySelector(`[data-table-id="${tableId}"]`);
  if (tableElement) {
    tableElement.classList.remove('free', 'busy');
    tableElement.classList.add(status);
    tableElement.textContent = status === 'free' ? 'Свободен' : 'Занят';
  }
}

ws.onerror = (error) => {
  console.error('WebSocket ошибка:', error);
};

ws.onclose = () => {
  console.log('WebSocket отключен');
};

// Отправка ping для проверки соединения
ws.send(JSON.stringify({ action: 'ping' }));
```

## Группы подписки

WebSocket использует группы Channels для отправки уведомлений:

- **Для заказов:**
  - С филиалом: `cafe_orders_{company_id}_{branch_id}`
  - Без филиала: `cafe_orders_{company_id}`

- **Для столов:**
  - С филиалом: `cafe_tables_{company_id}_{branch_id}`
  - Без филиала: `cafe_tables_{company_id}`

- **Для кухни:**
  - С филиалом: `cafe_kitchen_{company_id}_{branch_id}`
  - Без филиала: `cafe_kitchen_{company_id}`

## Когда отправляются уведомления

### Заказы (Orders):
- При создании заказа через `OrderListCreateView`
- При создании заказа через `ClientOrderListCreateView`
- При обновлении заказа через `OrderRetrieveUpdateDestroyView`
- При оплате заказа через `OrderPayView`
- При переводе задачи кухни в статус `READY` (через `/cafe/kitchen/tasks/<id>/ready/` или PATCH)

### Кухня (Kitchen):
- При переводе задачи кухни в статус `READY` (через `/cafe/kitchen/tasks/<id>/ready/` или PATCH)

### Столы (Tables):
- При создании стола через `TableListCreateView`
- При обновлении стола через `TableRetrieveUpdateDestroyView`
- **При изменении статуса стола (FREE/BUSY):**
  - Автоматически при создании заказа (стол → BUSY)
  - Автоматически при закрытии/отмене заказа (стол → FREE)
  - Автоматически при удалении заказа (если нет других открытых заказов)
  - Автоматически при оплате заказа с закрытием (стол → FREE)

## Отслеживание занятости столов в реальном времени

Система автоматически отслеживает статус столов (FREE/BUSY) в зависимости от заказов:

1. **При создании заказа:**
   - Стол автоматически становится `BUSY`
   - Отправляется событие `table_status_changed` в группы `cafe_orders_*` и `cafe_tables_*`

2. **При закрытии/отмене заказа:**
   - Стол автоматически становится `FREE`
   - Отправляется событие `table_status_changed`

3. **При удалении заказа:**
   - Проверяется наличие других открытых заказов на этот стол
   - Если открытых заказов нет - стол становится `FREE`
   - Отправляется событие `table_status_changed`

4. **При оплате заказа с закрытием:**
   - Стол автоматически становится `FREE`
   - Отправляется событие `table_status_changed`

**Все официанты, подключенные к WebSocket, получают уведомления о изменении статуса столов в реальном времени!**

## Безопасность

- Все WebSocket соединения требуют JWT токен для аутентификации
- Проверяется доступ пользователя к указанной компании
- Уведомления отправляются только в группы, соответствующие компании и филиалу пользователя

## Коды ошибок

- `4003` - Пользователь не аутентифицирован (невалидный или отсутствующий JWT токен)
- `4004` - У пользователя нет компании (пользователь не привязан к компании)
