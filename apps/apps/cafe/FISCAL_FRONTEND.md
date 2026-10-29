# Фискальная касса в кафе — гайд для фронта

Как фронт работает с двумя слоями: **Fiscal Connector** (`localhost:8080`, юр. фискализация) и **Nur backend** (`/cafe/fiscal/...`, хранение реквизитов и результатов).

Правило простое:
- **С коннектором** (verify-pin / auth / open-shift / receipt / close-shift) общается **только фронт**.
- **Бэк** хранит настройки кассы, налоговые коды блюд и записывает результат каждой фискализации (ФД/ФМ), чтобы работали возвраты, отчёты и сверка.

Все запросы к бэку — с обычным `Authorization: Bearer <jwt>` пользователя Nur. Филиал — как везде в кафе: через `?branch=<uuid>` (если у пользователя не зафиксирован).

---

## 0. TL;DR поток дня

```
УТРО (нужен интернет)
  GET  /cafe/fiscal/settings/                  → реквизиты (РНМ, ПИН, логин, URL коннектора, enabled)
  ├ если enabled=false → работаем как раньше (ESC/POS), фискал не трогаем
  POST localhost:8080/driver/verify-pin        (фронт → коннектор)
  POST localhost:8080/driver/auth              (фронт → коннектор, токен 5 мин)
  GET  localhost:8080/driver/state-shift       (фронт → коннектор)
  POST localhost:8080/driver/open-shift        (фронт → коннектор, PDF)
  POST /cafe/fiscal/shift/open/                → зафиксировать смену в Nur

ДЕНЬ — оплата заказа
  GET  /cafe/fiscal/orders/{id}/receipt-payload/?...   → готовое тело чека
  POST localhost:8080/driver/cash-register/receipt     (фронт → коннектор, PDF)
  POST /cafe/fiscal/orders/{id}/receipt/               → записать ФД/ФМ в Nur
  POST /cafe/orders/{id}/pay/                           → учёт заказа (как сейчас)

КАССА
  GET/POST localhost:8080/driver/cash-transaction[/deposit|/withdraw]   (фронт → коннектор)
  POST /cafe/fiscal/cash/deposit/  |  /cafe/fiscal/cash/withdraw/        → лог в Nur

ВЕЧЕР
  POST localhost:8080/driver/close-shift       (фронт → коннектор, Z-отчёт)
  POST /cafe/fiscal/shift/close/               → закрыть смену в Nur
```

---

## 1. Настройки кассы

### `GET /cafe/fiscal/settings/`
Возвращает реквизиты компании. Если записи нет — создаётся пустая.

```json
{
  "enabled": false,
  "connector_base_url": "http://localhost:8080",
  "registration_number": "",
  "pin": "",
  "login": "",
  "password": "",
  "tin": "",
  "full_name": "",
  "cashier_name": "",
  "fiscal_memory_number": "",
  "location_address": "",
  "tax_system_codes": [],
  "calc_item_attr_codes": [],
  "entrepreneurship_object_code": null,
  "business_activity_code": null,
  "tax_authority_department_code": null,
  "default_vat_code": 0,
  "default_st_code": 0,
  "default_calc_item_attr_code": 1,
  "default_measure": "шт",
  "receipt_width": 384,
  "updated_at": null
}
```

- `enabled` — «использовать фискальную кассу». **Если `false` — фронт не трогает коннектор, работает старый ESC/POS.**
- `connector_base_url` — куда фронт шлёт запросы коннектора.
- `registration_number` (РНМ), `pin`, `login`, `password` — фронт берёт их для `verify-pin` / `auth`.
- `default_*` — фолбэк налоговых кодов, если у блюда они не заданы.

### `PATCH /cafe/fiscal/settings/`
Частичное обновление (экран «Настройки → Печать»). Шлите только меняемые поля:

```json
{
  "enabled": true,
  "connector_base_url": "http://localhost:8080",
  "registration_number": "0123456789012345",
  "pin": "12345",
  "login": "cafe@example.com",
  "password": "secret",
  "default_vat_code": 12,
  "default_st_code": 0,
  "default_measure": "шт"
}
```

**Кэш из `auth`:** после успешного `POST /driver/auth` коннектор вернёт `tin`, `fullName`, `cashierName`, `fiscalMemoryNumber`, `taxSystemCodes`, `calcItemAttrCodes` и др. Рекомендуется сохранить их обратно `PATCH`-ом (`tin`, `full_name`, `cashier_name`, `fiscal_memory_number`, `tax_system_codes`, `calc_item_attr_codes`, …) — чтобы UI и отчёты их видели.

---

## 2. Смена

### `GET /cafe/fiscal/shift/state/`
Состояние смены **по данным Nur** (для UI/сверки; источник правды — коннектор `GET /driver/state-shift`).

```json
{ "shift_opened": true, "shift": { "id": "...", "status": "open", "opened_at": "...", "open_shift_datetime": "...", "fm_expiration_date": "..." } }
```

### `POST /cafe/fiscal/shift/open/`
Вызывать **после** успешного `POST /driver/open-shift` на коннекторе. Тело необязательное — передайте что вернул коннектор:

```json
{
  "registration_number": "0123456789012345",
  "open_shift_datetime": "2026-06-14T08:05:00Z",
  "fm_expiration_date": "2027-01-01T00:00:00Z",
  "raw": { "...": "ответ коннектора как есть" }
}
```
- `201` — смена создана.
- `409` `{"detail": "Смена уже открыта.", "shift": {...}}` — открытая смена уже есть.

### `POST /cafe/fiscal/shift/close/`
После `POST /driver/close-shift`. Тело: `{ "raw": { ... } }` (необязательно).
- `200` — закрыта; `409` — открытой смены нет.

### `GET /cafe/fiscal/shifts/`
Журнал смен (до 200), фильтр по филиалу через `?branch=`.

---

## 3. Чек продажи (главный сценарий)

### Шаг 1 — получить тело чека: `GET /cafe/fiscal/orders/{id}/receipt-payload/`

Query-параметры (все опциональны):

| параметр | смысл | дефолт |
|----------|-------|--------|
| `operation_type` | `INCOME` / `INCOME_RETURN` / `EXPENDITURE` / `EXPENDITURE_RETURN` | `INCOME` |
| `cash_received` | принятые наличные (для расчёта сдачи `deliverySum`) | = сумме чека |
| `charge_amount` | фискализируемая сумма (долг/частичная оплата → только факт) | итог − скидка |
| `origin_fd_number` | ФД чека-основания (для возврата) | — |
| `origin_fn_serial_number` | ФМ чека-основания (для возврата) | — |

Ответ:

```json
{
  "connector_base_url": "http://localhost:8080",
  "receipt_width": 384,
  "path": "/driver/cash-register/receipt",
  "method": "POST",
  "body": {
    "operationType": "INCOME",
    "paySum": 500.0,
    "deliverySum": 0.0,
    "totalSum": 500.0,
    "totalCashSum": 500.0,
    "totalCashlessSum": 0.0,
    "positions": [
      {
        "calcItemAttributeCode": 1,
        "sgtin": null,
        "name": "Капучино",
        "price": 250.0,
        "quantity": 2.0,
        "cost": 500.0,
        "measure": "шт",
        "vat": 12,
        "st": 0
      }
    ]
  }
}
```

Бэк уже посчитал: позиции, налоговые коды (с блюда или дефолт компании), скидку, округление, разнесение нал/безнал по способу оплаты. **Фронту достаточно отправить `body` на коннектор.**

> Можно строить `body` и на фронте — но тогда математику скидки/округления/split придётся повторять. Рекомендуется брать готовый payload с бэка.

### Шаг 2 — пробить на коннекторе
```
POST {connector_base_url}{path}
Headers: Authorization: <accessToken>, WIDTH-RECEIPT: {receipt_width}, Response-Type: PDF|JSON
Body: body
```
Ответ — PDF (печатаем) или JSON с `fdNumber` / `fnSerialNumber`.

### Шаг 3 — записать результат: `POST /cafe/fiscal/orders/{id}/receipt/`

```json
{
  "kind": "sale",
  "operation_type": "INCOME",
  "fd_number": 1234567,
  "fn_serial_number": "FN0001",
  "request_payload": { "...": "то, что отправили" },
  "response_payload": { "...": "ответ коннектора" }
}
```
- `kind`: `sale` или `return`.
- `201` — документ записан и привязан к заказу. **Это даёт возможность возврата и сверки.**

### Шаг 4 — учёт в Nur (как сейчас)
`POST /cafe/orders/{id}/pay/` — без изменений.

> Порядок шагов 3 и 4 не критичен, но фискальный чек должен пробиваться на коннекторе **до** того, как фронт сообщит кассиру об успехе.

---

## 4. Маппинг способов оплаты → суммы чека

Бэк делает это сам в `receipt-payload`, но для понимания:

| Кафе | Поля чека |
|------|-----------|
| `cash` | `totalCashSum = totalSum` |
| `card` / `transfer` | `totalCashlessSum = totalSum` |
| `split` | `totalCashSum`/`totalCashlessSum` из частей `checkout_payments` |
| долг / частичная | передайте `charge_amount` = фактически оплачено |
| возврат | `operation_type=INCOME_RETURN` + `origin_fd_number` + `origin_fn_serial_number` |

Для возврата `origin_*` берите из последнего чека продажи заказа: `GET /cafe/fiscal/receipts/?order={id}&kind=sale`.

---

## 5. Касса — внесение / изъятие

Сначала операция на коннекторе (`POST /driver/cash-transaction/deposit|withdraw`), потом лог в Nur:

### `POST /cafe/fiscal/cash/deposit/` и `POST /cafe/fiscal/cash/withdraw/`
```json
{ "amount": 1000.0, "fd_number": 222, "fn_serial_number": "FN0001", "response_payload": { } }
```
`201` — запись создана. Баланс наличных и X-отчёт берутся напрямую с коннектора (`GET /driver/cash-transaction`, `GET /driver/x-report`) — бэк их не дублирует.

---

## 6. Журнал документов

### `GET /cafe/fiscal/receipts/?kind=sale&order={id}`
До 300 последних фискальных документов. Фильтры: `kind` (`sale|return|deposit|withdraw|open_shift|close_shift|x_report`), `order`, `?branch=`.

---

## 7. Налоговые коды блюд

Поля на `MenuItem` (редактируются в карточке блюда, эндпоинты `/cafe/menu-items/`):

| поле | смысл |
|------|-------|
| `fiscal_vat_code` | код НДС (напр. 12 для VAT_12, 0 для VAT_0) |
| `fiscal_st_code` | код НСП (ST_0…ST_5) |
| `fiscal_calc_item_attr_code` | признак предмета расчёта |
| `fiscal_measure` | единица (шт, порция…) |
| `fiscal_sgtin` | ТНВЭД / маркировка |

`null`/пусто → бэк подставит `default_*` из `CafeFiscalSettings`. Перед первым чеком сверьте коды через `GET /driver/cash-register/available-tax-rates` и проставьте их у блюд.

---

## 8. Ошибки коннектора (показывать кассиру)

| код | сообщение |
|-----|-----------|
| `40416` | SAM-карта не выбрана в Fiscal Connector |
| `40417` | SAM-карта не верифицирована — введите PIN (повторить `verify-pin`) |
| `4008` | Неверный PIN |
| `4005` | Неверный логин или пароль |
| `4011` | Требуется повторная авторизация (`auth` заново) |
| `40918` | Смена не открыта |
| `40920` | Смена открыта > 24 ч — закройте |
| `40917` | Недостаточно наличных |
| `40919` | Изымите наличные перед закрытием |
| `4038` / `4039` | Касса заблокирована |
| `40310` | Касса/пользователь неактивны |
| `5002` / `5040` | FPO недоступен / таймаут |

Это ошибки **коннектора**, обрабатывает фронт. Бэк-эндпоинты возвращают обычные `200/201/400/403/409`.

---

## 9. Подводные камни

1. **Токен 5 минут** — между `auth` и `open-shift` не держать модалок.
2. **Интернет** нужен только для `auth` + `open-shift`. После открытия смены чеки бьются офлайн → можно фискализировать, а `POST /cafe/fiscal/orders/{id}/receipt/` и `pay/` поставить в офлайн-очередь и досинхронизировать.
3. **SAM вынули/вставили** → повторный `verify-pin`.
4. **CORS на localhost:8080** — из браузера может блокироваться; нужен Electron/Tauri или локальный proxy/printer-bridge на `127.0.0.1`.
5. **enabled=false** — весь фискальный слой выключен, поведение как раньше.
```
