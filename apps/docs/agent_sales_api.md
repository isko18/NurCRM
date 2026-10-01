# Agent Sales API — История продаж агента

## Обзор

API истории продаж для агентов. Каждый агент видит только свои продажи — по `AgentSaleAllocation` или по полю `user` (кассир).

---

## Эндпоинты

### 1. Список продаж агента

```
GET /api/main/agents/me/sales/
```

Возвращает только продажи, где:
- есть `AgentSaleAllocation` с `agent=request.user`, или
- `Sale.user = request.user` (агент был кассиром)

### 2. Детали продажи

```
GET /api/main/agents/me/sales/<sale_id>/
```

Доступ только к своим продажам. Чужие — 404.

### 3. Возврат продажи

```
POST /api/main/agents/me/sales/<sale_id>/return/
```

Отменяет оплаченную или долговую продажу. Агент может вернуть только свои продажи.

**Параметры:** `sale_id` в URL. Тело запроса не требуется.

**Логика:**
- **Агентская продажа** (есть `AgentSaleAllocation`) — удаляются записи распределения, товар снова у агента
- **Обычная продажа** (агент был кассиром) — товар возвращается на склад (`Product.quantity += item.quantity`)

После возврата статус продажи → `canceled`.

**Ограничения:** только продажи в статусе `paid` или `debt`.

**Ответ 200:** объект продажи (`SaleDetailSerializer`) со статусом `canceled`.

**Ошибки:**
| Код | Описание |
|-----|----------|
| 400 | «Продажа уже отменена.» |
| 400 | «Возврат возможен только для оплаченных или долговых продаж.» |
| 404 | Продажа не найдена или не принадлежит агенту |

---

## Параметры списка (GET /agents/me/sales/)

| Параметр | Тип | Описание |
|----------|-----|----------|
| `start` | date/datetime | Начало периода (YYYY-MM-DD или ISO) |
| `end` | date/datetime | Конец периода |
| `paid` | bool | Только оплаченные (`1`, `true`) |
| `status` | string | Фильтр по статусу: `new`, `paid`, `debt`, `canceled` |
| `user` | uuid | Фильтр по кассиру |
| `search` | string | Поиск по ID |
| `ordering` | string | Сортировка: `created_at`, `total`, `status` |

---

## Примеры запросов

```http
# Все свои продажи
GET /api/main/agents/me/sales/
Authorization: Bearer <token>

# За период
GET /api/main/agents/me/sales/?start=2024-03-01&end=2024-03-31

# Только оплаченные
GET /api/main/agents/me/sales/?paid=true

# Детали
GET /api/main/agents/me/sales/550e8400-e29b-41d4-a716-446655440000/

# Возврат
POST /api/main/agents/me/sales/550e8400-e29b-41d4-a716-446655440000/return/
```

---

## Формат ответа

### Список (GET /agents/me/sales/)

Совпадает с `pos/sales/` — массив объектов `SaleListSerializer`:

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "paid",
    "total": "1500.00",
    "created_at": "2024-03-15T10:30:00",
    "user": "uuid",
    "items": [...]
  }
]
```

### Детали (GET /agents/me/sales/<id>/)

Совпадает с `pos/sales/<id>/` — объект `SaleDetailSerializer`.

### Возврат (POST /agents/me/sales/<id>/return/)

Объект продажи со статусом `canceled`:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "canceled",
  "subtotal": "1500.00",
  "discount_total": "0.00",
  "tax_total": "0.00",
  "total": "1500.00",
  "items": [...],
  "user": "...",
  "created_at": "2024-03-15T10:30:00",
  ...
}
```

---

## Коды ошибок

| Код | Описание |
|-----|----------|
| 400 | Продажа уже отменена / возврат только для paid/debt (при POST return) |
| 401 | Не авторизован |
| 404 | Продажа не найдена или не принадлежит агенту |

---

## Права доступа

- Требуется аутентификация (`IsAuthenticated`)
- Фильтр по компании и филиалу (`CompanyBranchRestrictedMixin`)
- Агент видит только свои продажи

---

## Сравнение с pos/sales/

| Эндпоинт | Кто видит |
|----------|-----------|
| `GET /api/main/pos/sales/` | Все продажи компании (владелец/кассир) |
| `GET /api/main/agents/me/sales/` | Только свои продажи (агент) |

---

## Техническая информация

### Файлы

- **Views:** `apps/main/pos_views.py` → `AgentMySalesListAPIView`, `AgentMySaleRetrieveAPIView`, `AgentSaleReturnAPIView`

### URL names

- `agent-my-sales-list`
- `agent-my-sale-detail`
- `agent-sale-return`
