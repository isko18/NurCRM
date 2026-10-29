# Модуль «Производство»: разделение возвратов и брака

Документ для фронтенда. Описывает изменения в возвратах из продажи и агентской аналитике.

Затрагивает:
- `POST /api/main/pos/sales/{sale_id}/return/`
- `POST /api/main/agents/me/sales/{sale_id}/return/`
- `GET  /api/main/agents/me/analytics/`
- `GET  /api/main/clients/{client_id}/agent-analytics/` — **новый**
- `GET  /api/main/agents/me/returns/` и `GET /api/main/returns/`
- `POST /api/main/returns/`

---

## Главное

Появилось разделение **обычного возврата** и **брака** через флаг `is_defect`:

- `is_defect = false` (обычный возврат) → товар возвращается обратно на склад агента.
- `is_defect = true` (брак) → товар списывается, на склад **не** возвращается, фиксируется как брак.

Каждый возврат из продажи теперь сохраняется отдельной записью (модель `ReturnFromAgent`) с суммой по цене продажи, что и питает всю аналитику ниже.

---

## 1. Возврат из продажи

**Эндпоинты:**
`POST /api/main/pos/sales/{sale_id}/return/`
`POST /api/main/agents/me/sales/{sale_id}/return/`

### Payload

Добавлено поле `is_defect` **на уровне всего запроса** (по умолчанию `false`):

```json
{
  "items": [
    { "sale_item_id": "uuid", "quantity": 2 }
  ],
  "is_defect": true
}
```

- `items` — как и раньше: частичный возврат по позициям. Если `items` пустой/не передан — возврат всего чека.
- `is_defect` — применяется ко всем позициям запроса.

### Поведение

| `is_defect` | Что с товаром | Аналитика |
|-------------|---------------|-----------|
| `false`     | Возвращается агенту (на руки) | Учитывается как обычный возврат |
| `true`      | Списывается (брак), на склад не возвращается | Учитывается как брак |

В обоих случаях чек корректируется только на указанные позиции и количество (не весь чек, если передан `items`). Суммы чека пересчитываются.

Ответ — как и раньше: тело продажи (`SaleDetailSerializer`), HTTP 200.

> Примечание: учёт брака/возвратов в аналитике ведётся **для агентских продаж**. Для обычных кассовых чеков `is_defect=true` означает только то, что товар не возвращается на склад.

---

## 2. Аналитика агента

**Эндпоинт:** `GET /api/main/agents/me/analytics/?period=month`

В блок `summary` добавлены поля (старое поле `defective_items` сохранено):

```json
{
  "summary": {
    "defective_items": 5,
    "defective_items_amount": 2500.00,

    "returns_count": 3,
    "returns_amount": 1200.00,

    "accounts_receivable": 8793.0,
    "accounts_receivable_pos_sales": 8193.0,
    "accounts_receivable_client_deals": 600.0,
    "clients_debt_total": 4303.0
  }
}
```

| Поле | Описание |
|------|----------|
| `defective_items` | Кол-во единиц брака за период (`is_defect=true`) |
| `defective_items_amount` | **Новое** — сумма списанного брака (по цене продажи) |
| `returns_count` | **Новое** — кол-во обычных возвратов (`is_defect=false`) |
| `returns_amount` | **Новое** — сумма обычных возвратов |

Разделение идёт по `is_defect` среди принятых (`accepted`) возвратов агента за период.

---

## 3. Аналитика контрагента глазами агента (новый эндпоинт)

**Эндпоинт:** `GET /api/main/clients/{client_id}/agent-analytics/`

Квери-параметры: `?period=day|week|month|custom`, `?date_from=YYYY-MM-DD`, `?date_to=YYYY-MM-DD`.
Период влияет на `sales` / `defects` / `returns`. Блок `debt` — текущий (на момент запроса), от периода не зависит.

Считается по текущему пользователю-агенту (`request.user`).

### Ответ

```json
{
  "client_id": "uuid",
  "client_name": "Тестовый клиент",
  "period": { "type": "month", "date_from": "2026-05-18", "date_to": "2026-06-16" },

  "sales":   { "count": 12, "amount": 45000.00 },
  "defects": { "count": 2,  "amount": 800.00 },
  "returns": { "count": 3,  "amount": 1500.00 },

  "debt": {
    "pos_sales_debt": 3703.00,
    "client_deals_debt": 600.00,
    "total": 4303.00
  }
}
```

| Поле | Описание |
|------|----------|
| `sales.count` / `sales.amount` | Оплаченные продажи агента этому клиенту за период |
| `defects.count` / `defects.amount` | Брак по этому клиенту: `count` — единицы (qty), `amount` — сумма |
| `returns.count` / `returns.amount` | Обычные возвраты по клиенту: `count` — число возвратов, `amount` — сумма |
| `debt.pos_sales_debt` | Долг по POS-продажам в долг |
| `debt.client_deals_debt` | Остаток по сделкам-рассрочкам клиента |
| `debt.total` | Сумма двух долгов |

`404`, если клиент не найден в компании.

---

## 4. Список возвратов агента

**Эндпоинты:**
`GET /api/main/agents/me/returns/`
`GET /api/main/returns/`

### Поля каждого возврата

Добавлены `is_defect`, `amount`, `client`, `client_name`, `product_name`:

```json
{
  "id": "uuid",
  "qty": 3,
  "status": "accepted",
  "is_defect": true,
  "amount": "2500.00",
  "product": "Пряник Томпок",
  "product_name": "Пряник Томпок",
  "client": "uuid | null",
  "client_name": "Тестовый клиент | null"
}
```

(остальные поля — как раньше: `subreal_id`, `agent`, `status_display`, `returned_by_name`, `accepted_by_name`, `returned_at`, `accepted_at`).

### returns_summary

В ответ списка добавлены графы брака и обычных возвратов (старые `pending_*` сохранены):

```json
{
  "results": [ /* ... */ ],
  "returns_summary": {
    "pending_count": 1,
    "pending_qty": 3.0,

    "defect_count": 2,
    "defect_qty": 5.0,
    "defect_amount": 2500.00,

    "regular_return_count": 1,
    "regular_return_qty": 1.0,
    "regular_return_amount": 1200.00
  }
}
```

Сводка считается по тем же фильтрам, что и список.

### Фильтр

Добавлен фильтр `?is_defect=true|false` — например, показать только брак: `GET /api/main/agents/me/returns/?is_defect=true`.

### Ручное создание возврата

`POST /api/main/returns/` теперь принимает необязательное `is_defect` (по умолчанию `false`):

```json
{ "subreal": "uuid", "qty": 3, "is_defect": true }
```

---

## Важно

- `amount` (сумма возврата/брака) считается по **цене продажи** строки чека: `unit_price × qty − скидка по строке`.
- `amount` заполняется только у возвратов, созданных из возврата продажи. У ручных возвратов непроданного остатка `amount = 0`.
- `client` / `client_name` заполняются только у возвратов из продажи (берутся из чека); у ручных возвратов — `null`.
- Аналитика агента кэшируется — после возврата цифры обновятся не мгновенно (по таймауту кэша).
- Денежные суммы в аналитике (`*_amount`) приходят числами (float); в списке возвратов `amount` — строка с двумя знаками (как DecimalField DRF).
