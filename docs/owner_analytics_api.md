# Owner Analytics API — Документация

## Обзор

API общей аналитики владельца компании. Доступен только пользователям с правами владельца/админа (`_is_owner_like`).

## Базовый URL

```
GET /api/main/owners/analytics/
```

## Детализация карточек (для модалки)

Эндпоинт `GET /api/main/analytics/cards/details/?card=<key>` — список строк для модалки по клику на карточку. Полное описание параметров, всех значений `card`, формата `items`/`totals` и прав доступа: **[analytics_cards_details_api.md](./analytics_cards_details_api.md)**.

## Параметры запроса

| Параметр | Тип | Описание | Обязательный | По умолчанию |
|----------|-----|----------|---------------|--------------|
| `period` | string | Период: `day`, `week`, `month`, `custom` | Нет | `month` |
| `date` | date | Дата для `period=day` (YYYY-MM-DD) | Нет | сегодня |
| `date_from` | date | Начало периода (YYYY-MM-DD) | Нет | зависит от period |
| `date_to` | date | Конец периода (YYYY-MM-DD) | Нет | зависит от period |
| `branch` | uuid | ID филиала (через миксин) | Нет | — |

### Логика периодов

- **day** — один день (`date` или `date_from` или `date_to`)
- **week** — 7 дней до `date_to`
- **month** / **custom** — до 30 дней до `date_to`

---

## Примеры запросов

```http
# Аналитика за месяц (по умолчанию)
GET /api/main/owners/analytics/?period=month

# Аналитика за месяц, группировка по дням
GET /api/main/owners/analytics/?period=month&group_by=day

# Произвольный период
GET /api/main/owners/analytics/?period=custom&date_from=2024-01-01&date_to=2024-01-31

# Один день
GET /api/main/owners/analytics/?period=day&date=2024-03-15
```

---

## Структура ответа

```json
{
  "period": {
    "type": "month",
    "date_from": "2024-02-15",
    "date_to": "2024-03-15",
    "group_by": "day"
  },
  "summary": {
    "users_count": 25,
    "transfers_count": 120,
    "acceptances_count": 85,
    "items_transferred": 1500,
    "defective_items": 17,
    "sales_count": 340,
    "sales_amount": "1250000.00",
    "gross_profit": "380000.00",
    "stock_purchase_value": "890000.00",
    "stock_retail_value": "1240000.00",
    "raw_material_value": "210000.00",
    "total_debt": "45000.00"
  },
  "charts": {
    "sales_by_date": [...],
    "transfers_by_date": [...],
    "top_products_by_sales": [...],
    "top_users_by_sales": [...],
    "top_users_by_transfers": [...],
    "sales_distribution_by_product": [...],
    "expense_breakdown": [...]
  }
}
```

---

## Summary — сводные показатели

Полное описание всех ключей `summary`, формул и нюансов (склад без услуг, `total_debt` vs дебиторская): **[owner_analytics_summary_ru.md](./owner_analytics_summary_ru.md)**.

| Поле | Тип | Описание |
|------|-----|----------|
| `users_count` | integer | Количество пользователей компании |
| `transfers_count` | integer | Количество передач за период |
| `acceptances_count` | integer | Количество приёмок за период |
| `items_transferred` | number | Объём переданных единиц |
| **`defective_items`** | integer | **Брак товаров** (сумма `ReturnFromAgent.qty` со статусом `ACCEPTED` за период) |
| `sales_count` | integer | Количество оплаченных продаж |
| `sales_amount` | string | Сумма продаж (Decimal) |
| **`gross_profit`** | string | **Валовая прибыль** (выручка − себестоимость) |
| **`stock_purchase_value`** | string | **Закупочная цена склада** (остатки × закупочная цена) |
| **`stock_retail_value`** | string | **Розничная цена склада** (остатки × розничная цена) |
| **`raw_material_value`** | string | **Цена сырья** (остатки сырья × цена сырья) |
| **`total_debt`** | string | **Общий долг** (сумма непогашенных долгов) |

### Расчёт показателей

Точные формулы (выручка с `line_discount`, себестоимость, разница `total_debt` / `accounts_receivable`): **[owner_analytics_summary_ru.md](./owner_analytics_summary_ru.md)**.

- **gross_profit** — выручка по строкам чека минус COGS (см. документ выше).
- **stock_purchase_value** / **stock_retail_value** — по товарам `Product` **без** `kind=service`: `Σ(quantity × …)`.
- **raw_material_value** — по сырью (ItemMake): `Σ(quantity × price)`.
- **defective_items** — по принятым возвратам от агентов за период (`returned_at`).
- **total_debt** — остаток только по `ClientDeal(kind=debt)`; POS-долг входит в `accounts_receivable`, но не в `total_debt`.

---

## Детализация карточки `defective_items` (Брак товаров)

См. раздел **defective_items** в **[analytics_cards_details_api.md](./analytics_cards_details_api.md)** (пример ответа и разграничение owner vs агент).

---

## Charts — графики и таблицы

### sales_by_date

Продажи по датам (или неделям/месяцам при `group_by`).

```json
[
  {
    "date": "2024-03-01",
    "sales_count": 12,
    "sales_amount": "45000.00"
  }
]
```

### transfers_by_date

Передачи по датам.

```json
[
  {
    "date": "2024-03-01",
    "transfers_count": 5,
    "items_transferred": 120
  }
]
```

### top_products_by_sales

Топ-10 товаров по сумме продаж.

```json
[
  {
    "product_id": "uuid",
    "product_name": "Товар А",
    "qty": 150.0,
    "amount": "75000.00"
  }
]
```

### top_users_by_sales

Топ-10 пользователей по сумме продаж.

```json
[
  {
    "user_id": "uuid",
    "user_name": "Иванов Иван",
    "role": "seller",
    "sales_count": 45,
    "sales_amount": "180000.00"
  }
]
```

### top_users_by_transfers

Топ-10 пользователей по объёму передач.

### sales_distribution_by_product

Доля продаж по товарам (в процентах).

```json
[
  {
    "product_id": "uuid",
    "product_name": "Товар А",
    "amount": "75000.00",
    "percent": 15.5
  }
]
```

### expense_breakdown — статьи расходов

Разбивка расходов по статьям (`CashFlow`, тип `EXPENSE`, статус `APPROVED`).

```json
[
  {
    "name": "Зарплата",
    "total": "250000.00",
    "count": 5
  },
  {
    "name": "Аренда",
    "total": "80000.00",
    "count": 1
  },
  {
    "name": "Без названия",
    "total": "15000.00",
    "count": 3
  }
]
```

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | string | Название статьи расхода |
| `total` | string | Сумма по статье |
| `count` | integer | Количество операций |

---

## Права доступа

- Требуется аутентификация (`IsAuthenticated`)
- Доступ только для владельца/админа (`_is_owner_like`)
- Фильтр по филиалу через `CompanyBranchRestrictedMixin` (параметр `branch`)

---

## Коды ошибок

| Код | Описание |
|-----|----------|
| 400 | Не задана компания у пользователя |
| 403 | Нет прав (не владелец/админ) |
| 401 | Не авторизован |

---

## Техническая информация

### Файлы

- **View:** `apps/main/views.py` → `OwnerOverallAnalyticsAPIView`
- **Логика:** `apps/main/analytics_owner_production.py` → `build_owner_analytics_payload`

### Модели

- `Sale`, `SaleItem` — продажи, валовая прибыль
- `Product` — стоимость склада
- `Debt`, `DebtPayment` — общий долг
- `CashFlow` (construction) — статьи расходов
- `ManufactureSubreal` — передачи
- `Acceptance` — приёмки
