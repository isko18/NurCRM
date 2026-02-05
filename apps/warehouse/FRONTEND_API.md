Документация API для фронтенда — модуль склада (apps/warehouse)

> Цель: дать фронту один “источник правды” по тому, как работать со складом: справочники (склады/бренды/категории), товары (включая фото/упаковки/характеристики), складские документы (проведение/отмена), денежные документы.

## 1) База и общие правила

### Базовый префикс
- Все эндпойнты модуля: `/api/warehouse/`
- Prod backend: `https://app.nurcrm.kg` → полный путь: `https://app.nurcrm.kg/api/warehouse/`

### Аутентификация
- Используется JWT (см. настройки): заголовок `Authorization: Bearer <access_token>`

### Скоуп по компании/филиалу (важно)
Все вьюхи склада используют ограничение `CompanyBranchRestrictedMixin`:
- **company** берётся из пользователя.
- **branch (филиал)** выбирается автоматически:
  - если пользователь “жёстко” привязан к филиалу (например `user.primary_branch()` / `user.branch`) — используется он;
  - иначе можно передавать `?branch=<uuid>` в query.

Рекомендация фронту:
- всегда держать “активный филиал” и прокидывать `branch` в запросы, если у пользователя нет фиксированного филиала.

### Пагинация (DRF PageNumberPagination)
В списках возвращается объект вида:
```json
{
  "count": 123,
  "next": "http://.../api/warehouse/documents/?page=2",
  "previous": null,
  "results": [ ... ]
}
```
Параметры:
- `?page=1|2|...`

### Ошибки
- **401**: нет/невалидный токен.
- **403**: нет прав (или попытка доступа к чужим данным).
- **404**: объект не найден.
- **400**:
  - field-validation: `{"field": ["ошибка"]}`
  - business-validation: `{"detail": "текст ошибки"}`

## 2) Справочники (склады / бренды / категории / контрагенты)

### 2.1 Склады (полный CRUD)
Эндпойнты:
- `GET /api/warehouse/` — список складов
- `POST /api/warehouse/` — создать склад
- `GET /api/warehouse/{warehouse_uuid}/` — детали
- `PATCH/PUT /api/warehouse/{warehouse_uuid}/` — обновить
- `DELETE /api/warehouse/{warehouse_uuid}/` — удалить

Сериализатор (ответ/тело):
```json
{
  "id": "uuid",
  "name": "string|null",
  "location": "string",
  "status": "active|inactive",
  "company": "uuid (read-only)",
  "branch": "uuid|null (read-only)"
}
```

Фильтры списка (`GET /api/warehouse/`):
- `name` (icontains)
- `status` (`active|inactive`)
- `created_after` (datetime, `created_date__gte`)
- `created_before` (datetime, `created_date__lte`)
- `company`, `branch` — технически есть в filterset, но фактически доступ ограничен текущим пользователем (обычно фронту не нужно).

### 2.2 Бренды
Эндпойнты:
- `GET /api/warehouse/brands/`
- `POST /api/warehouse/brands/`
- `GET/PATCH/PUT/DELETE /api/warehouse/brands/{brand_uuid}/`

Тело/ответ:
```json
{
  "id": "uuid",
  "company": "uuid (read-only)",
  "branch": "uuid|null (read-only)",
  "name": "string",
  "parent": "uuid|null"
}
```

Фильтр:
- `name` (icontains)

### 2.3 Категории
Эндпойнты:
- `GET /api/warehouse/category/`
- `POST /api/warehouse/category/`
- `GET/PATCH/PUT/DELETE /api/warehouse/category/{category_uuid}/`

Тело/ответ:
```json
{
  "id": "uuid",
  "company": "uuid (read-only)",
  "branch": "uuid|null (read-only)",
  "name": "string",
  "parent": "uuid|null"
}
```

Примечание:
- В текущей реализации на списке категорий фильтры не подключены (вьюха без `filterset_class`).

### 2.4 Контрагенты (только “simple CRUD” для документов)
Эндпойнты:
- `GET /api/warehouse/crud/counterparties/`
- `POST /api/warehouse/crud/counterparties/`
- `GET/PATCH/PUT/DELETE /api/warehouse/crud/counterparties/{id}/`

Тело/ответ:
```json
{
  "id": "uuid",
  "name": "string",
  "type": "CLIENT|SUPPLIER|BOTH",
  "company": "uuid|null (read-only)",
  "branch": "uuid|null (read-only)",
  "agent": "uuid|null (read-only)"
}
```

Примечание:
- для агента список/детали контрагентов показываются **только свои**;
- при создании агентом поле `agent` проставляется автоматически.
- `company` и `branch` не передаются: сервер проставляет сам.

## 3) Товары (основной CRUD + вложенные сущности)

### 3.1 Товары по складу (основной API)
Эндпойнты:
- `GET /api/warehouse/{warehouse_uuid}/products/` — список товаров склада
- `POST /api/warehouse/{warehouse_uuid}/products/` — создать товар в этом складе
- `GET /api/warehouse/products/{product_uuid}/` — детали товара (глобально по uuid товара)
- `PATCH/PUT/DELETE /api/warehouse/products/{product_uuid}/` — обновить/удалить

Сериализатор товара (ответ):
```json
{
  "id": "uuid",
  "name": "string",
  "article": "string|null",
  "description": "string|null",
  "barcode": "string|null",
  "code": "string|null",
  "unit": "string",
  "is_weight": true,
  "quantity": "0.000",
  "purchase_price": "0.00",
  "markup_percent": "0.00",
  "price": "0.00",
  "discount_percent": "0.00",
  "plu": 123,
  "country": "string|null",
  "status": "pending|accepted|rejected|null",
  "stock": false,
  "expiration_date": "YYYY-MM-DD|null",
  "brand": "uuid|null",
  "category": "uuid",
  "warehouse": "uuid",
  "characteristics": {
    "height_cm": "0.00|null",
    "width_cm": "0.00|null",
    "depth_cm": "0.00|null",
    "factual_weight_kg": "0.000|null",
    "description": "string"
  },
  "images": [
    {
      "id": "uuid",
      "product": "uuid",
      "image_url": "https://.../media/products/...webp",
      "is_primary": true,
      "alt": "string",
      "created_at": "2026-02-01T12:00:00Z"
    }
  ],
  "packages": [
    {
      "id": "uuid",
      "product": "uuid",
      "name": "коробка",
      "quantity_in_package": "10.000",
      "unit": "шт.",
      "created_at": "2026-02-01T12:00:00Z"
    }
  ]
}
```

Особенности (важно для UI/логики):
- `code` может автогенерироваться (уникален в рамках склада).
- для весовых товаров (`is_weight=true`) может автогенерироваться `plu` (уникален в рамках склада).
- `price` может пересчитываться автоматически из `purchase_price` и `markup_percent`.
- `barcode`, `code`, `article`, `country` нормализуются (пустая строка → `null`).

Фильтры списка товаров склада (`GET /api/warehouse/{warehouse_uuid}/products/`):
- `name` (icontains)
- `article` (icontains)
- `price_min`, `price_max`
- `purchase_price_min`, `purchase_price_max`
- `markup_min`, `markup_max`
- `brand`, `category`, `warehouse` (как id)
- `status` (`pending|accepted|rejected`)
- `stock` (boolean)

### 3.1.1 Скан штрихкода (быстрое добавление товара)
Эндпойнт:
- `POST /api/warehouse/{warehouse_uuid}/products/scan/`

Тело:
```json
{
  "barcode": "string",
  "name": "string",
  "category": "uuid"
}
```

Поведение:
- если товар с таким `barcode` найден в этом складе → вернёт его (`created=false`);
- если `barcode` похож на весовой EAN-13 → пытается найти товар по `plu`, вернёт `scan_qty`;
- если товар не найден → создаст (нужны `name` + `category`, остальные поля как в обычном POST).

Ответ:
```json
{
  "created": true,
  "scan_qty": "0.000|null",
  "product": { "...": "как в 3.1" }
}
```

Ошибки:
- `400` если `barcode` пустой;
- `400` если товара нет и не переданы обязательные поля для создания.

### 3.2 Фото товара
Эндпойнты:
- `GET /api/warehouse/products/{product_uuid}/images/`
- `POST /api/warehouse/products/{product_uuid}/images/`
- `GET/PATCH/PUT/DELETE /api/warehouse/products/{product_uuid}/images/{image_uuid}/`

Создание/обновление:
- `Content-Type: multipart/form-data`
- поле `image` — обязательно (JPG/PNG/WEBP; сервер конвертирует в WEBP)
- опционально: `alt`, `is_primary`

Ответ включает:
- `image_url` (абсолютный URL, если доступен `request`)

### 3.3 Упаковки товара
Эндпойнты:
- `GET /api/warehouse/products/{product_uuid}/packages/`
- `POST /api/warehouse/products/{product_uuid}/packages/`
- `GET/PATCH/PUT/DELETE /api/warehouse/products/{product_uuid}/packages/{package_uuid}/`

Тело/ответ:
```json
{
  "id": "uuid",
  "product": "uuid",
  "name": "string",
  "quantity_in_package": "0.000",
  "unit": "string",
  "created_at": "2026-02-01T12:00:00Z"
}
```

Валидация:
- `quantity_in_package` > 0

### 3.4 “Простой” CRUD товаров (для выбора в документах / быстрый поиск)
Эндпойнты:
- `GET /api/warehouse/crud/products/`
- `POST /api/warehouse/crud/products/`
- `GET/PATCH/PUT/DELETE /api/warehouse/crud/products/{id}/`

Тело/ответ (упрощённый):
```json
{
  "id": "uuid",
  "name": "string",
  "article": "string",
  "barcode": "string|null",
  "unit": "string",
  "quantity": "0.000"
}
```

Поиск:
- `?search=text` (ищет по `name/article/barcode`)

Примечание:
- для поиска по штрихкоду сервер использует кэш (если `search` достаточно длинный).

### 3.5 “Простой” CRUD складов (для селектов)
- `GET /api/warehouse/crud/warehouses/`
- `POST /api/warehouse/crud/warehouses/`
- `GET/PATCH/PUT/DELETE /api/warehouse/crud/warehouses/{id}/`

Ответ:
```json
{ "id": "uuid", "name": "string" }
```

## 4) Складские документы (товарные)

### 4.1 Типы и статусы
- `doc_type`:
  - `SALE` (Продажа)
  - `PURCHASE` (Покупка)
  - `SALE_RETURN` (Возврат продажи)
  - `PURCHASE_RETURN` (Возврат покупки)
  - `INVENTORY` (Инвентаризация)
  - `RECEIPT` (Приход)
  - `WRITE_OFF` (Списание)
  - `TRANSFER` (Перемещение)
- `status`: `DRAFT` | `POSTED`

### 4.2 Эндпойнты документов
Базовый список:
- `GET /api/warehouse/documents/`
- `POST /api/warehouse/documents/`

Отдельные списки по типам (удобно для вкладок/страниц):
- `GET/POST /api/warehouse/documents/sale/`
- `GET/POST /api/warehouse/documents/purchase/`
- `GET/POST /api/warehouse/documents/sale-return/`
- `GET/POST /api/warehouse/documents/purchase-return/`
- `GET/POST /api/warehouse/documents/inventory/`
- `GET/POST /api/warehouse/documents/receipt/`
- `GET/POST /api/warehouse/documents/write-off/`
- `GET/POST /api/warehouse/documents/transfer/`

Детали:
- `GET/PATCH/PUT/DELETE /api/warehouse/documents/{id}/`

Проведение/отмена:
- `POST /api/warehouse/documents/{id}/post/`
- `POST /api/warehouse/documents/{id}/unpost/`

### 4.2.1 Документы агента (по своим товарам)
Эндпойнты:
- `GET /api/warehouse/agent/documents/`
- `POST /api/warehouse/agent/documents/`
- `GET/PATCH/PUT/DELETE /api/warehouse/agent/documents/{id}/`

Важно:
- `agent` проставляется сервером (текущий пользователь).
- Для агента нельзя `TRANSFER` и `INVENTORY`.
- Контрагент должен принадлежать агенту.
- Товары в `items[]` должны быть в остатках агента (иначе 400).

### 4.3 Формат документа (сериализатор)
```json
{
  "id": "uuid",
  "doc_type": "SALE",
  "status": "DRAFT|POSTED",
  "number": "SALE-20260201-0001|null",
  "date": "2026-02-01T12:00:00Z",
  "warehouse_from": "uuid|null",
  "warehouse_to": "uuid|null",
  "counterparty": "uuid|null",
  "agent": "uuid|null",
  "counterparty_display_name": "string|null",
  "comment": "string",
  "total": "0.00",
  "items": [
    {
      "id": "uuid",
      "product": "uuid",
      "qty": "1.000",
      "price": "150.00",
      "discount_percent": "0.00",
      "line_total": "150.00"
    }
  ]
}
```

Read-only поля:
- `number`, `total`, `status`, `date`

### 4.4 Создание документа (пример)
`POST /api/warehouse/documents/`
```json
{
  "doc_type": "SALE",
  "warehouse_from": "11111111-1111-1111-1111-111111111111",
  "counterparty": "22222222-2222-2222-2222-222222222222",
  "comment": "Продажа",
  "items": [
    {
      "product": "33333333-3333-3333-3333-333333333333",
      "qty": "3",
      "price": "150.00",
      "discount_percent": "0"
    }
  ]
}
```

Если используете typed-endpoint (например `/documents/sale/`):
- `doc_type` можно не передавать — сервер проставит сам.

### 4.5 Обновление документа (важно)
`PATCH/PUT /api/warehouse/documents/{id}/`
- если документ **POSTED** → сервер вернёт 400: нельзя изменять проведённый документ.
- если вы передаёте `items`, сервер:
  - удаляет все старые строки,
  - создаёт новые строки из массива `items`.

Рекомендация фронту:
- редактировать строки локально и отправлять полный `items[]` одним запросом.

### 4.6 Фильтры и поиск по документам
Список (`/documents/` и typed endpoints):
- фильтры: `doc_type`, `status`, `warehouse_from`, `warehouse_to`, `counterparty`
- поиск: `?search=...` (по `number` и `comment`)

### 4.7 Проведение и отмена
Проведение:
- `POST /api/warehouse/documents/{id}/post/`
- тело **может** содержать `allow_negative` (boolean или строка `"true"|"1"|"yes"`) для обхода проверки отрицательных остатков:
```json
{ "allow_negative": true }
```
- при успехе: 200 + сериализованный документ (status станет `POSTED`, `number` будет сгенерирован).

Отмена:
- `POST /api/warehouse/documents/{id}/unpost/`
- при успехе: документ возвращается в `DRAFT`, движения удаляются, остатки откатываются.

### 4.8 Бизнес-правила по типам документов (для UI)
- `TRANSFER`: обязательно `warehouse_from` и `warehouse_to` (и они должны быть разными).
- `TRANSFER`: товар **должен** принадлежать складу-источнику (`warehouse_from`).
- `TRANSFER`: при проведении создаётся/находится товар на складе-получателе и остаток уходит на него.
- `INVENTORY`: `items[].qty` — фактический остаток; при проведении создаётся движение на \(\Delta = fact - current\).
- `SALE`, `WRITE_OFF`, `PURCHASE_RETURN`: уменьшают остаток на `warehouse_from`.
- `PURCHASE`, `RECEIPT`, `SALE_RETURN`: увеличивают остаток на `warehouse_from`.
- для `SALE/PURCHASE/SALE_RETURN/PURCHASE_RETURN` обязательны: `warehouse_from` и `counterparty`.
- нельзя проводить пустой документ.
- для “штучных” товаров количество (`qty`) должно быть целым (сервер проверяет; фронту лучше валидировать заранее).
- если в документе указан `agent`, операции идут по остаткам агента (склад не меняется).

## 5) Денежные документы (приход/расход денег)

### 5.1 Категории платежей
- `GET/POST /api/warehouse/money/categories/`
- `GET/PATCH/PUT/DELETE /api/warehouse/money/categories/{id}/`

Фильтры/поиск:
- фильтры: `company`, `branch` (обычно не нужны фронту)
- поиск: `?search=...` (по `title`)

Ответ:
```json
{ "id": "uuid", "company": "uuid", "branch": "uuid|null", "title": "string" }
```

### 5.2 Денежные документы
- `GET/POST /api/warehouse/money/documents/`
- `GET/PATCH/PUT/DELETE /api/warehouse/money/documents/{id}/`
- `POST /api/warehouse/money/documents/{id}/post/`
- `POST /api/warehouse/money/documents/{id}/unpost/`

Типы:
- `MONEY_RECEIPT` — приход
- `MONEY_EXPENSE` — расход

Статусы:
- `DRAFT` | `POSTED`

Формат (ответ):
```json
{
  "id": "uuid",
  "company": "uuid",
  "branch": "uuid|null",
  "doc_type": "MONEY_RECEIPT|MONEY_EXPENSE",
  "status": "DRAFT|POSTED",
  "number": "MONEY_RECEIPT-20260201-0001|null",
  "date": "2026-02-01T12:00:00Z",
  "warehouse": "uuid",
  "warehouse_name": "string|null",
  "counterparty": "uuid",
  "counterparty_display_name": "string|null",
  "payment_category": "uuid",
  "payment_category_title": "string|null",
  "amount": "1500.00",
  "comment": "string",
  "created_at": "2026-02-01T12:00:00Z",
  "updated_at": "2026-02-01T12:10:00Z"
}
```

Read-only поля:
- `number`, `status`, `date`, `created_at`, `updated_at`

Важные правила:
- после `POSTED` документ нельзя менять, пока не выполните `unpost`.
- денежные документы **не создают** складских движений по товарам и **не меняют** остатки.

Фильтры/поиск:
- фильтры: `doc_type`, `status`, `warehouse`, `counterparty`, `payment_category`
- поиск: `?search=...` (по `number`, `comment`, `counterparty__name`)

### 5.3 Денежные операции по контрагенту
- `GET /api/warehouse/money/counterparties/{counterparty_id}/operations/`

Работает как список money-документов по одному контрагенту + фильтры/поиск:
- фильтры: `doc_type`, `status`, `warehouse`, `payment_category`
- поиск: `?search=...` (по `number`, `comment`)

## 6) Агенты: заявки и остатки

### 6.0 Мобильное приложение (только агент)
Минимальный набор:
- `GET /api/warehouse/` — список доступных складов
- `GET /api/warehouse/{warehouse_uuid}/products/` — товары склада (поиск/фильтры)
- `POST /api/warehouse/agent-carts/` — создать заявку (нужен `warehouse`)
- `POST /api/warehouse/agent-cart-items/` — добавить позиции
- `POST /api/warehouse/agent-carts/{id}/submit/` — отправить владельцу
- `GET /api/warehouse/agent-carts/` — история заявок (только свои)
- `GET /api/warehouse/agents/me/products/` — остатки агента
- `GET /api/warehouse/agent/documents/` — документы агента (если используется продажа/возвраты)

Правила:
- агент видит только свои заявки и документы;
- позиции заявки можно менять только в `draft`;
- `approve/reject` доступны только владельцу/админу.
- товар в заявке должен принадлежать выбранному складу.
- контрагенты агента — только свои, `agent` не передается вручную.

Короткий сценарий:
1) выбрать склад (`GET /api/warehouse/`);
2) выбрать товары этого склада (`GET /api/warehouse/{warehouse_uuid}/products/`);
3) создать заявку с этим складом (`POST /api/warehouse/agent-carts/`);
4) добавить позиции (товары только из выбранного склада);
5) отправить заявку владельцу (`submit`).

### 6.1 Заявки агента на товар
Эндпойнты:
- `GET /api/warehouse/agent-carts/` — список заявок (агент видит только свои)
- `POST /api/warehouse/agent-carts/` — создать заявку (agent проставится автоматически)
- `GET/PATCH/PUT/DELETE /api/warehouse/agent-carts/{id}/`
- `POST /api/warehouse/agent-carts/{id}/submit/` — отправить владельцу
- `POST /api/warehouse/agent-carts/{id}/approve/` — одобрить (только владелец/админ)
- `POST /api/warehouse/agent-carts/{id}/reject/` — отклонить (только владелец/админ)

Формат заявки:
```json
{
  "id": "uuid",
  "agent": "uuid",
  "warehouse": "uuid",
  "status": "draft|submitted|approved|rejected",
  "note": "string|null",
  "submitted_at": "2026-02-01T12:00:00Z|null",
  "approved_at": "2026-02-01T12:00:00Z|null",
  "approved_by": "uuid|null",
  "created_date": "2026-02-01T12:00:00Z",
  "updated_date": "2026-02-01T12:10:00Z",
  "items": [ ... ]
}
```

Примечание:
- `agent` не передается: сервер проставляет текущего пользователя.
- `company` и `branch` не передаются: сервер берёт их из выбранного `warehouse`.

### 6.2 Позиции заявки
Эндпойнты:
- `GET /api/warehouse/agent-cart-items/?cart={uuid}`
- `POST /api/warehouse/agent-cart-items/`
- `GET/PATCH/PUT/DELETE /api/warehouse/agent-cart-items/{id}/`

Формат позиции:
```json
{
  "id": "uuid",
  "cart": "uuid",
  "product": "uuid",
  "quantity_requested": "10.000",
  "created_date": "2026-02-01T12:00:00Z",
  "updated_date": "2026-02-01T12:10:00Z"
}
```

Важные правила:
- позиции можно менять **только** пока заявка `draft`.
- при `approve` со склада списывается товар, у агента увеличиваются остатки.
- товар в позиции **должен** принадлежать складу, выбранному в заявке.

### 6.3 Остатки у агента
Эндпойнты:
- `GET /api/warehouse/agents/me/products/`
- `GET /api/warehouse/owner/agents/products/` (только владелец/админ)

Ответ:
```json
{
  "id": "uuid",
  "agent": "uuid",
  "warehouse": "uuid",
  "product": "uuid",
  "product_name": "string",
  "product_article": "string|null",
  "product_unit": "string",
  "qty": "5.000"
}
```

Примечание:
- для `agents/me/products` агент определяется токеном, вручную не передается.

## 7) Акт сверки с контрагентом (как 1С)

Эндпойнты:
- `GET /api/warehouse/counterparties/{counterparty_id}/reconciliation/` → PDF (скачивание)
- `GET /api/warehouse/counterparties/{counterparty_id}/reconciliation/json/` → JSON

Параметры:
- `start` (обязательный) — `YYYY-MM-DD` или ISO datetime
- `end` (обязательный) — `YYYY-MM-DD` или ISO datetime
- `currency` (опционально, по умолчанию `KGS`)
- `branch` (опционально, если нет фиксированного филиала у пользователя)

Логика расчёта:
- учитываются **только проведённые** документы и операции.
- складские документы:
  - дебет: `SALE`, `PURCHASE_RETURN`
  - кредит: `PURCHASE`, `SALE_RETURN`
- денежные документы:
  - дебет: `MONEY_EXPENSE`
  - кредит: `MONEY_RECEIPT`
- смысл по долгам:
  - `MONEY_RECEIPT` (приход от контрагента) **уменьшает** его долг перед нами.
  - `MONEY_EXPENSE` (расход) — это наша оплата контрагенту, **уменьшает** наш долг перед ним.
- фильтрация по компании и активному филиалу (если филиал не указан и не фиксирован — берутся только глобальные записи).

Ответ JSON:
```json
{
  "company": {
    "id": "uuid",
    "name": "string",
    "inn": "string",
    "okpo": "string",
    "score": "string",
    "bik": "string",
    "address": "string",
    "phone": "string",
    "email": "string"
  },
  "counterparty": {
    "id": "uuid",
    "name": "string",
    "type": "CLIENT|SUPPLIER|BOTH"
  },
  "period": {
    "start": "2026-02-01",
    "end": "2026-02-29",
    "currency": "KGS"
  },
  "opening_balance": "0.00",
  "entries": [
    {
      "date": "2026-02-05T12:30:00+06:00",
      "title": "Продажа SALE-20260205-0003",
      "a_debit": "1500.00",
      "a_credit": "0.00",
      "b_debit": "0.00",
      "b_credit": "1500.00",
      "ref_type": "document:SALE",
      "ref_id": "uuid",
      "running_balance_after": "1500.00"
    }
  ],
  "totals": {
    "a_debit": "1500.00",
    "a_credit": "0.00",
    "b_debit": "0.00",
    "b_credit": "1500.00"
  },
  "closing_balance": "1500.00",
  "as_of_date": "2026-03-01",
  "debt": {
    "debtor": "Контрагент",
    "creditor": "Компания",
    "amount": "1500.00",
    "currency": "KGS"
  }
}
```

Ошибки:
- `400` если `start/end` не заданы или в неверном формате.

## 8) Аналитика склада (для владельца и агента)

### 7.1 Аналитика владельца (общая)
`GET /api/warehouse/owner/analytics/`

Параметры:
- `period=day|week|month|custom`
- `date`, `date_from`, `date_to`

Ответ (основные поля):
```json
{
  "period": "month",
  "date_from": "2026-02-01",
  "date_to": "2026-02-29",
  "summary": {
    "requests_approved": 10,
    "items_approved": "120.000",
    "sales_count": 45,
    "sales_amount": "15000.00",
    "on_hand_qty": "80.000",
    "on_hand_amount": "6400.00"
  },
  "charts": {
    "sales_by_date": [ ... ]
  },
  "top_agents": {
    "by_sales": [ ... ],
    "by_received": [ ... ]
  },
  "details": {
    "warehouses": [
      {
        "warehouse_id": "uuid",
        "warehouse_name": "string",
        "carts_approved": 3,
        "items_approved": "30.000",
        "sales_count": 12,
        "sales_amount": "3400.00",
        "on_hand_qty": "15.000",
        "on_hand_amount": "1200.00"
      }
    ],
    "sales_by_product": [
      {
        "product_id": "uuid",
        "product_name": "string",
        "qty": "10.000",
        "amount": "1500.00"
      }
    ]
  }
}
```

### 7.2 Аналитика агента (по себе)
`GET /api/warehouse/agents/me/analytics/`

### 7.3 Аналитика по конкретному агенту
`GET /api/warehouse/owner/agents/{agent_id}/analytics/`

Ответы у агента:
- заявки (submitted/approved/rejected)
- выданные товары
- продажи/возвраты/списания
- остатки на руках
- графики по заявкам/продажам

Детально:
- `details.sales_by_product[]` — продажи по товарам
- `details.sales_by_warehouse[]` — продажи по складам

Пример `details` агента:
```json
{
  "details": {
    "sales_by_product": [
      { "product_id": "uuid", "product_name": "string", "qty": "5.000", "amount": "500.00" }
    ],
    "sales_by_warehouse": [
      { "warehouse_id": "uuid", "warehouse_name": "string", "sales_count": 3, "sales_amount": "700.00" }
    ]
  }
}
```

## 9) UI/UX рекомендации (коротко)
- Документы: отдельный экран/модал для редактирования `items[]` (таблица с добавлением/удалением строк).
- Перед `post/unpost`: показывать подтверждение (необратимые бизнес‑эффекты).
- При работе с товарами:
  - для “штучных” — валидировать целое `qty`;
  - для фото — отправлять `multipart/form-data`, показывать `image_url`.
