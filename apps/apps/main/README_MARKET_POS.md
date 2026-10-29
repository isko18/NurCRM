# Документация (Market POS) — смены, продажи, сканер, товары, аналитика

**Архитектура продажи (модели `Sale`/`Cart`, `checkout_cart`, `mark_paid`, долг, возврат):** см. отдельный файл **`MAIN_SALE_FULL_RU.md`** в этой же папке.

Этот документ описывает **кассовые продажи (POS)** в приложении `apps/main` для сферы **Маркет**:
- **Открытие/закрытие смены** (CashShift) — находится в `apps/construction`, но является обязательной частью флоу продаж.
- **Продажа**: старт → добавление товаров (сканер/вручную) → оплата → чек/накладная.
- **Добавление товара**: по штрихкоду (из глобальной базы) и вручную.
- **Аналитика по маркету**: продажи/склад/кассы/смены.

Базовый префикс API в проекте: `POST/GET /api/...` (см. `core/urls.py`).

---

## Общие правила компании/филиала (важно)

Большинство endpoint-ов `main` используют `CompanyBranchRestrictedMixin` (см. `apps/main/views.py`):
- **company** берётся из пользователя (`request.user.company` или `owned_company`).
- **активный филиал** определяется так:
  - если у пользователя есть «жёстко назначенный» филиал (primary/branch/branch_ids/…): он используется всегда;
  - иначе можно указать `?branch=<uuid>` в запросе;
  - если филиала нет → используется **глобальный режим** (работа только с записями `branch IS NULL`).

Практика:
- для маркет‑кассы обычно работает `?branch=<uuid>` или закреплённый филиал у кассира.

---

## 1) Смена (CashShift): открыть / закрыть

### Зачем
В `main` продажи в POS **требуют открытую смену кассира в выбранной кассе**:
- `POST /api/main/pos/sales/start/` вернёт `400`, если у кассира нет **OPEN** смены на этой кассе.
- `POST /api/main/pos/sales/<cart_id>/checkout/` подставляет смену в корзину, если её не было; без открытой смены — та же ошибка.

Смена живёт в приложении **`construction`** (модель `CashShift`), не в `main`.

### Базовый URL
Префикс API: **`/api/construction/`** (см. `core/urls.py`).

| Действие | Метод | Путь |
|----------|--------|------|
| Список смен | `GET` | `/api/construction/shifts/` |
| Деталь смены | `GET` | `/api/construction/shifts/<uuid>/` |
| **Открыть смену** | `POST` | `/api/construction/shifts/open/` |
| **Закрыть смену** | `POST` | `/api/construction/shifts/<uuid>/close/` |
| Продажи по смене (история) | `GET` | `/api/construction/cash/shifts/<uuid>/sales/` |

Заголовки: **`Authorization: Bearer <access_token>`**, как для остального API.

### Филиал (`?branch=`)
Эндпоинт открытия смены использует **`CompanyBranchScopedMixin`**: список доступных **касс** в теле запроса фильтруется по активному филиалу.

- Если у пользователя выбран филиал или передан **`?branch=<uuid>`** — в `POST .../shifts/open/` в поле `cashbox` можно указывать только кассы **этого филиала**.
- В **глобальном** режиме (без филиала) — только кассы с **`branch IS NULL`** (общекомпанейские).

Практика для маркета: перед открытием смены вызывать API с тем же `branch`, что и для POS (`/api/main/...?branch=...`), иначе касса может быть «не в списке».

### Как открыть смену (пошагово)

1. Убедиться, что в нужном филиале есть **`Cashbox`** (касса). Если касс нет — создать в админке или через API приложения `construction` (см. `apps/construction/`).
2. **Опционально:** `GET /api/construction/shifts/?status=open` — посмотреть уже открытые смены текущего кассира.
3. **`POST /api/construction/shifts/open/`** с телом:

```json
{
  "cashbox": "<uuid кассы>",
  "opening_cash": "0.00"
}
```

| Поле | Обязательность | Смысл |
|------|----------------|--------|
| `cashbox` | Да | UUID кассы (`Cashbox`), из списка, отфильтрованного по компании и филиалу |
| `opening_cash` | Нет (по умолчанию `0.00`) | Сумма наличных «на старте» смены в кассе |
| `cashier` | Нет | UUID пользователя-кассира. **Обычный кассир не передаёт** — смена открывается **на себя**. Передать другого кассира может только **владелец/админ** (`_is_owner_like`) |

**Ответ `201`:** объект смены (сериализатор `CashShiftListSerializer`). Для **открытой** смены в ответе подмешиваются «живые» итоги: `sales_count`, `sales_total`, `expected_cash` и т.д. (см. `calc_live_totals` в модели).

**Особые случаи:**
- Если у этого **кассира** на этой **кассе** уже есть смена со статусом **`open`**, сервер **не создаёт дубликат**, а возвращает **ту же** смену (`201`, существующая запись) — идемпотентность.
- Нельзя открыть вторую OPEN смену **тому же кассиру на той же кассе** (ограничение БД `uq_open_shift_per_cashbox_cashier`). При попытке логика выше вернёт существующую.
- На **одну кассу** могут одновременно работать **разные кассиры** (несколько OPEN смен), если это разные пользователи.

### Как закрыть смену

`POST /api/construction/shifts/<shift_id>/close/`

Тело:

```json
{
  "closing_cash": "12500.50"
}
```

| Поле | Смысл |
|------|--------|
| `closing_cash` | Обязательно. Фактическая сумма наличных в кассе на момент закрытия |

Правила доступа:
- **Кассир** может закрыть **только свою** смену (`shift.cashier == текущий пользователь`).
- **Владелец/админ** — по логике прав в коде (см. `CashShiftCloseView`).

После закрытия вызывается **`shift.close(closing_cash)`**: статус `closed`, фиксируются `income_total`, `expense_total`, агрегаты продаж за смену и т.д.

### Типичные ошибки

| Ситуация | Что проверить |
|----------|----------------|
| `400` при открытии: касса не из списка | Филиал в запросе (`?branch=`) и филиал кассы |
| POS: «Смена не открыта» | Сначала `POST .../shifts/open/`, тот же пользователь и касса, что в `sales/start` |
| `403` при закрытии чужой смены | Закрывает только владелец смены (или админ) |

Код: `apps/construction/views.py` (`CashShiftOpenView`, `CashShiftCloseView`), `apps/construction/serializers.py` (`CashShiftOpenSerializer`, `CashShiftCloseSerializer`), модель `CashShift` в `apps/construction/models.py`.

---

## 2) Продажа (POS): старт → позиции → оплата → чек

В `main` продажа реализована через **корзину** (`Cart`) и её позиции (`CartItem`), которые затем оформляются в **продажу** (`Sale`) и позиции продажи (`SaleItem`).

Ключевые модели:
- `Cart` / `CartItem` / `Sale` / `SaleItem` — `apps/main/models.py`
- POS API — `apps/main/pos_views.py`, сериализаторы — `apps/main/pos_serializers.py`

### 2.1 Старт продажи (создать/получить активную корзину)
`POST /api/main/pos/sales/start/`

Тело:
```json
{
  "cashbox_id": "uuid-кассы (опционально)",
  "order_discount_total": "0.00 (опционально — фиксированная скидка)",
  "order_discount_percent": "10 (опционально — скидка в % от subtotal)"
}
```

Передаётся **либо** `order_discount_total`, **либо** `order_discount_percent`. Скидка в % пересчитывается при каждом изменении корзины.

Правила:
- **не открывает смену автоматически** — если у кассира нет OPEN смены в кассе, вернёт:
  - `400 {"detail":"Смена не открыта. Сначала откройте смену на кассе, затем начните продажу."}`
- находит **открытую смену текущего пользователя** в указанной кассе (или автоподбор кассы по филиалу);
- возвращает активную корзину `Cart` со статусом `active` для этой смены/кассира;
- если у пользователя было несколько активных корзин в этой смене — лишние помечаются `checked_out`.

Ответ: сериализатор `SaleCartSerializer` (корзина + items + суммы).

### 2.2 Добавить товар сканером (штрихкод)
`POST /api/main/pos/sales/<cart_id>/scan/`

Тело:
```json
{
  "barcode": "штрихкод",
  "quantity": "1.000 (опционально, Decimal с 3 знаками)"
}
```

Поведение:
- ищет `Product` вашей компании по `barcode`;
- если не найден — пробует распарсить **весовой EAN‑13** и найти товар по `plu`:
  - формат: `PP CCCCC EEEEE K` (см. `_parse_scale_barcode()` в `pos_views.py`)
  - количество берётся из веса (в кг), а не из `quantity`;
- если позиция `CartItem` уже есть — увеличивает `quantity`, иначе создаёт.

Разбор ШК весов настраивается тремя переключателями компании
(`PATCH /api/users/settings/company/`, поля видны в `GET /api/users/company/`):

| Поле | Значения | Что задаёт |
|---|---|---|
| `scale_barcode_mode` | `auto` (деф.) / `weight` / `amount` | что зашито в поле `EEEEE`: вес или сумма. `auto` — по префиксу: 20 = вес, 25 = сумма |
| `scale_barcode_layout` | `plu` (деф.) / `code` | границы полей: `PP+PLU(5)+EEEEE(5)` или `PP+PLU(6)+EEEE(4)` |
| `scale_barcode_amount_unit` | `tiyin` (деф.) / `som` | единица суммы: `03800` = 38.00 сом либо `00036` = 36 сом |

Если раскладка настроена не так, как печатают весы, товар всё равно ищется —
после промаха по основной раскладке пробуется альтернативная (в лог `crm.pos.scan`
пишется предупреждение).

Пример: этикетка `2 00001 00036 6` (Банан вес, 0.190 кг, 190 сом/кг, сумма 36 сом).
Префикс 20, но весы зашили СУММУ целыми сомами. Нужны `scale_barcode_mode=amount`
и `scale_barcode_amount_unit=som` → PLU 1, сумма 36 сом, количество 36 / 190 = 0.189 кг.
При дефолтных настройках тот же ШК читался бы как вес 36 г.

Типичные ошибки:
- `404 {"not_found": true, "message": "Товар не найден"}`
- `404 {"not_found": true, "message": "Товар с ПЛУ 12345 не найден"}`

### 2.3 Добавить товар вручную (по product_id)
`POST /api/main/pos/sales/<cart_id>/add-item/`

Тело:
```json
{
  "product_id": "uuid-товара",
  "quantity": "1.000 (опционально)",
  "unit_price": "опционально — базовая цена",
  "discount_total": "опционально — скидка на строку"
}
```

Правила:
- можно передать `unit_price`, `discount_total` или **оба**;
- `unit_price` — базовая цена; `discount_total` — скидка на строку (хранятся отдельно);
- эффективная цена за единицу = `unit_price - (discount_total / quantity)`;
- эффективная цена не может быть ниже закупочной (`product.purchase_price`).

### 2.4 Добавить позицию «вручную без товара» (кастом)
`POST /api/main/pos/carts/<cart_id>/custom-item/`

Используйте для услуг/разовых позиций без карточки товара.

Тело:
```json
{
  "name": "Название позиции",
  "price": "100.00",
  "quantity": 1
}
```

### 2.5 Изменить/удалить позицию в корзине
`PATCH /api/main/pos/carts/<cart_id>/items/<item_id>/`

Тело:
```json
{
  "quantity": "2.500",
  "unit_price": "100.00",
  "discount_total": "10.00"
}
```

Правила:
- `quantity = 0` → позиция удаляется;
- `quantity < 0` → ошибка;
- **цена и скидка меняются независимо**: можно передать только `unit_price`, только `discount_total` или оба;
- `unit_price` — базовая цена; `discount_total` — скидка на строку (хранятся отдельно).

`DELETE /api/main/pos/carts/<cart_id>/items/<item_id>/` — удалить позицию.

### 2.5.1 Изменить скидку на чек
`PATCH /api/main/pos/carts/<cart_id>/`

Тело:
```json
{
  "order_discount_percent": "10"
}
```
или
```json
{
  "order_discount_total": "50.00"
}
```

Скидка в % пересчитывается от текущего subtotal при каждом recalc (добавление/удаление товаров).

Подробнее: `docs/MARKET_POS_CART_FRONTEND_API.md`

### 2.6 Завершить продажу (оплата)
`POST /api/main/pos/sales/<cart_id>/checkout/`

Тело (минимально для наличных):
```json
{
  "payment_method": "cash",
  "cash_received": "500.00",
  "print_receipt": false
}
```

Тело (безнал):
```json
{
  "payment_method": "transfer",
  "cash_received": "0.00",
  "print_receipt": false
}
```

Важные правила:
- если корзина **ещё не привязана к смене**, сервер сам найдёт OPEN смену кассира в кассе (можно подсказать `cashbox_id`, либо `shift_id`):
  - если нет смены → `400 {"detail":"Смена не открыта..."}`
- для `payment_method=cash`: `cash_received` обязателен и должен быть \(\ge\) `cart.total`, иначе:
  - `400 {"detail":"Сумма, полученная наличными, меньше суммы продажи."}`
- оформление выполняет `checkout_cart(cart)`:
  - возможна ошибка остатков: `400 {"detail":"..."}` (исключение `NotEnoughStock`)

Ответ (важные поля):
- `sale_id`, `status`
- `total`, `cash_received`, `change`
- `shift_id`, `cashbox_id`
- опционально `receipt_text`, если `print_receipt=true`

### 2.7 Получить чек/данные для печати и накладную

Данные чека (JSON‑payload для печати):
- `GET /api/main/pos/sales/<sale_id>/receipt/`

PDF накладная:
- `GET /api/main/sales/<sale_id>/invoice/`

JSON версии документов:
- `GET /api/main/sales/json/<sale_id>/receipt/`
- `GET /api/main/sales/json/<sale_id>/invoice/`

### 2.8 История продаж
- `GET /api/main/pos/sales/` — список продаж
  - фильтры: `status`, `user`, `paid=1`, `start=<date|datetime>`, `end=<date|datetime>`
- `GET /api/main/pos/sales/<sale_id>/` — детали продажи

---

## 3) Добавление товара: сканер / вручную

### 3.1 Добавить товар по штрихкоду (из глобальной базы)
`POST /api/main/products/create-by-barcode/`

Назначение:
- если штрихкод уже есть в вашей компании → ошибка
- если штрихкод найден в `GlobalProduct` → создаётся `Product` в вашей компании с подтянутыми `brand/category/name`
- если штрихкод **не найден** в глобальной базе → `404` и подсказка создать вручную

Минимальное тело:
```json
{
  "barcode": "штрихкод"
}
```

Можно дополнять: `purchase_price`, `markup_percent`, `price`, `quantity`, `is_weight`, `unit`, `expiration_date`, `packages` и др. (см. реализацию в `apps/main/views.py`).

### 3.2 Создать товар вручную (опционально добавив в глобальную базу)
`POST /api/main/products/create-manual/`

Минимальное тело:
```json
{
  "name": "Название товара",
  "price": "100.00",
  "purchase_price": "70.00",
  "barcode": "штрихкод (опционально)"
}
```

Поведение:
- если указан `barcode`, и его нет в `GlobalProduct`, он будет добавлен туда автоматически (`get_or_create`);
- проверка уникальности `barcode` внутри компании.

### 3.3 Найти товар по штрихкоду (в вашей компании)
`GET /api/main/products/barcode/<barcode>/`

### 3.4 Найти товар по штрихкоду (только глобальная база)
`GET /api/main/products/global-barcode/<barcode>/`

---

## 4) Аналитика по маркету

Endpoint:
`GET /api/main/analytics/market/`

Параметры:
- `tab`: `sales` | `stock` | `cashboxes` | `shifts` (по умолчанию `sales`)
- период:
  - `date_from=YYYY-MM-DD` или ISO datetime
  - `date_to=YYYY-MM-DD` или ISO datetime
- фильтры для продаж (вкладка `sales`):
  - `cashbox=<uuid>`
  - `shift=<uuid>`
  - `cashier=<uuid>`
  - `payment_method=<cash|transfer|...>`
  - `min_total`, `max_total`
- `include_global=1` (если в данных есть `branch IS NULL` и вы хотите включать их вместе с филиалом)

Что считает `tab=sales` (см. `apps/main/analytics_market.py`):
- `revenue`, `tx` (кол-во продаж), `clients`
- `daily` (выручка по дням)
- `top_products` (топ товаров по выручке)
- маржинальность (если доступна себестоимость):
  - `cogs`, `gross_profit`, `margin_percent`
  - предупреждение `cogs_warning`, если себестоимость нулевая/не заполнена

Пример:
`GET /api/main/analytics/market/?tab=sales&date_from=2026-02-01&date_to=2026-02-02&cashbox=<uuid>`

---

## Примечания по сканеру

В POS «сканер» реализован как **отправка штрихкода с фронта** в `/api/main/pos/sales/<cart_id>/scan/`.

В коде также есть модель `MobileScannerToken` и endpoint выдачи токена:
- `POST /api/main/pos/sales/<cart_id>/mobile-scanner/` → `{token, expires_at}`

При этом обработчик приёма сканов по токену (`MobileScannerIngestAPIView` в `apps/main/pos_views.py`)
**пока не подключён в `apps/main/urls.py`**. Если планируете мобильный «второй экран»/камера‑сканер — нужно добавить роут.

