# Market POS — чек для печати и данные eKassa (фронтенд)

Кратко:

- **Чекаут** (`POST .../checkout/`) **не ждёт** eKassa: продажа сохраняется, `mark_paid` ставит фискализацию **в фоне** после commit.
- **Печать с реквизитами ОФД** — отдельный запрос: **`GET /api/main/pos/sales/<sale_id>/receipt/?wait_ekassa=1&receipt_text=1`** (сервер ждёт терминального состояния в `ekassa_fiscal`, затем отдаёт JSON и строку **`receipt_text`**).

Базовый префикс: `/api/main/`. Авторизация: `Authorization: Bearer <token>`.

---

## 1. Чекаут

### `POST /api/main/pos/sales/<cart_id>/checkout/`

- **`print_receipt: true`** — в ответе **нет** `receipt_text`; вместо этого поле **`receipt_print_path`**: относительный путь для GET печати (см. ниже).
- **`print_receipt: false`** — поля `receipt_print_path` нет.
- При готовой интеграции eKassa по-прежнему может быть **`ekassa: { "queued": true }`** — фискализация в фоне.

### `POST /api/main/agents/me/carts/<cart_id>/checkout/`

Те же правила: при **`print_receipt: true`** отдаётся **`receipt_print_path`** (тот же шаблон URL, `sale_id` — созданная продажа).

---

## 2. Печать (ожидание eKassa)

### `GET /api/main/pos/sales/<sale_id>/receipt/`

| Query | Назначение |
|--------|------------|
| **`wait_ekassa=1`** | Дождаться появления в БД финального результата фискализации (до ~45 с опроса; по таймауту — один повторный запрос к eKassa). Без параметра — ответ сразу, `ekassa` может быть ещё пустым. |
| **`receipt_text=1`** | В JSON добавить поле **`receipt_text`** (тот же формат, что в POS для физической печати). Имеет смысл вместе с **`wait_ekassa=1`**, если нужны ФД/ФПД/QR в тексте. |
| **`cashier_name`** | Опционально, как раньше. |

Пример для кассы после успешного чекаута с `print_receipt: true`:

```
GET /api/main/pos/sales/<sale_id>/receipt/?wait_ekassa=1&receipt_text=1
```

В теле ответа: обычный **`build_receipt_payload`**, плюс при наличии данных — **`ekassa`**, при **`receipt_text=1`** — строка **`receipt_text`**.

---

## 3. JSON документа чека

### `GET /api/main/sales/json/<sale_id>/receipt/`

- Query **`wait_ekassa=1`** — перед сборкой ответа дождаться фискализации (та же логика, что для POS receipt).
- Поле **`ekassa`** в корне — `sale.ekassa_fiscal` после ожидания (если было).

---

## 4. Структура `ekassa_fiscal` / `ekassa` (ориентир для UI)

| Ключ | Описание |
|------|----------|
| `status` | `"pending"` \| `"ok"` \| `"error"` |
| `newid` | UUID запроса к eKassa |
| `fd_number` | Номер ФД |
| `ekassa_receipt_id` | ID чека в ответе eKassa |
| `message` | Сообщение или текст ошибки |
| `fields` | Словарь полей ФД из API |
| `kkm_reg_number` | РН ККМ (`fields["1037"]`) |
| `fm_number` | ФМ (`fields["1041"]`) |
| `fpd` | ФПД (`fields["1077"]`) |
| `link` | Ссылка для QR / проверки |
| `ekassa_payload` | При ошибке API (если сохранён) |

---

## Рекомендация для UX

1. Чекаут — сразу показывать успех и `sale_id`.
2. Если нужна печать с ОФД — **`GET`** по **`receipt_print_path`** из ответа (или собрать URL вручную с `wait_ekassa=1&receipt_text=1`).
3. Пока `wait_ekassa` не вернулся — можно показать индикатор ожидания; по таймауту обработать пустой/частичный `ekassa`.
