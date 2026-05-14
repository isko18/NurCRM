# Партнёрство компаний по складу — API для фронтенда

Две **разные** компании могут связать склады «партнёрством» (аналог филиала между юрлицами). После **принятия** заявки сторона получает доступ к **каталогу** партнёра (склады и товары с остатками) и может выполнить **межкомпанейское перемещение** в одном документе `TRANSFER` (списание со склада одной компании, приход на склад другой).

**Базовые правила модуля склада** (аутентификация, префикс `/api/warehouse/`, филиал `?branch=`): см. `apps/warehouse/FRONTEND_API.md`, раздел 1.

---

## Внутренняя vs межкомпанейская логика

**Одна компания** (склады одной компании, учёт филиала как раньше):

- `POST /api/warehouse/transfer/` — быстрое перемещение.

**Разные компании** (активное партнёрство между ними обязательно):

- `POST /api/warehouse/stock-partnerships/transfer/` — тело запроса **как** у `transfer/`, см. раздел «Межкомпанейское перемещение».

---

## Ограничения по ролям

- **Агенты** склада (без контекста владельца как сотрудника) **не** могут вызывать межкомпанейское перемещение (**403**).
- **Принять / отклонить** входящую заявку — только **владелец / admin** (или staff/superuser по правилам `_is_owner_like`) компании **получателя** заявки.
- **Отправить заявку**, **списки заявок**, **каталог партнёра**, **список партнёров** — пользователь должен представлять текущую компанию (владелец или сотрудник с `user.company` = этой компании).
- Межкомпанейское перемещение: компания пользователя (`owned_company` / `company` из миксина) должна совпадать с **одной** из компаний складов (`warehouse_from` или `warehouse_to`).

---

## Заявки на партнёрство

### Список заявок

`GET /api/warehouse/stock-partnership-requests/`

Ответ:

```json
{
  "incoming": [
    {
      "id": "uuid",
      "from_company": "uuid",
      "from_company_name": "string",
      "to_company": "uuid",
      "to_company_name": "string",
      "status": "PENDING|ACCEPTED|REJECTED|CANCELLED",
      "note": "string",
      "created_by": "uuid|null",
      "created_by_email": "string|null",
      "decided_by": "uuid|null",
      "decided_by_email": "string|null",
      "created_at": "ISO-8601",
      "decided_at": "ISO-8601|null",
      "updated_at": "ISO-8601"
    }
  ],
  "outgoing": [ "... тот же формат ..." ]
}
```

- `incoming` — заявки **в вашу** компанию со статусом `PENDING`.
- `outgoing` — заявки **из вашей** компании (последние, до 200 записей, любые статусы).

### Создать заявку

`POST /api/warehouse/stock-partnership-requests/`

Тело:

```json
{
  "to_company": "uuid компании-получателя заявки",
  "note": "необязательно, до 512 символов"
}
```

Ошибки **400**:

- `{"to_company": "Нельзя отправить запрос самой себе."}`
- `{"to_company": "Партнёрство с этой компанией уже активно."}`
- `{"to_company": "Уже есть ожидающий запрос к этой компании."}` (одна «ожидающая» заявка на пару направление `from` → `to`)

### Принять заявку

`POST /api/warehouse/stock-partnership-requests/{request_id}/accept/`

Только компания-адресат, **owner/admin** (см. ограничения выше).

### Отклонить заявку

`POST /api/warehouse/stock-partnership-requests/{request_id}/reject/`

### Отозвать исходящую заявку

`POST /api/warehouse/stock-partnership-requests/{request_id}/cancel/`

Отправитель, пока статус `PENDING`.

После **accept** создаётся активное партнёрство (пара компаний). Отдельного API «разорвать партнёрство» в текущей версии нет.

---

## Активные партнёры

`GET /api/warehouse/stock-partnerships/active/`

Ответ:

```json
{
  "partners": [
    { "id": "uuid", "name": "Название компании" }
  ]
}
```

---

## Каталог партнёра (склады + товары + остаток)

Доступно только при **активном** партнёрстве с указанной компанией.

`GET /api/warehouse/stock-partnerships/companies/{company_id}/catalog/`

Ответ:

```json
{
  "partner_company": { "id": "uuid", "name": "string" },
  "warehouses": [
    {
      "id": "uuid",
      "name": "string",
      "branch_id": "uuid|null",
      "branch_name": "string|null",
      "products": [
        {
          "id": "uuid",
          "name": "string",
          "article": "string",
          "barcode": "string",
          "unit": "string",
          "qty": "строка decimal, например \"12.000\""
        }
      ]
    }
  ]
}
```

- `qty` — остаток: сначала `StockBalance`, иначе логика как у склада для `WarehouseProduct.quantity`.

**403**, если партнёрства нет:

```json
{ "detail": "Нет активного партнёрства с этой компанией." }
```

---

## Межкомпанейское перемещение

`POST /api/warehouse/stock-partnerships/transfer/`

Тело — **как** у `POST /api/warehouse/transfer/`:

```json
{
  "warehouse_from": "uuid",
  "warehouse_to": "uuid",
  "comment": "необязательно",
  "items": [
    {
      "product": "uuid товара со склада-источника",
      "qty": "1.000",
      "price": "0.00",
      "discount_percent": "0.00",
      "discount_amount": "0.00"
    }
  ]
}
```

Правила:

- `warehouse_from.company_id !== warehouse_to.company_id` (иначе **400**: использовать `/api/warehouse/transfer/`).
- Между компаниями складов должно быть **активное партнёрство**.
- Все `items[].product` должны принадлежать **`warehouse_from`** (как у обычного `TRANSFER`).
- Ответ — документ в том же формате, что после обычного `transfer/` (документ создаётся и **сразу проводится**).

Примеры ошибок **400**:

- `{"warehouse": "Для перемещения внутри компании используйте POST /api/warehouse/transfer/."}`
- `{"warehouse": "Один из складов должен принадлежать вашей компании."}`
- `{"warehouse": "Между компаниями этих складов нет принятого партнёрства."}`
- `{"detail": "..."}` — ошибка проведения (остатки, валидация строк и т.д.).

---

## Подсказка UI: поиск компании для поля `to_company`

`GET /api/warehouse/agents/companies/search/?search=...`

В ответе элементы с полями `id`, `name`, `slug` (можно подставить `id` в `to_company` при создании заявки).
