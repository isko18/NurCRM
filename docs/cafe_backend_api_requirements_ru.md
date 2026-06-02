# Кафе — требования к API (документация для бэкенда)

Документ по задачам с фронта CRM (`/crm/cafe/...`).  
**Базовый префикс:** `/api/cafe/` (прод: `https://app.nurcrm.kg/api/cafe/...`).

**Статус реализации в репозитории:** см. чеклисты `[x]` / `[ ]` в конце каждого раздела.

---

## Общее

- Фильтрация: `CompanyBranchQuerysetMixin`, `?branch=<uuid>`.
- Даты: `YYYY-MM-DD`, интервал включительно (timezone компании, `Asia/Bishkek`).
- Деньги: decimal/string с 2 знаками.

| Раздел | Маршрут CRM |
|--------|-------------|
| Аналитика | `/crm/cafe/analytics` |
| Склад | `/crm/cafe/stock` |
| Инвентаризация | `/crm/cafe/inventory` |
| Заказы | `/crm/cafe/orders` |

---

## 1. Динамика продаж

### Эндпоинт

```http
GET /api/cafe/analytics/sales/dynamics/?date_from=...&date_to=...&period=day|week|month&branch=...
```

**Реализация:** `apps/cafe/analytics.py` → `SalesDynamicsView`, URL в `apps/cafe/urls.py`.

### Ответ

`date_from`, `date_to`, `period`, `basis: "paid_at"`, `totals` (orders_count, items_qty, revenue), `series[]` (label, date_from, date_to, …).

### Правила

- `is_paid=true`, дата `paid_at`, строки `is_rejected=false`.
- Авто `period`: ≤62 дней → `day`, иначе `week`.
- `period=day` и диапазон >366 дней → 400.
- Кэш ~30–60 с.

### Чеклист бэкенда

- [x] `GET analytics/sales/dynamics/`
- [x] `date_from`, `date_to`, `period`
- [x] `totals` + `series`
- [x] Та же база, что `sales/summary/`

---

## 2. Склад: авто-расход «Закупки»

### Категория

`CafeExpenseCategory`: `slug=zakupki`, `is_system=true`.  
`ensure_zakupki_category()` в `apps/cafe/services/warehouse_expense.py`.

### API категорий

| Метод | Путь |
|--------|------|
| GET, POST | `/api/cafe/expense-categories/` |
| GET, PATCH, DELETE | `/api/cafe/expense-categories/{id}/` |

PATCH/DELETE системной → **403**.

### Авто `CafeExpense`

| Триггер | `source` |
|---------|----------|
| `POST /warehouse/` с `remainder > 0` | `warehouse_create` |
| `POST /warehouse/{id}/receive/` | `warehouse_receipt` |
| `PUT /warehouse/{id}/` (рост remainder) | `warehouse_receipt` |

Идемпотентность: `(company, source, source_id)`.

### Оприходование

```http
POST /api/cafe/warehouse/{id}/receive/
```

Ответ: товар + `expense_id`, `expense_amount`, `movement_id`.

### Чеклист бэкенда

- [x] Системная категория «Закупки»
- [x] Запрет PATCH/DELETE категории
- [x] Авто-расход на создание и приход
- [x] `source`, `source_id`
- [x] `POST .../receive/` + `expense_id` в ответе
- [ ] Cashflow на бэке (вариант A: только `CafeExpense`; фронт убрал дубли)

---

## 3. Посуда и расходники (household)

### Префиксы

| Ресурс | Путь |
|--------|------|
| Номенклатура | `/api/cafe/household-items/` |
| Движения | `.../movements/`, `.../receive/`, `.../write-off/` |
| Инвентаризация | `/api/cafe/household-inventory/sessions/` + `.../confirm/` |

**Код:** `apps/cafe/household_views.py`, модели в `apps/cafe/models.py`.

### POST сессии инвентаризации

```json
{
  "comment": "...",
  "items": [
    { "item": "uuid", "qty_counted": "118" }
  ]
}
```

Альтернатива: ключ `lines` с тем же форматом.

### Чеклист бэкенда

- [x] Модели Household*
- [x] CRUD номенклатуры
- [x] receive / write-off
- [x] Сессии + confirm + summary
- [x] `CafeExpense` на приход с `unit_price`
- [x] PATCH черновика: comment + пересоздание строк (`items` / `lines`)
- [x] Исправление сериализатора: `items` в `Meta.fields` (POST без AssertionError)

---

## 4. Оплата заказа

```http
POST /api/cafe/orders/{id}/pay/
```

- [x] `split` + `payments[]`
- [x] `debt` + предоплата
- [x] `pay_now` + `client_id`
- [x] `idempotency_key`

---

## Сводный приоритет

| № | Задача | Бэкенд |
|---|--------|--------|
| 1 | `sales/dynamics/` | Готов |
| 2 | Авто «Закупки» | Готов (`CafeExpense`; cashflow — на фронте) |
| 3 | Household | Готов |

После деплоя на прод: перезапуск воркеров, миграции `cafe` (если не применены).
