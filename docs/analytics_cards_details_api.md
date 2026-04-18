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

Используется только для карточек **`transfers_count`** и **`discounts_total`**. Парсинг совпадает с [`_parse_period` в `analytics_agent.py`](../apps/main/analytics_agent.py): по умолчанию `period=month`, границы до 30 дней и т.д.

Параметр **`group_by`** в URL для этого эндпоинта **не используется** (ответ не группирует строки по дням; только срез списка `items`).

Для остальных `card` период в запросе можно передавать (как на дашборде), но на выборку **не влияет**.

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
- **`period`** — добавляется только для `transfers_count` и `discounts_total`:  
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

`stock_purchase_value`, `stock_retail_value`, `raw_material_value`, `stock_value`, `defective_items`, `discounts_total`, `transfers_count`, `users_count`.

## Примеры

```http
GET /api/main/analytics/cards/details/?card=stock_purchase_value&limit=50&offset=0
GET /api/main/analytics/cards/details/?card=defective_items&limit=200&offset=0
GET /api/main/analytics/cards/details/?card=transfers_count&period=month&group_by=day
GET /api/main/analytics/cards/details/?card=discounts_total&period=custom&date_from=2026-04-01&date_to=2026-04-18
GET /api/main/analytics/cards/details/?card=users_count&limit=200&offset=0
```
