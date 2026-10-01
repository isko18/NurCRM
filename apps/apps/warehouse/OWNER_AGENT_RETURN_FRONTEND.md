# Возврат товара от агента — действия владельца (Frontend)

Владелец может **посмотреть остатки агента** и **сам принять возврат** на склад без заявки от агента.

Базовый префикс: **`/api/warehouse/`**

---

## 1. Посмотреть остатки агента

### Все агенты

```http
GET /api/warehouse/owner/agents/products/
GET /api/warehouse/owner/agents/products/?agent={agent_uuid}
GET /api/warehouse/owner/agents/products/?warehouse={warehouse_uuid}
GET /api/warehouse/owner/agents/products/?search=название
```

### Конкретный агент

```http
GET /api/warehouse/owner/agents/{agent_id}/products/
```

Query-параметры: `warehouse`, `search`, `product_group`, `order_by=date|-date`, `page`, `page_size`.

Пример ответа (пагинация):

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "agent": "uuid-агента",
      "agent_display": "Иван Иванов",
      "warehouse": "uuid-склада",
      "product": "uuid-товара",
      "product_name": "Товар 1",
      "product_article": "P001",
      "product_unit": "шт.",
      "qty": "25.000",
      "qty_available": "25.000",
      "product_price": "150.000",
      "last_movement_at": "2026-05-27T10:00:00Z"
    }
  ]
}
```

- `qty` — остаток на руках у агента (`AgentStockBalance`)
- `qty_available` — доступно для возврата (минус уже отправленные заявки агента в статусе `submitted`)

Список агентов для выбора:

```http
GET /api/warehouse/agents/company-requests/?status=active
```

---

## 2. Принять возврат от агента (владелец сам)

Flow: `draft` → **`receive`** → `approved`

### Вариант A — одним запросом (рекомендуется)

```http
POST /api/warehouse/agent-return-carts/
Content-Type: application/json

{
  "warehouse": "uuid-склада",
  "agent": "uuid-агента",
  "note": "Возврат от владельца",
  "items_input": [
    {
      "product": "uuid-товара",
      "quantity_returned": "10.000"
    }
  ]
}
```

Затем:

```http
POST /api/warehouse/agent-return-carts/{cart_id}/receive/
{}
```

### Вариант B — по шагам

1. `POST /api/warehouse/agent-return-carts/` + `"agent"` (обязательно)
2. `POST /api/warehouse/agent-return-cart-items/`
3. `POST /api/warehouse/agent-return-carts/{id}/receive/`

---

## 3. Эндпоинты владельца

| Метод | URL | Назначение |
|-------|-----|------------|
| GET | `/owner/agents/products/` | Остатки всех агентов |
| GET | `/owner/agents/{agent_id}/products/` | Остатки одного агента |
| POST | `/agent-return-carts/` + `agent` | Создать возврат (владелец) |
| POST | `/agent-return-cart-items/` | Добавить позицию |
| POST | `/agent-return-carts/{id}/receive/` | **Принять возврат на склад** |
| GET | `/agent-return-carts/?agent={id}` | История возвратов агента |

---

## 4. Два сценария возврата

| | Агент инициирует | Владелец инициирует |
|---|---|---|
| Создание | `POST agent-return-carts/` без `agent` | `POST agent-return-carts/` **с `agent`** |
| Отправка | `submit` → ждёт владельца | не нужен |
| Приём на склад | `approve` (статус `submitted`) | **`receive`** (статус `draft`) |
| Итог | `approved` | `approved` |

---

## 5. Ошибки

```json
{
  "quantity_returned": [
    "Недостаточно у агента для Товар 1: нужно 30.000, доступно 25.000."
  ]
}
```

```json
{
  "agent": ["Укажите агента, у которого принимаете возврат."]
}
```

При ошибке добавления первой позиции пустой черновик удаляется автоматически.
