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

Ошибки:
- **400** при попытке создать/переименовать бренд в дубль в рамках текущего `company/branch`:
```json
{ "name": "Бренд с таким названием уже существует." }
```

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

Ошибки:
- **400** при попытке создать/переименовать категорию в дубль в рамках текущего `company/branch`:
```json
{ "name": "Категория с таким названием уже существует." }
```

### 2.3.1 Группы товаров внутри склада (как в 1С)
Иерархия групп по складу: `GET/POST /api/warehouse/{warehouse_uuid}/groups/`, `GET/PATCH/DELETE .../groups/{group_uuid}/`. Тело: `name`, `parent` (uuid|null). В ответе: `products_count`. У товара поле `product_group`; фильтр `?product_group=<uuid>`.

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
  "category": "uuid|null",
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
- `category`, `country`, `characteristics` можно не передавать при создании; в ответе они могут быть `null`.

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
  "category": "uuid|null"
}
```

Поведение:
- если товар с таким `barcode` найден в этом складе → вернёт его (`created=false`);
- если `barcode` похож на весовой EAN-13 → пытается найти товар по `plu`, вернёт `scan_qty`;
- если товар не найден → создаст (нужен `name`, остальные поля как в обычном POST).

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
- `status`: `DRAFT` | `CASH_PENDING` | `POSTED` | `REJECTED`

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
- `POST /api/warehouse/documents/{id}/cash/approve/`
- `POST /api/warehouse/documents/{id}/cash/reject/`

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
  "status": "DRAFT|CASH_PENDING|POSTED|REJECTED",
  "number": "SALE-20260201-0001|null",
  "date": "2026-02-01T12:00:00Z",
  "payment_kind": "cash|credit|null",
  "warehouse_from": "uuid|null",
  "warehouse_to": "uuid|null",
  "warehouse_from_name": "string|null",
  "warehouse_to_name": "string|null",
  "counterparty": "uuid|null",
  "cash_register": "uuid|null",
  "cash_register_name": "string|null",
  "payment_category": "uuid|null",
  "payment_category_title": "string|null",
  "cash_request_status": "PENDING|APPROVED|REJECTED|null",
  "agent": "uuid|null",
  "counterparty_display_name": "string|null",
  "comment": "string",
  "discount_percent": "0.00",
  "discount_amount": "0.00",
  "total": "0.00",
  "items": [...],
  "moves": [
    {
      "id": "uuid",
      "document": "uuid",
      "warehouse": "uuid",
      "warehouse_name": "string",
      "product": "uuid",
      "product_name": "string",
      "product_article": "string|null",
      "qty_delta": "1.000|-1.000",
      "move_kind": "RECEIPT|EXPENSE",
      "created_at": "2026-02-01T12:00:00Z"
    }
  ],
  "receipts": [...],
  "expenses": [...]
}
```

**Приходы и расходы:**
- `moves` — все движения товара по документу (создаются при проведении).
- `move_kind`: `RECEIPT` — приход (увеличение остатка), `EXPENSE` — расход (уменьшение остатка).
- `receipts` — подмножество moves с `move_kind=RECEIPT`.
- `expenses` — подмножество moves с `move_kind=EXPENSE`.
- Документ TRANSFER содержит и приходы (на склад-приёмник), и расходы (со склада-источника). SALE — только расходы, PURCHASE/RECEIPT — только приходы.

Read-only поля:
- `number`, `total`, `status`, `date`

**Оплата (продажа/покупка в долг):**
- `payment_kind` — только для типов `SALE`, `PURCHASE`, `SALE_RETURN`, `PURCHASE_RETURN`.
  - `cash` — оплата сразу (по умолчанию).
  - `credit` — **в долг**: при продаже клиент должен нам (задолженность погашается приходом денег от контрагента); при покупке мы должны поставщику (погашается расходом денег контрагенту). Имеет смысл проводить документ как обычно, а долг учитывается в акте сверки с контрагентом.
- Для остальных типов документов поле можно не передавать (или `null`).

**Касса (важно):**
- Любой складской документ после `post` сначала попадает в статус `CASH_PENDING` (ожидает решения по кассе).
- На этом этапе склад уже проведён (созданы `moves`, остатки изменены), но финальное решение принимает касса:
  - `POST /documents/{id}/cash/approve/` — подтвердить.
  - `POST /documents/{id}/cash/reject/` — отклонить.
- Если отклонить (`cash/reject`):
  - складские движения откатываются,
  - документ получает статус `REJECTED`,
  - в кассу ничего не попадает.
- Если подтвердить (`cash/approve`) и `payment_kind="cash"`:
  - создаётся и проводится денежный документ:
    - `SALE` → `MONEY_RECEIPT`
    - `PURCHASE` → `MONEY_EXPENSE`
    - `SALE_RETURN` → `MONEY_EXPENSE`
    - `PURCHASE_RETURN` → `MONEY_RECEIPT`
    - `RECEIPT` → `MONEY_RECEIPT`
    - `WRITE_OFF` → `MONEY_EXPENSE`
  - документ получает статус `POSTED`.
- Для `cash/approve` с денежным движением нужны `cash_register` и `payment_category`:
  - если в текущем company/branch ровно одна касса/категория — сервер подставит автоматически;
  - если несколько — укажите явно в документе (`PATCH /documents/{id}/`) до `cash/approve`.

### 4.4 Создание документа (пример)
`POST /api/warehouse/documents/`
```json
{
  "doc_type": "SALE",
  "payment_kind": "credit",
  "warehouse_from": "11111111-1111-1111-1111-111111111111",
  "counterparty": "22222222-2222-2222-2222-222222222222",
  "comment": "Продажа в долг",
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
- фильтры: `doc_type`, `status`, `payment_kind` (`cash`|`credit`), `warehouse_from`, `warehouse_to`, `counterparty`
- поиск: `?search=...` (по `number` и `comment`)

### 4.7 Проведение и отмена
Проведение:
- `POST /api/warehouse/documents/{id}/post/`
- тело **может** содержать `allow_negative` (boolean или строка `"true"|"1"|"yes"`) для обхода проверки отрицательных остатков:
```json
{ "allow_negative": true }
```
- при успехе: 200 + сериализованный документ (status станет `CASH_PENDING`, `number` будет сгенерирован).

Отмена:
- `POST /api/warehouse/documents/{id}/unpost/`
- при успехе: документ возвращается в `DRAFT`, движения удаляются, остатки откатываются.

Подтверждение/отклонение кассой:
- `POST /api/warehouse/documents/{id}/cash/approve/`
  - опционально `{"note": "комментарий"}`
  - подтверждает кассовый запрос; при необходимости создаёт `MONEY_RECEIPT/MONEY_EXPENSE`; статус документа -> `POSTED`.
- `POST /api/warehouse/documents/{id}/cash/reject/`
  - опционально `{"note": "причина отказа"}`
  - отклоняет кассовый запрос; склад откатывается; статус документа -> `REJECTED`.

Inbox для кассира (работа с запросами кассы):
- `GET /api/warehouse/cash/requests/`
  - фильтры: `status`, `requires_money`, `money_doc_type`, `document__doc_type`, `document__payment_kind`
  - поиск: `?search=...` (по `document.number`, `document.comment`, `document.counterparty.name`)
- `POST /api/warehouse/cash/requests/{request_id}/approve/`
- `POST /api/warehouse/cash/requests/{request_id}/reject/`

Формат элемента в `GET /cash/requests/`:
```json
{
  "id": "request-uuid",
  "status": "PENDING|APPROVED|REJECTED",
  "requires_money": true,
  "money_doc_type": "MONEY_RECEIPT|MONEY_EXPENSE|null",
  "amount": "1500.00",
  "decision_note": "",
  "requested_at": "2026-02-17T18:00:00Z",
  "decided_at": null,
  "decided_by_id": null,
  "money_document_id": null,
  "document": {
    "id": "doc-uuid",
    "number": "SALE-20260217-0001",
    "doc_type": "SALE",
    "status": "CASH_PENDING",
    "payment_kind": "cash",
    "date": "2026-02-17T17:59:00Z",
    "total": "1500.00",
    "warehouse_from": "uuid",
    "warehouse_from_name": "Основной склад",
    "counterparty": "uuid",
    "counterparty_display_name": "Клиент",
    "cash_register": "uuid|null",
    "payment_category": "uuid|null"
  }
}
```

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

## 5) Касса и денежные документы (приход/расход)

### 5.0 Касса (cash register)
В кассу попадают приходы и расходы денег.

- `GET/POST /api/warehouse/cash-registers/` — список касс / создать кассу
- `GET/PATCH/PUT/DELETE /api/warehouse/cash-registers/{id}/` — детали кассы
- `GET /api/warehouse/cash-registers/{id}/operations/` — касса с балансом, приходами и расходами

Формат кассы:
```json
{ "id": "uuid", "company": "uuid", "branch": "uuid|null", "name": "string", "location": "string" }
```

Read-only поля:
- `company`, `branch` — сервер проставляет автоматически.

Создание кассы (`POST /api/warehouse/cash-registers/`):
- тело:
```json
{ "name": "Основная касса", "location": "офис" }
```
- сервер сам проставит `company` и (если выбран активный филиал) `branch`.

Фильтры/поиск (`GET /api/warehouse/cash-registers/`):
- фильтры: `company`, `branch` (обычно фронту не нужны — доступ и так ограничен текущей компанией/филиалом)
- поиск: `?search=...` (по `name`, `location`)

Ответ `operations/`:
```json
{
  "id": "uuid",
  "name": "Основная касса",
  "company": "uuid",
  "branch": "uuid|null",
  "location": "",
  "balance": "1500.00",
  "receipts_total": "5000.00",
  "expenses_total": "3500.00",
  "receipts": [ /* массив денежных документов MONEY_RECEIPT */ ],
  "expenses": [ /* массив денежных документов MONEY_EXPENSE */ ]
}
```

- `balance` = приходы − расходы **только по проведённым** (`status=POSTED`) денежным документам этой кассы
- `receipts` — приходы (MONEY_RECEIPT)
- `expenses` — расходы (MONEY_EXPENSE)

Важно:
- баланс **не хранится** отдельным полем — вычисляется из проведённых документов;
- в `operations/` попадают **только** документы с `cash_register = эта касса` (документы с заполненным `warehouse` без `cash_register` сюда не попадают).

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

Read-only поля:
- `company`, `branch` — сервер проставляет автоматически.

Ошибки:
- **400** при попытке создать/переименовать категорию платежа в дубль в рамках текущего `company/branch`:
```json
{ "title": "Категория платежа с таким названием уже существует." }
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
  "cash_register": "uuid",
  "cash_register_name": "string|null",
  "warehouse": "uuid|null",
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

Дополнительные правила (как сейчас работает сервер):
- `number` генерируется при проведении (формат: `TYPE-YYYYMMDD-0001`), до проведения может быть `null`.
- обязательные поля для `MONEY_RECEIPT` и `MONEY_EXPENSE`:
  - `cash_register` (рекомендуется всегда; поле `warehouse` — устаревшее/legacy)
  - `counterparty`
  - `payment_category`
  - `amount` \(> 0\)

Ошибки (типичные 400):
- если не указана касса и не указан `warehouse` (legacy):
```json
{ "cash_register": "Укажите кассу." }
```
- если не указан контрагент/категория платежа:
```json
{ "counterparty": "Укажите контрагента." }
```
```json
{ "payment_category": "Укажите категорию платежа." }
```
- если `amount <= 0`:
```json
{ "amount": "Сумма должна быть больше 0." }
```
- попытка изменить проведённый документ:
```json
{ "status": "Нельзя изменять проведенный документ. Сначала отмените проведение." }
```

Базовое создание в черновик (`POST /api/warehouse/money/documents/`):
```json
{
  "doc_type": "MONEY_RECEIPT",
  "cash_register": "uuid",
  "counterparty": "uuid",
  "payment_category": "uuid",
  "amount": "1500.00",
  "comment": "оплата"
}
```

Проведение / отмена:
- `POST /api/warehouse/money/documents/{id}/post/`:
  - переводит документ в `POSTED`
  - если `number` пустой — генерирует номер
- `POST /api/warehouse/money/documents/{id}/unpost/`:
  - возвращает документ в `DRAFT` (номер при этом остаётся как был)

Авто-проведение при create/update (backward compatible):
- можно сразу провести документ при создании/обновлении, если:
  - отправить `{"post": true}` или `{"status": "POSTED"}` в body, **или**
  - передать `?post=1` в querystring

Фильтры/поиск:
- фильтры: `doc_type`, `status`, `cash_register`, `warehouse`, `counterparty`, `payment_category`
- поиск: `?search=...` (по `number`, `comment`, `counterparty__name`)

**Важно:** при создании денежного документа укажите `cash_register` — кассу, в которую попадает приход или расход.

### 5.3 Денежные операции по контрагенту
- `GET /api/warehouse/money/counterparties/{counterparty_id}/operations/`

Работает как список money-документов по одному контрагенту + фильтры/поиск:
- фильтры: `doc_type`, `status`, `cash_register`, `warehouse`, `payment_category`
- поиск: `?search=...` (по `number`, `comment`)

## 6) Агенты: заявки и остатки

> **Подробная документация по системе агентов склада** (самостоятельная регистрация, заявки в компании, приём/отклонение/отстранение, доступ к операциям): см. **[WAREHOUSE_AGENTS.md](./WAREHOUSE_AGENTS.md)**.

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
