# Касса — открытые вопросы и пробелы (сентябрь 2026)

**Дата:** 2026-09-08
**От:** `nur-market` (FE / docs)
**Кому:** бэкенд NurCRM API
**Статус:** ждём ответ бэкенда

**Контекст:** фронт `nur-market` и Flutter-клиент готовы по контрактам `docs/backend/kassa/`.
P0/P1 из [BACKEND-QUESTIONS.md](./BACKEND-QUESTIONS.md) закрыты. Осталось 6 блоков ниже.

По каждому пункту нужен письменный ответ (**Да / Нет / Бэклог** + 1–2 строки детали) и, где
указано, пример ответа API со Staging.

TZ везде **Asia/Bishkek (UTC+6)**. Префикс `{host}/api`.

---

## Формат ответа (важно)

1. По каждому пункту: **Да / Нет / Бэклог** + деталь.
2. Блоки 1 и 2 — приложить примеры ответов API со Staging.
3. Блок 3 — указать выбранный вариант (A / B) и способ передачи кода удаления.
4. Даты выката Staging → Prod для блоков 1, 2, 3.
5. Ответы внести в таблицы «Ответы BE» соответствующих `.md` + пометить дату.
6. **Прислать ответ отдельным файлом** — `docs/backend/kassa/OPEN-QUESTIONS-2026-09-BE-RESPONSE.md`
   (или дополнить этот файл разделом «Ответы BE» и вернуть его целиком), чтобы FE мог
   разобрать по пунктам и снять флаги.

---

## Блок 1. Возврат чека (POS) → компенсирующее движение

Док: [sale-return-cashflow-backend.md](./sale-return-cashflow-backend.md) §16–17.
Сейчас на Staging (`tests_pos_sale_return_cashflow.py` 10/10, миграции `construction.0024` + `main.0118`), прод не тронут.

### 1.1. Подтвердить поведение (R3, R4, R7, R8, R11)

| # | Вопрос | Нужен ответ |
|---|---|---|
| R3 | `affects_shift_drawer` компенсирующего `expense`: `True` для наличного чека, `False` для card / mbank / безнала, по каждой части split отдельно? | Да/Нет + как реализовано |
| R4 | Статус компенсирующего `expense`: **всегда `approved`** (чтобы приход нетился в дефолтном `status=approved` отчёте сразу) или зеркалит статус исходного `pos_sale` (`pending`, кроме тарифа «Старт»)? Если зеркалит — при reject исходного `pos_sale` в Pending компенсация тоже реджектится каскадом? | Выбранная политика, зафиксировать в доке |
| R7 | Привязка смены: смена исходного чека открыта → в неё; закрыта → в текущую открытую смену кассира (без пересчёта `cash_diff` закрытой); нет открытой смены → `shift=null`, `affects_shift_drawer=False`, возврат не блокируется. Так? | Да/Нет по каждой из 3 ситуаций |
| R8 | `is_defect=true` + наличный чек: деньги клиенту **возвращаются**, товар на склад **не** возвращается, компенсирующий `expense` создаётся. Так? Или бизнес-правило «брак → деньги не отдаём»? | Подтвердить, что `expense` создаётся |
| R11 | Ошибка `INSERT` компенсирующего `expense` откатывает весь возврат (склад/долг не изменены). Есть тест-кейс №12? | Да/Нет + имя теста |

### 1.2. `idempotency_key` (R2)

- Есть ли `UNIQUE (company_id, idempotency_key)` на `CashFlow` / `SaleReturn`?
- Повторный `POST /main/pos/sales/{id}/return/` с тем же ключом → HTTP-код и тело? Ожидаем **200 + то же тело возврата**, без второго `expense`, без повторного отката склада/долга.
- Разные `idempotency_key` по одному `sale_id` — независимые частичные возвраты, суммируются?

### 1.3. Частичный возврат и статус чека (R5)

- Точное значение `sale.status` при частичном возврате на проводе — `"partially_returned"`? (нужно для `normalizeSaleStatus`, фильтра `?status=`, лейбла).
- В `cash_sales_total` / `noncash_sales_total` / `payment_breakdown` смены берётся **фактическая** сумма чека (`total − Σ returned_money`) или исходная? Если исходная — как смена сходится (за счёт `expense` с `affects_shift_drawer=True`)?
- В `GET /main/pos/sales/{id}/` по позициям есть `returned_qty` / `returnable_qty` на строку? (для ограничения «Макс. возврат» остатком).

### 1.4. Realtime (R9)

- Начать слать `market.cashflow.created` при создании `pos_sale_return` `expense` — канал компании/кассы, формат из [realtime-notifications-backend.md](./realtime-notifications-backend.md). FE-приёмник уже готов (`useNotificationsSocket` → `cashFlowRealtimeEventReceived`).
- Слать `market.shift.updated` с новыми `drawer_expected_cash` / `cash_sales_total`, если возврат затронул открытую смену. Подтвердить точные имена полей `cashbox_id` / `cashflow_id` / `shift_id` в пейлоаде.

### 1.5. Бэкфилл (R10)

Выбрать и описать план для возвратов, сделанных **до** выката фикса (у них нет парного `pos_sale_return`, `total_income` завышен):

1. миграция: по `sale.status in (canceled, partially_returned)` без парного `pos_sale_return` создать `expense` задним числом (дата = дата возврата, `status=approved`, `affects_shift_drawer=False`); **или**
2. ручная корректировка через `/crm/kassa`; **или**
3. задокументировать разрыв и не трогать.

Указать объём затронутых записей на проде.

### 1.6. Примеры ответов API (приложить со Staging)

- Тело `POST /main/pos/sales/{id}/return/` — полный и частичный (с `status`, суммами возврата).
- Тело повтора с тем же `idempotency_key`.
- `GET /construction/cashflows/` фрагмент с записью `pos_sale_return` (все поля).
- `GET /construction/shifts/{id}/` до и после возврата в открытой смене (diff по `drawer_expected_cash`, `cash_sales_total`).

### 1.7. Выкат

Дата переноса Staging → Prod после приёмки FE.

---

## Блок 2. Заявки на редактирование / отмену движения кассы

Док: [cashflow-edit-cancel-request-backend.md](./cashflow-edit-cancel-request-backend.md) §11–13.
Staging (`0025_cashflow_change_requests`, 13/13). Флаг FE `VITE_CASHFLOW_CHANGE_REQUESTS` выключен.

1. **WS-пейлоад (R8):** точные имена полей в `market.cashflow.updated` / `market.cashflow.deleted` / `market.cashflow.created` — `cashbox_id` vs `cashboxId` vs `cashbox`, `cashflow_id` vs `flow_id` vs `id`. FE сейчас читает по алиасам — нужен канонический контракт + пример реального сообщения.
2. **Прод:** дата выката эндпоинтов `/{id}/edit-request/`, `/{id}/cancel-request/` и новых полей в `GET /construction/cashflows/?status=pending` на прод. До этого флаг в прод-билд не включаем.
3. **`GET /construction/cashflows/?status=pending&cashbox={id}`** возвращает `request_kind`, `target_flow` (вложенный объект `id, name, amount, type, created_at`), `proposed`, `reason`, `requested_by` — подтвердить составом ответа со Staging.
4. **Закрытая смена (R6):** правки/отмены запрещены, `422` с `detail`. Подтвердить текст.
5. **Bulk-approve** (`PATCH /construction/cashflows/bulk/status/`) корректно применяет смешанный список (обычная операция + `edit` + `cancel`), атомарно по каждой — тест №12 из §9?

---

## Блок 3. Настройки кассы (код удаления + макс. скидка)

Док: [cashier-settings.md](./cashier-settings.md). **Эндпоинтов ещё нет** — фронт «мягко» деградирует. Нужна реализация целиком.

1. **Выбрать вариант хранения** (§2): **A** — `delete_item_code` = то же поле, что `Company.cashier_password` (одно значение в обеих вкладках); **B** — новое поле, `cashier_password` объявляется устаревшим. Сообщить выбор — от него зависит, убирать ли FE старое поле из вкладки «Моя компания».
2. **Реализовать 3 эндпоинта:**
   - `GET /main/pos/cashier-settings/` — всем сотрудникам; `delete_item_code` только owner/admin, остальным — `delete_item_code_required: bool` + `max_discount_percent` + `debt_schedule_version`.
   - `PATCH /main/pos/cashier-settings/` — только owner/admin (`403` иначе); валидация: код 4–8 цифр или `null` / `""`; `max_discount_percent` 0–100 до 2 знаков или `null`; `debt_schedule_version` ∈ {`v1`, `v2`}.
   - `POST /main/pos/cashier-settings/verify-delete-code/` — `{valid: bool}`, **всегда 200** (даже при неверном коде), константное сравнение, троттлинг ~10/мин на пользователя (`429` при превышении), код не задан → `{valid: true}`.
3. **Закрыть дыру:** перестать отдавать `cashier_password` / `delete_item_code` не-owner/admin в `GET /users/company/`. Сейчас кассир вытаскивает код из DevTools.
4. **Серверная проверка (не только UI):**
   - удаление позиции корзины (`DELETE /main/pos/carts/{cart_id}/items/{item_id}/`) и корзины целиком (`DELETE /main/pos/sales/{id}/`) — при заданном коде и роли ≠ owner/admin требовать подтверждение. **Сообщить способ передачи кода** (заголовок `X-Delete-Code` / короткоживущий токен от `verify-delete-code/`) — FE подстроится.
   - лимит скидки при `PATCH /main/pos/carts/{cart_id}/items/{item_id}/` (`discount_total`) и `POST /main/pos/start/` (`order_discount_total` / `order_discount_percent`); при превышении — `400 {"detail": "Максимальная скидка — N%", "max_discount_percent": "N.00"}`, **не** обрезать молча.
5. **Аудит:** попытки ввода кода (кто/когда/устройство); сам код не логировать и не писать в журнал удалений (`/main/pos/cart-item-deletions/`).
6. **`debt_schedule_version`:** подтвердить, что отсутствие поля на бэке трактуется как `v1`, и это единый источник для выбора UI отсрочки на кассе.

---

## Блок 4. Бэкфилл исторических смен ([BACKEND-QUESTIONS.md](./BACKEND-QUESTIONS.md) п.55)

Открыт с 2026-08-31.

- Есть ли на проде смены с `expected_cash`, посчитанным по старой формуле (закупки вычтены из POS-кассы, кейс −20 806)?
- Позиция: закрытые смены — зафиксированные данные, не пересчитываем; но нужен **отчёт/выборка** таких смен для владельцев (сколько, на какую сумму искажение), либо разовый скрипт-пересчёт `ledger_expected_cash` → `drawer_expected_cash` с сохранением исходного в отдельное поле.
- Дать решение: (a) отчёт, (b) пересчёт, (c) не трогаем + текст для клиента.

---

## Блок 5. Phase 2 — продуктовые пробелы

Док: [SCENARIO-POST-MIGRATION.md](./SCENARIO-POST-MIGRATION.md) §5.3, [BACKEND-QUESTIONS.md](./BACKEND-QUESTIONS.md) §8.
Не блокеры, но нужен статус «делаем / бэклог / не будем» по каждому:

1. **«Выдача из ящика» под закупку** — операция `expense` на POS-кассе с `affects_shift_drawer=true`, `shift_id=текущая`, linked со складским `expense` на `expense_variable`. Планируется?
2. **Transfer между кассами** (POS → `expense_variable`) отдельной операцией, а не только ручным движением в `/crm/kassa`.
3. **Блокировка закрытия смены** при расхождении больше порога (или хотя бы флаг `cash_diff_exceeds_threshold` в ответе `close/`).
4. **`non_drawer_expenses_total`** — подтвердить, что уже отдаётся в `GET /construction/shifts/{id}/` и в ответе `close/` (FE перестанет считать эвристикой на `CloseShiftPage`).
5. **«Касса 2» без `role`** — на проде все кассы получили `role` миграцией `0023`? Есть ли компании, где `get_inferred_role()` всё ещё срабатывает в рантайме?
6. **Тариф «Старт» vs pending** — авто-движения на «Старте» сразу `approved`; путаницы в Pending нет?
7. **Analytics `open_shift_expected_cash`** на карточке кассы — совпадает с `drawer_expected_cash` смены после фикса формулы?

---

## Блок 6. Синхронный выкат

Док: [auto-cashflows-backend.md](./auto-cashflows-backend.md) §Выкат, [SCENARIO-POST-MIGRATION.md](./SCENARIO-POST-MIGRATION.md) §7.

- `AUTO_CASHFLOWS` (BE) и `VITE_BE_CASHFLOWS` / `BE_CASHFLOWS` (клиенты) включаются **в один день**. Назначить дату/окно для Staging и для Prod.
- Нужен ли переходный заголовок `X-Client-Auto-Cashflows: 0|1` для поэтапного rollout по клиентам, или включаем разом?
- Мониторинг после включения: алерты на `400 cashbox_unresolvable` и на дубли в `cashflows` (double-write). Кто настраивает, какие метрики.
- Через 1–2 релиза после стабилизации — снять legacy-путь `postCashflowIfNeeded` для авто-операций (отдельный релиз FE). Подтвердить, что BE к этому моменту 100% источник истины.

---

## Ответы BE

Ответы бэкенда подготовлены и зафиксированы. Полный документ с примерами ответов со Staging доступен в файле:
[`docs/backend/kassa/OPEN-QUESTIONS-2026-09-BE-RESPONSE.md`](./NurCRM/docs/backend/kassa/OPEN-QUESTIONS-2026-09-BE-RESPONSE.md).

### Краткая сводка:
- **Блок 1 (Возврат чека):** Готово на Staging (тесты 10/10). `affects_shift_drawer = True` только для налички (в т.ч. пропорционально в split). Статус `approved` на Старте, `pending` на модерации. Привязка смены: открытая → в неё; закрытая → в открытую смену кассира; нет смены → без смены, не блокируется. При `is_defect` деньги возвращаются, товар на склад не поступает. Повторный `idempotency_key` возвращает 200 без дублей.
- **Блок 2 (Заявки на редактирование/отмену):** Готово на Staging (13/13 тестов). Канонические поля в сокетах: `cashbox_id`, `cashflow_id`, `shift_id`. При закрытой смене 422. Bulk-approve корректно применяет правки/отмены атомарно.
- **Блок 3 (Настройки кассы):** Готово на Staging (5/5 тестов). Выбран **Вариант A** (`cashier_password`). Эндпоинты `/cashier-settings/` и `/verify-delete-code/` работают. Утечка в `/users/company/` закрыта. Поддерживается передача кода через заголовок `X-Delete-Code` и через кэш-токен на 120 сек. Превышение лимита скидки возвращает 400. Отсутствие `debt_schedule_version` трактуется как `v1`.
- **Блок 4 (Бэкфилл смен):** Решение (c) — закрытые исторические смены не трогаем во избежание сдвига бухгалтерии. Новая формула разделения ящика и общих расходов активна для всех новых смен.
- **Блок 5 (Phase 2):** Выдача под закупку и прямой трансфер — в бэклоге Phase 2. `non_drawer_expenses_total` уже отдаётся. Роли всем кассам проставлены.
- **Блок 6 (Выкат):** Согласованная дата выката на Prod: **2026-09-10 (четверг) 04:00–06:00 UTC+6**.
