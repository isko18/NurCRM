# Возврат товара агентом (Warehouse Frontend)

Документация для фронта: агент склада возвращает товар на склад компании, владелец принимает или отклоняет.

> **Важно:** это модуль **`/api/warehouse/`**, не `/api/main/returns/`.  
> Старый `main` возврат работает через `ManufactureSubreal` и **не подходит** для агентов склада с остатками `AgentStockBalance`.

Базовый префикс:

```http
/api/warehouse/
```

Аутентификация:

```http
Authorization: Bearer <access_token>
```

---

## 1. Flow

```text
draft -> submitted -> approved / rejected
```

| Статус | Кто действует | Описание |
|--------|---------------|----------|
| `draft` | Агент | Черновик, можно менять позиции |
| `submitted` | Агент отправил | Ждёт решения владельца |
| `approved` | Владелец принял | Товар вернулся на склад |
| `rejected` | Владелец отклонил | Остатки не менялись |

При `approve`:
- уменьшается `AgentStockBalance` (личный остаток агента);
- увеличивается `StockBalance` и `WarehouseProduct.quantity` на складе.

---

## 2. Эндпоинты

| Метод | URL | Кто | Назначение |
|-------|-----|-----|------------|
| GET | `/agent-return-carts/` | Оба | Список возвратов |
| POST | `/agent-return-carts/` | Агент | Создать возврат |
| GET/PATCH/DELETE | `/agent-return-carts/{id}/` | Агент (свои) / owner (все) | Детали / правка черновика |
| POST | `/agent-return-carts/{id}/submit/` | Агент | Отправить владельцу |
| POST | `/agent-return-carts/{id}/approve/` | Владелец/админ | Принять возврат |
| POST | `/agent-return-carts/{id}/reject/` | Владелец/админ | Отклонить возврат |
| GET | `/agent-return-cart-items/?cart={id}` | Оба | Позиции возврата |
| POST | `/agent-return-cart-items/` | Агент | Добавить позицию |
| PATCH/DELETE | `/agent-return-cart-items/{id}/` | Агент | Изменить/удалить позицию |

Фильтры списка (`GET /agent-return-carts/`):
- `status` — `draft|submitted|approved|rejected`
- `warehouse` — UUID склада
- `agent` — UUID агента (для owner/admin)
- `submitted_at`, `approved_at`

---

## 3. Сценарий агента

### Шаг 1 — посмотреть остатки

```http
GET /api/warehouse/agents/me/products/
```

Возвращает личный остаток агента (`AgentStockBalance`), если не включён общий доступ.

### Шаг 2 — создать возврат

```http
POST /api/warehouse/agent-return-carts/
Content-Type: application/json

{
  "warehouse": "uuid-склада",
  "note": "Возврат непроданного товара"
}
```

- `agent` **не передаётся** — сервер подставляет текущего пользователя;
- владелец **не может** создавать возврат за агента.

### Шаг 3 — добавить позиции

```http
POST /api/warehouse/agent-return-cart-items/
Content-Type: application/json

{
  "cart": "uuid-возврата",
  "product": "uuid-товара",
  "quantity_returned": "5.000"
}
```

Правила:
- товар должен принадлежать складу из возврата;
- количество не больше остатка у агента (с учётом других `submitted` возвратов);
- позиции редактируются только в `draft`.

### Шаг 4 — отправить владельцу

```http
POST /api/warehouse/agent-return-carts/{id}/submit/
Content-Type: application/json

{}
```

---

## 4. Сценарий владельца

### Входящие возвраты

```http
GET /api/warehouse/agent-return-carts/?status=submitted
GET /api/warehouse/agent-return-carts/?agent={agent_uuid}
```

### Принять возврат

```http
POST /api/warehouse/agent-return-carts/{id}/approve/
Content-Type: application/json

{}
```

Требования:
- статус `submitted`;
- у агента достаточно остатка по каждой позиции.

### Отклонить возврат

```http
POST /api/warehouse/agent-return-carts/{id}/reject/
Content-Type: application/json

{}
```

Остатки не меняются.

---

## 5. Формат ответа

```json
{
  "id": "uuid",
  "agent": "uuid",
  "agent_display": "Иван Иванов",
  "warehouse": "uuid",
  "status": "submitted",
  "note": "Возврат непроданного товара",
  "submitted_at": "2026-05-27T12:00:00Z",
  "approved_at": null,
  "approved_by": null,
  "created_date": "2026-05-27T11:55:00Z",
  "updated_date": "2026-05-27T12:00:00Z",
  "items": [
    {
      "id": "uuid",
      "cart": "uuid",
      "product": "uuid",
      "product_name": "Товар 1",
      "product_article": "P001",
      "product_unit": "шт.",
      "quantity_returned": "5.000",
      "qty": "5.000",
      "price": "150.000",
      "created_date": "2026-05-27T11:58:00Z",
      "updated_date": "2026-05-27T11:58:00Z"
    }
  ]
}
```

---

## 6. Ошибки

| Код | Пример | Причина |
|-----|--------|---------|
| 400 | `{"quantity_returned": ["Недостаточно у агента..."]}` | Больше остатка агента |
| 400 | `{"items": ["Недостаточно у агента для ..."]}` | При submit/approve |
| 400 | `{"detail": "Владелец/админ не создаёт возврат за агента..."}` | Owner пытается POST cart |
| 403 | `{"detail": "Только владелец/админ."}` | Агент вызывает approve/reject |
| 403 | `{"detail": "Нет доступа."}` | Чужой возврат |

---

## 7. UI рекомендации

### Экран агента «Вернуть товар»

1. Выбор склада (`GET /api/warehouse/`).
2. Список товаров с остатком (`GET /api/warehouse/agents/me/products/`).
3. Создание возврата + позиции + `submit`.

### Экран владельца «Входящие возвраты»

1. Список `?status=submitted`.
2. Кнопки **Принять** (`approve`) / **Отклонить** (`reject`).
3. После approve обновить остатки склада и агента.

### Состояния кнопок

| Статус | Агент | Владелец |
|--------|-------|----------|
| `draft` | Редактировать, Submit | Только просмотр |
| `submitted` | Только просмотр | Approve / Reject |
| `approved` / `rejected` | Только просмотр | Только просмотр |

---

## 8. Отличие от `/api/main/returns/`

| | Warehouse (`agent-return-carts`) | Main (`/api/main/returns/`) |
|---|---|---|
| Остаток агента | `AgentStockBalance` | `ManufactureSubreal` |
| Когда использовать | Агент получил товар через `agent-carts` / `dispatch` | Старый manufacture/market flow |
| Принятие | `POST .../approve/` | `POST /api/main/returns/{id}/approve/` |

Если агент работает через **warehouse**, используйте только **`agent-return-carts`**.
