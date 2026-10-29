# Выдача товара агенту от владельца (Frontend)

Документация для фронтенда по сценарию, когда **владелец/админ сам выбирает агента и отправляет ему товар** со склада — без предварительной заявки от агента.

Базовый префикс: **`/api/warehouse/`**  
Аутентификация: **JWT** (`Authorization: Bearer <access_token>`).

---

## 1. Два сценария

| | Сценарий агента (как было) | Сценарий владельца (новое) |
|---|---|---|
| Кто инициирует | Агент | Владелец/админ |
| Создание заявки | Агент, `agent` не передаётся | Владелец, **обязателен** `agent` |
| Промежуточный шаг | `submit` → ждёт одобрения | Не нужен |
| Выдача товара | `approve` (статус `submitted`) | **`dispatch`** (статус `draft`) |
| Итоговый статус | `approved` | `approved` |

Оба сценария используют одни и те же сущности: `AgentRequestCart` + `AgentRequestItem`.

---

## 2. Сценарий владельца (пошагово)

### Шаг 1 — получить список агентов

```http
GET /api/warehouse/agents/company-requests/?status=active
```

Ответ — массив членств. Для выбора агента в UI нужны поля:

| Поле | Для чего |
|------|----------|
| `user` | UUID агента → передаётся в `agent` при создании заявки |
| `user_display` | Имя в селекте |
| `assigned_warehouse` | Если указан — агент работает только с этим складом |

Альтернатива: назначить нового агента напрямую (если ещё не в списке):

```http
POST /api/warehouse/agents/company-memberships/
Content-Type: application/json

{
  "user": "uuid-пользователя",
  "assigned_warehouse": "uuid-склада"
}
```

---

### Шаг 2 — выбрать склад и товары

```http
GET /api/warehouse/
GET /api/warehouse/{warehouse_uuid}/products/?search=...
```

Товары в позициях заявки **должны принадлежать выбранному складу**.

Если у агента задан `assigned_warehouse`, выбирайте только этот склад — иначе API вернёт 400.

---

### Шаг 3 — создать заявку с указанием агента

```http
POST /api/warehouse/agent-carts/
Content-Type: application/json

{
  "warehouse": "550e8400-e29b-41d4-a716-446655440000",
  "agent": "660e8400-e29b-41d4-a716-446655440001",
  "note": "Выдача от владельца"
}
```

**Правила для владельца/админа:**

- поле `agent` **обязательно**;
- `agent` должен быть активным агентом компании (`status=active` в `company-requests`);
- если агенту назначен склад — `warehouse` должен совпадать с `assigned_warehouse`;
- `company` и `branch` **не передаются** — сервер берёт их из `warehouse`;
- заявка создаётся в статусе `draft`.

**Пример ответа (201):**

```json
{
  "id": "770e8400-e29b-41d4-a716-446655440002",
  "agent": "660e8400-e29b-41d4-a716-446655440001",
  "agent_display": "Иван Иванов",
  "warehouse": "550e8400-e29b-41d4-a716-446655440000",
  "status": "draft",
  "note": "Выдача от владельца",
  "submitted_at": null,
  "approved_at": null,
  "approved_by": null,
  "sale_document": null,
  "sale_document_number": null,
  "created_date": "2026-05-27T10:00:00Z",
  "updated_date": "2026-05-27T10:00:00Z",
  "items": []
}
```

**Ошибки (400):**

```json
{ "agent": ["Укажите агента, которому отправляете товар."] }
```

```json
{ "agent": ["Пользователь не является активным агентом этой компании."] }
```

```json
{ "agent": ["Агенту назначен другой склад."] }
```

---

### Шаг 4 — добавить позиции

```http
POST /api/warehouse/agent-cart-items/
Content-Type: application/json

{
  "cart": "770e8400-e29b-41d4-a716-446655440002",
  "product": "880e8400-e29b-41d4-a716-446655440003",
  "quantity_requested": "10.000"
}
```

Дополнительные операции (пока заявка в `draft`):

```http
GET  /api/warehouse/agent-cart-items/?cart={cart_id}
PATCH /api/warehouse/agent-cart-items/{item_id}/
DELETE /api/warehouse/agent-cart-items/{item_id}/
```

**Формат позиции в ответе:**

```json
{
  "id": "uuid",
  "cart": "uuid",
  "product": "uuid",
  "product_name": "Товар 1",
  "product_article": "P001",
  "product_unit": "шт.",
  "quantity_requested": "10.000",
  "qty": "10.000",
  "price": "150.00",
  "discount_percent": "0.00",
  "discount_amount": "0.00",
  "line_total": "1500.00"
}
```

---

### Шаг 5 — выдать товар агенту (новый endpoint)

```http
POST /api/warehouse/agent-carts/{cart_id}/dispatch/
Content-Type: application/json

{}
```

**Доступ:** только владелец/админ.

**Требования:**

- статус заявки = `draft`;
- есть хотя бы одна позиция с `quantity_requested > 0`;
- на складе достаточно остатка по каждой позиции.

**Что делает сервер:**

1. Списывает товар с `StockBalance` / остатка склада.
2. Зачисляет на личный остаток агента (`AgentStockBalance`).
3. Переводит заявку в `approved`.
4. Проставляет `submitted_at`, `approved_at`, `approved_by`.

**Пример ответа (200):**

```json
{
  "id": "770e8400-e29b-41d4-a716-446655440002",
  "agent": "660e8400-e29b-41d4-a716-446655440001",
  "agent_display": "Иван Иванов",
  "warehouse": "550e8400-e29b-41d4-a716-446655440000",
  "status": "approved",
  "note": "Выдача от владельца",
  "submitted_at": "2026-05-27T10:05:00Z",
  "approved_at": "2026-05-27T10:05:00Z",
  "approved_by": "uuid-владельца",
  "items": [ "... позиции ..." ]
}
```

**Ошибки:**

| Код | Причина |
|-----|---------|
| 403 | Не владелец/админ |
| 400 | Пустая заявка |
| 400 | Недостаточно товара на складе |
| 400 | Статус не `draft` |

Пример ошибки по остаткам:

```json
{
  "items": ["Недостаточно на складе для Товар 1: нужно 10.000, доступно 3.000."]
}
```

---

## 3. Просмотр истории (владелец)

```http
GET /api/warehouse/agent-carts/
GET /api/warehouse/agent-carts/?agent={agent_uuid}
GET /api/warehouse/agent-carts/?status=approved
GET /api/warehouse/agent-carts/?warehouse={warehouse_uuid}
```

Владелец видит **все** заявки компании (не только свои).  
Агент — только свои.

Остатки агента после выдачи:

```http
GET /api/warehouse/owner/agents/products/?agent={agent_uuid}
```

(или общий список остатков всех агентов без фильтра).

---

## 4. Старый сценарий (агент → владелец) — без изменений

Для мобильного приложения агента:

```http
POST /api/warehouse/agent-carts/          # без agent
POST /api/warehouse/agent-cart-items/
POST /api/warehouse/agent-carts/{id}/submit/
```

Для владельца при входящей заявке:

```http
POST /api/warehouse/agent-carts/{id}/approve/   # только status=submitted
POST /api/warehouse/agent-carts/{id}/reject/
```

> **Важно:** `approve` работает только из статуса `submitted`.  
> `dispatch` работает только из статуса `draft`.  
> Не вызывайте оба endpoint для одной заявки.

---

## 5. Рекомендации для UI

### Экран «Выдать товар агенту» (владелец)

1. Селект **агент** — из `GET .../company-requests/?status=active`.
2. Селект **склад** — из `GET /api/warehouse/`, с учётом `assigned_warehouse` агента.
3. Таблица **товары** — из `GET /api/warehouse/{warehouse}/products/`.
4. Кнопка **«Выдать»**:
   - `POST agent-carts` → добавить все позиции → `POST dispatch`;
   - или создать черновик заранее и редактировать позиции до dispatch.

### Состояния кнопок

| Статус заявки | Редактировать позиции | Действие владельца |
|---------------|----------------------|--------------------|
| `draft` (создал владелец) | Да | **`dispatch`** |
| `submitted` (запросил агент) | Нет | `approve` / `reject` |
| `approved` | Нет | только просмотр (+ опционально `create-sale`) |
| `rejected` | Нет | только просмотр |

### После `dispatch`

- Показать успех и обновить остатки агента.
- При необходимости оформить продажу: `POST /api/warehouse/agent-carts/{id}/create-sale/` (заявка уже `approved`).

---

## 6. Краткая шпаргалка endpoint'ов

| Метод | URL | Кто | Назначение |
|-------|-----|-----|------------|
| GET | `/agents/company-requests/?status=active` | Владелец | Список агентов для выбора |
| POST | `/agents/company-memberships/` | Владелец | Назначить агента напрямую |
| POST | `/agent-carts/` + `"agent"` | Владелец | Создать заявку на выдачу |
| POST | `/agent-carts/` без `"agent"` | Агент | Создать свою заявку |
| POST | `/agent-cart-items/` | Оба | Добавить позицию |
| POST | `/agent-carts/{id}/dispatch/` | **Владелец** | **Выдать товар (новое)** |
| POST | `/agent-carts/{id}/submit/` | Агент | Отправить владельцу |
| POST | `/agent-carts/{id}/approve/` | Владелец | Одобрить заявку агента |
| POST | `/agent-carts/{id}/reject/` | Владелец | Отклонить заявку агента |
| GET | `/owner/agents/products/` | Владелец | Остатки агентов |

---

## 7. TypeScript-типы (ориентир)

```typescript
type AgentCartStatus = "draft" | "submitted" | "approved" | "rejected";

interface OwnerCreateAgentCartBody {
  warehouse: string;
  agent: string;       // обязательно для owner/admin
  note?: string;
}

interface AgentCreateAgentCartBody {
  warehouse: string;
  note?: string;
  // agent не передаётся
}

interface AgentCartItemBody {
  cart: string;
  product: string;
  quantity_requested: string; // decimal string, напр. "10.000"
}

interface AgentRequestCart {
  id: string;
  agent: string;
  agent_display: string;
  warehouse: string;
  status: AgentCartStatus;
  note: string | null;
  submitted_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
  sale_document: string | null;
  sale_document_number: string | null;
  created_date: string;
  updated_date: string;
  items: AgentRequestCartItem[];
}
```
