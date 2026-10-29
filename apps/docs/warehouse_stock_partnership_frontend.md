# Партнёрство компаний (склад + касса / инкассация) — API для фронтенда

Две **разные** компании связываются **одной заявкой** на партнёрство. После **принятия** одновременно открывается:

- **склад** — каталог складов и товаров партнёра, межкомпанейное перемещение `TRANSFER`;
- **касса** — каталог касс партнёра с сальдо, **инкассация** (перевод наличных с кассы одной компании на кассу другой).

Отдельной заявки «только на кассу» нет: привязка склада и кассы идёт вместе.

**Базовые правила модуля склада** (аутентификация, префикс `/api/warehouse/`, филиал `?branch=`): см. `apps/warehouse/FRONTEND_API.md`, раздел 1.

---

## Внутренняя vs межкомпанейская логика

**Одна компания** (склады одной компании, учёт филиала как раньше):

- `POST /api/warehouse/transfer/` — быстрое перемещение.

**Разные компании** (активное партнёрство между ними обязательно):

- `POST /api/warehouse/stock-partnerships/transfer/` — товар, см. «Межкомпанейское перемещение».
- `POST /api/warehouse/stock-partnerships/cash-incassations/` — деньги, см. «Инкассация (касса)».

---

## Ограничения по ролям

- **Агенты** склада (без контекста владельца как сотрудника) **не** могут вызывать межкомпанейское перемещение (**403**).
- **Принять / отклонить** входящую заявку — только **владелец / admin** (или staff/superuser по правилам `_is_owner_like`) компании **получателя** заявки.
- **Отправить заявку**, **списки заявок**, **каталог партнёра**, **список партнёров** — пользователь должен представлять текущую компанию (владелец или сотрудник с `user.company` = этой компании).
- Межкомпанейское перемещение товара: компания пользователя должна совпадать с **одной** из компаний складов (`warehouse_from` или `warehouse_to`).
- Инкассация: компания пользователя должна совпадать с **одной** из компаний касс (`cash_register_from` или `cash_register_to`).

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

**Аналитика по партнёрам (только владелец):** см. [warehouse_partner_analytics_frontend.md](./warehouse_partner_analytics_frontend.md) — `GET /api/warehouse/owner/partners/analytics/` и `GET /api/warehouse/owner/partners/{id}/analytics/`.

---

## Каталог партнёра (склады, товары, кассы)

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
  ],
  "cash_registers": [
    {
      "id": "uuid",
      "name": "string",
      "location": "string",
      "branch_id": "uuid|null",
      "branch_name": "string|null",
      "balance": "строка decimal, сальдо по проведённым приходам/расходам"
    }
  ]
}
```

- `products[].qty` — остаток: сначала `StockBalance`, иначе `WarehouseProduct.quantity`.
- `cash_registers[].balance` — приходы минус расходы по **проведённым** `MoneyDocument` этой кассы.

**403**, если партнёрства нет:

```json
{ "detail": "Нет активного партнёрства с этой компанией." }
```

---

## Инкассация (касса)

Перевод наличных между кассами **разных** компаний-партнёров. На бэкенде создаются и сразу **проводятся** два денежных документа:

- **расход** (`MONEY_EXPENSE`) с кассы-источника, категория «Инкассация»;
- **приход** (`MONEY_RECEIPT`) на кассу-приёмник, категория «Инкассация».

Проверяется сальдо кассы-источника (нельзя перевести больше доступного).

### Создать инкассацию

`POST /api/warehouse/stock-partnerships/cash-incassations/`

Тело:

```json
{
  "cash_register_from": "uuid кассы, с которой списываем",
  "cash_register_to": "uuid кассы партнёра, на которую зачисляем",
  "amount": "1000.00",
  "comment": "необязательно"
}
```

Правила:

- кассы **разных** компаний;
- между компаниями — **активное партнёрство** (та же заявка, что для склада);
- **ваша** компания = `cash_register_from.company` **или** `cash_register_to.company`;
- `amount` > 0, на кассе-источнике достаточно сальдо.

Ответ **201** — объект инкассации:

```json
{
  "id": "uuid",
  "from_company": "uuid",
  "from_company_name": "string",
  "to_company": "uuid",
  "to_company_name": "string",
  "cash_register_from": "uuid",
  "cash_register_from_name": "string",
  "cash_register_to": "uuid",
  "cash_register_to_name": "string",
  "expense_document": "uuid",
  "expense_document_number": "MONEY_EXPENSE-20260515-0001",
  "receipt_document": "uuid",
  "receipt_document_number": "MONEY_RECEIPT-20260515-0001",
  "amount": "1000.00",
  "comment": "string",
  "created_by": "uuid|null",
  "created_by_email": "string|null",
  "created_at": "ISO-8601"
}
```

Ошибки **400** (примеры):

- `{"cash_register": "Одна из касс должна принадлежать вашей компании."}`
- `{"detail": "Недостаточно средств в кассе «…». Доступно: …, требуется: …"}`
- `{"detail": "Между компаниями этих касс нет принятого партнёрства."}`

### История инкассаций

`GET /api/warehouse/stock-partnerships/cash-incassations/`

Ответ:

```json
{
  "results": [ "... объекты как после POST ..." ]
}
```

До **200** последних операций, где ваша компания — отправитель или получатель.

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
