# Детализация карточек аналитики — `GET /api/main/analytics/cards/details/`

Список строк для модального окна при клике на карточку дашборда. Отдельно от сводного `GET /api/main/owners/analytics/` (или аналогов для агента).

## Доступ и контекст

- **Метод:** `GET`
- **Авторизация:** любой аутентифицированный пользователь (включая агентов).
- **Компания:** у пользователя должна быть компания; иначе `400` с телом `{"detail": "У вас не задана компания."}`.
- **Филиал:** как у `products/list` и прочих — через миксин (`branch` в query, если поддерживается клиентом). В ответе всегда есть `branch_id` (UUID строкой или `null` для «без филиала»).

## Query-параметры (общие)

| Параметр | Описание |
|----------|----------|
| **`card`** | Обязательный ключ карточки (см. ниже). Без него — ошибка валидации. |
| `limit` | Размер страницы, по умолчанию `200`, максимум `1000`. |
| `offset` | Смещение, по умолчанию `0`, максимум `1000000`. |
| `branch` | UUID филиала (если передаётся фронтом в рамках миксина). |

Неверное или неподдерживаемое значение `card` — `400` с полями `card` (текст ошибки) и `supported` (массив допустимых ключей).

## Период (`period`, `date`, `date_from`, `date_to`)

Используется для **`transfers_count`**, **`items_transferred`**, **`acceptances_count`**, **`sales_count`**, **`sales_amount`**, **`discounts_total`**, а также **`revenue`**, **`cost_of_goods_sold`**, **`gross_profit`**, **`gross_margin_percent`** (разбивка по оплаченным продажам за период). Парсинг совпадает с [`_parse_period` в `analytics_agent.py`](../apps/main/analytics_agent.py): по умолчанию `period=month`, границы до 30 дней и т.д.

Параметр **`group_by`** в URL для этого эндпоинта **не используется** (ответ не группирует строки по дням; только срез списка `items`).

Для **`items_on_hand_qty`** / **`items_on_hand_amount`** период **не влияет** (снимок остатков «на руках» как в `analytics_agent`). Для **`accounts_receivable`**, **`accounts_payable`**, **`total_debt`** период в запросе **не используется** (текущие остатки/сальдо). Для остальных `card` без периода параметр `period` в URL можно не учитывать.

## Общая форма ответа

```json
{
  "card": "<тот же ключ, что в запросе>",
  "branch_id": "<uuid строкой> | null",
  "count": 0,
  "offset": 0,
  "limit": 200,
  "totals": {},
  "items": []
}
```

- **`count`** — число записей в **полном** наборе (до пагинации), кроме случаев, где явно указано иначе.
- **`items`** — страница `[offset : offset + limit]`.
- **`period`** — добавляется для карточек с периодом (передачи, приёмки, продажи, скидки):  
  `{"type": "...", "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}`.

---

## Поддерживаемые значения `card`

### `stock_purchase_value` / `stock_value` / `stock_retail_value`

Товары (`Product`) компании и филиала: те же фильтры, что у списка товаров.

**Элемент `items`:** `id`, `name`, `unit`, `kind`, `quantity`, `purchase_price`, `retail_price`, `purchase_sum`, `retail_sum` (суммы по строке — строки с двумя знаками после запятой).

**`totals`:** для **текущей страницы** `items` (не по всему складу):

- `purchase_sum`
- `retail_sum`

Разница ключей `card` только в семантике карточки; набор строк и полей одинаковый.

---

### `raw_material_value`

Единицы сырья (`ItemMake`) с учётом компании/филиала.

**`items`:** `id`, `name`, `unit`, `quantity`, `price`, `sum`, `supplier` (`{ "id", "full_name" }` или `null`).

**`totals`:** `sum` — сумма по **текущей странице** `items`.

---

### `defective_items`

Принятые возвраты от агентов (`ReturnFromAgent`, статус `ACCEPTED`), сгруппировано по товару.

- **Owner/admin:** все возвраты по компании (с учётом `branch`).
- **Агент:** только возвраты, где `returned_by = текущий пользователь`.

**`items`:** `product_id`, `product_name`, `qty` (сумма количества), `returns_count` (число возвратов).

**`totals`:** `qty` — суммарное количество по **всем** группам (не только по странице).

---

### `transfers_count`

Передачи (`ManufactureSubreal`) за период из `_parse_period`.

- **Owner/admin:** все передачи по компании; `created_at` от начала `date_from` 00:00 (локальная TZ) до **исключая** полночь после `date_to` (как `_dt_range` в `analytics_owner_production.py`).
- **Агент:** только свои передачи; `created_at` от 00:00 `date_from` до 23:59:59 `date_to` (включительно по календарным дням).

Фильтр по филиалу: либо конкретный `branch`, либо `branch__isnull=True`.

**`items`:** `id`, `created_at` (ISO), `status`, `qty_transferred`, `qty_accepted`, `qty_returned`, `agent` (`id`, `name`), `product` (`id`, `name`).

**`totals`:** `items_transferred` — сумма `qty_transferred` по **всему** запросу (не только страница).

---

### `items_transferred`

Те же данные, что **`transfers_count`**: список передач и **`totals.items_transferred`**. Отличается только **`card`** в ответе (карточка «перемещено единиц» на дашборде).

---

### `acceptances_count`

Приёмки по передачам (`Acceptance`) за период (`accepted_at`), фильтр филиала по **`subreal.branch`** (как в `analytics_owner_production` / `analytics_agent`).

- **Owner/admin:** все приёмки компании за интервал `accepted_at` как у владельца (`_dt_range`: до полуночи после `date_to` не включая).
- **Агент:** только приёмки по передачам, где **`subreal.agent`** = текущий пользователь; `accepted_at` от 00:00 `date_from` до конца дня `date_to` (как у агента в `analytics_agent`).

**`items`:** `id`, `accepted_at` (ISO), `qty`, `accepted_by` (`id`, `name`), `agent` (`id`, `name` — агент по передаче), `subreal` (`id`, `status`), `product` (`id`, `name`).

**`totals`:** `qty_accepted` — сумма `qty` по **всему** запросу.

---

### `sales_count` / `sales_amount`

Список оплаченных продаж (`Sale`, статус `PAID`) за период.

- **Owner/admin:** `created_at` в интервале `_dt_range` (как `analytics_owner_production`).
- **Агент:** только продажи с `user = текущий пользователь`; `created_at` от 00:00 `date_from` до конца дня `date_to` (как `analytics_agent`).

Фильтр по филиалу: `branch` или `branch__isnull=True`.

**`items`:** `id`, `created_at`, `total`, `discount_total`, `user` (`id`, `name`), `client` (`id`, `name` или `null`).

**`totals`:** `sales_amount` — сумма `total` по **всем** продажам в выборке (не только страница).

Оба значения `card` возвращают **одинаковую** структуру; отличается только поле **`card`** в JSON.

---

### `items_on_hand_qty` / `items_on_hand_amount`

Остатки на руках у **агента** (логика `_compute_agent_on_hand` в `analytics_agent.py`, как `items_on_hand_*` в сводке агента).

- **Owner/admin:** `items` пустой, `count: 0`, `totals.qty_on_hand = 0`, `totals.amount = "0.00"` (карточка только для роли агента).
- **Агент:** строки по товарам с положительным `qty_on_hand`; для `items_on_hand_amount` в каждой строке есть `amount`.

**`totals`:** `qty_on_hand`, `amount` (строка decimal) — по всей выборке.

---

### `revenue` / `cost_of_goods_sold` / `gross_profit` / `gross_margin_percent`

Разбивка по **товару** (`SaleItem` оплаченных продаж за период), формулы как в `analytics_owner_production.py` / `analytics_agent.py`:

- выручка по строке: `quantity × unit_price − line_discount`;
- COGS: `quantity × coalesce(purchase_price_snapshot, product.purchase_price)`.

- **Owner/admin:** интервал продаж как у **`sales_amount`** (`_dt_range` vs агент).
- **Агент:** только свои оплаченные продажи.

**`items`:** по строке товара: `product_id`, `product_name`, `revenue`, `cost_of_goods_sold`, `gross_profit`, `gross_margin_percent` (все суммы — строки decimal).

Фильтр строк по карточке: для **`revenue`** — только с выручкой &gt; 0; для **`cost_of_goods_sold`** — с COGS &gt; 0; для **`gross_profit`** — с выручкой или COGS &gt; 0; для **`gross_margin_percent`** — только с выручкой &gt; 0.

**`totals`:** сводные `revenue`, `cost_of_goods_sold`, `gross_profit`, `gross_margin_percent` по **всему** периоду (как на дашборде).

---

### `total_debt`

Сделки **`ClientDeal`** с `kind = debt` и **остатком** `(amount − prepayment) − оплачено по графику` **&gt; 0**.

- **Owner/admin:** по компании и филиалу (как в сводке владельца).
- **Агент:** только сделки клиентов, у которых **`client.salesperson`** = текущий пользователь (как часть дебиторки агента).

**`items`:** `id`, `title`, `client`, `amount`, `prepayment`, `paid`, `remaining`.

**`totals`:** `total_debt` — сумма `remaining` по всем строкам.

---

### `accounts_receivable`

Объединённый список (сортировка по сумме по убыванию):

1. **`kind: "client_deal"`** — те же остатки по рассрочке, что для **`total_debt`** (у агента — только «свои» клиенты).
2. **`kind: "sale_debt"`** — продажи **`Sale`** со статусом **`DEBT`** (у владельца — по компании/филиалу; у агента — только `user = текущий пользователь`).

**`totals`:** `accounts_receivable`, `accounts_receivable_client_deals`, `accounts_receivable_pos_sales`.

---

### `accounts_payable`

Только **owner/admin**: по каждому контрагенту склада (**поставщик** / **оба** типа) считается сальдо как в `analytics_owner_production` (товарные документы + денежные документы); в список попадают только контрагенты, которым компания **должна** (payable &gt; 0).

**Агент:** пустой ответ, `totals.accounts_payable = "0.00"`.

Если модуль склада недоступен — также пустой список.

**`items`:** `counterparty_id`, `name`, `accounts_payable`.

**`totals`:** `accounts_payable` — сумма по строкам (без отдельной строки building-ledger из сводки; при необходимости уточняйте в коде сводки).

---

### `discounts_total`

Оплаченные продажи (`Sale`, статус `PAID`) с `discount_total > 0` за период (`created_at` от начала `date_from` до конца `date_to` дня).

- **Owner/admin:** все продажи компании (с `branch`).
- **Агент:** только продажи, где `user = текущий пользователь`.

Группировка: пара сотрудник + клиент.

**`items`:** `user` (`id`, `name`), `client` (`id`, `name`), `sales_count`, `discounts_total` (строка decimal).

**`totals`:** `discounts_total` — сумма скидок по **всем** группам.

---

### `users_count`

Сотрудники компании: `User` с `company = текущая компания` (как счётчик `users_count` в сводной аналитике). Период не влияет.

**`items`:** `id`, `email`, `first_name`, `last_name`, `phone_number`, `role`, `is_active`.

**`totals`:** пустой объект `{}`.

---

## Список ключей для ошибки `supported`

Актуальный перечень дублируется в ответе API при неверном `card`:

`stock_purchase_value`, `stock_retail_value`, `raw_material_value`, `stock_value`, `defective_items`, `discounts_total`, `transfers_count`, `items_transferred`, `acceptances_count`, `sales_count`, `sales_amount`, `items_on_hand_qty`, `items_on_hand_amount`, `revenue`, `cost_of_goods_sold`, `gross_profit`, `gross_margin_percent`, `accounts_receivable`, `accounts_payable`, `total_debt`, `users_count`.

## Примеры

```http
GET /api/main/analytics/cards/details/?card=stock_purchase_value&limit=50&offset=0
GET /api/main/analytics/cards/details/?card=defective_items&limit=200&offset=0
GET /api/main/analytics/cards/details/?card=transfers_count&period=month&group_by=day
GET /api/main/analytics/cards/details/?card=acceptances_count&period=month&limit=200&offset=0
GET /api/main/analytics/cards/details/?card=items_transferred&period=month
GET /api/main/analytics/cards/details/?card=sales_amount&period=month
GET /api/main/analytics/cards/details/?card=items_on_hand_qty
GET /api/main/analytics/cards/details/?card=discounts_total&period=custom&date_from=2026-04-01&date_to=2026-04-18
GET /api/main/analytics/cards/details/?card=users_count&limit=200&offset=0
GET /api/main/analytics/cards/details/?card=gross_profit&period=month
GET /api/main/analytics/cards/details/?card=accounts_receivable&limit=50&offset=0
GET /api/main/analytics/cards/details/?card=total_debt
GET /api/main/analytics/cards/details/?card=accounts_payable
```
