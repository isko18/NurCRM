# Техническое задание: интеграция модуля Building с 1С

| | |
|---|---|
| **Документ** | ТЗ на интеграцию с 1С (двусторонняя) |
| **Модуль-потребитель** | `apps/building` |
| **Новый модуль интеграции** | `apps/onec` (предлагается) |
| **Платформа** | nurCRM (Django REST Framework, Celery, Redis) |
| **Версия** | 1.0 |
| **Дата** | 12.06.2026 |

---

## 1. Цель и объём

### 1.1. Цель
Обеспечить **двустороннюю** интеграцию денежного контура модуля Building с 1С:

- **Outbound (nurCRM → 1С):** все денежные операции (продажи/договоры, платежи
  по рассрочке, закупки и оплаты поставщикам, движения по кассе, выплаты ЗП/авансы,
  долги/бартер) автоматически формируют документы в 1С.
- **Inbound (1С → nurCRM):** возвращается аналитика и подтверждения — подтверждение
  проведения документов, взаиморасчёты/сальдо по контрагентам, финансовые
  показатели, справочники (мастер-данные).

### 1.2. Согласованные параметры
| Параметр | Решение |
|----------|---------|
| Транспорт | HTTP-сервисы 1С (REST), JSON |
| Режим выгрузки | Реалтайм через Celery-задачи (с ретраями) |
| Объём outbound | Продажи/договоры, платежи по рассрочке, закупки/оплаты поставщикам, касса/ЗП/долги/бартер |
| Объём inbound | Подтверждение проведения, сальдо/взаиморасчёты, фин. показатели, справочники |

### 1.3. Эталон реализации
В проекте уже есть рабочий образец интеграции — `apps/ekassa`:
`EkassaIntegration` (пер-компанийный конфиг с шифрованием), `client.py`
(HTTP-клиент с кешем токена и ретраем на 401), `crypto.py`, `exceptions.py`,
`signals.py`. **Интеграцию с 1С строим по этому же паттерну.** Дополнительно
используем **Celery + Redis** (уже в зависимостях) для асинхронной доставки и
паттерн HMAC-подписи из `apps/main/services/webhooks.py` для входящих вызовов.

---

## 2. Архитектура

### 2.1. Общая схема
```
                        nurCRM (Building)
  ┌─────────────────────────────────────────────────────────────┐
  │ Денежная операция (treaty / cashflow / procurement / payroll)│
  │        │ transaction.on_commit                               │
  │        ▼                                                      │
  │  services_1c.enqueue_push(source)                            │
  │        │ создаёт/обновляет OneCSyncRecord (outbox, идемпотент)│
  │        ▼                                                      │
  │  Celery task push_to_1c(sync_id) ──(retry/backoff)──┐        │
  └───────────────────────────────────────────────────┼────────┘
                                                       │ HTTPS POST (JSON)
                                                       ▼
                                          ┌──────────────────────┐
                                          │   1С: HTTP-сервисы    │
                                          │  /hs/nurcrm/...       │
                                          └──────────┬───────────┘
   Inbound:                                          │
   1) callback (проведение) ──HMAC──► /api/onec/callbacks/posting/
   2) Celery beat pull ◄────────────── фин.показатели / сальдо / справочники
```

### 2.2. Принцип Outbox (обязательно)
Нельзя дёргать 1С прямо в HTTP-запросе пользователя. Вместо этого:

1. В транзакции операции пишем запись `OneCSyncRecord` (статус `pending`).
2. Через `transaction.on_commit` ставим Celery-задачу (гарантия: не выгружаем
   незакоммиченные данные).
3. Задача идемпотентно отправляет документ в 1С, сохраняет внешний ID/номер,
   статус `sent`/`posted`. При ошибке — ретрай с экспоненциальным backoff;
   после N попыток — `failed` (видно в админке, доступен ручной retry).

Это даёт надёжность «at-least-once» + идемпотентность по ключу.

### 2.3. Состав нового модуля `apps/onec`
| Файл | Назначение |
|------|------------|
| `models.py` | `OneCIntegration`, `OneCSyncRecord`, read-model'и (`OneCCounterpartyBalance`, `OneCFinancialSnapshot`) |
| `crypto.py` | Шифрование секретов (можно переиспользовать `apps/ekassa/crypto.py`) |
| `client.py` | `OneCHttpClient`: авторизация, кеш токена, `request_json` с ретраем |
| `exceptions.py` | `OneCAPIError` |
| `mappers.py` | Преобразование объектов Building → payload документов 1С |
| `services.py` | `enqueue_push()`, обработка callback'ов, помощники |
| `tasks.py` | Celery: `push_to_1c`, `pull_balances`, `pull_financials`, `pull_master_data` |
| `views.py` | CRUD настроек, мониторинг sync-записей, callback-эндпоинты |
| `urls.py` | Маршруты `/api/onec/...` |
| `signals.py` | Сброс кеша токена при смене настроек |
| `admin.py` | Админка по sync-записям (повтор, просмотр payload) |

> Альтернатива: разместить тонкий слой прямо в `apps/building` (`integration_1c.py`),
> если интеграция не будет переиспользоваться другими модулями. Рекомендуется
> отдельный `apps/onec` — по аналогии с `ekassa` и для повторного использования.

---

## 3. Модель данных

### 3.1. `OneCIntegration` (пер-компанийный конфиг)
| Поле | Тип | Описание |
|------|-----|----------|
| `company` | FK Company (unique) | Владелец настроек (мультитенантность) |
| `is_active` | bool | Интеграция включена |
| `base_url` | str | Базовый URL HTTP-сервисов 1С (`https://1c.host/base/hs/nurcrm`) |
| `auth_type` | choice | `basic` / `token` |
| `login` | str | Пользователь 1С |
| `password_cipher` | bytes | Зашифрованный пароль/токен |
| `inbound_secret_cipher` | bytes | Секрет HMAC для входящих callback'ов |
| `enabled_sources` | JSON | Какие операции выгружать (флаги по типам) |
| `last_pull_at` | datetime | Время последней успешной выгрузки из 1С |
| `request_timeout` | int | Таймаут запроса, сек |

### 3.2. `OneCSyncRecord` (outbox / журнал синхронизации)
| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | PK |
| `company` | FK | Компания |
| `direction` | choice | `outbound` / `inbound` |
| `source_type` | choice | `treaty`, `installment_payment`, `cashflow`, `procurement`, `payroll_payment`, `advance`, `debt`, `barter` |
| `source_id` | UUID | ID объекта Building |
| `operation` | choice | `create` / `update` / `cancel` |
| `idempotency_key` | str (unique) | `{source_type}:{source_id}:{operation}` |
| `status` | choice | `pending`, `sending`, `sent`, `posted`, `failed`, `skipped` |
| `attempts` | int | Кол-во попыток |
| `last_error` | text | Последняя ошибка |
| `onec_doc_type` | str | Тип документа в 1С |
| `onec_external_id` | str | GUID документа в 1С |
| `onec_number` | str | Номер документа в 1С |
| `onec_posted_at` | datetime | Когда проведён в 1С |
| `request_payload` | JSON | Что отправили |
| `response_payload` | JSON | Что вернулось |
| `created_at` / `updated_at` | datetime | |

> Уникальность `idempotency_key` гарантирует отсутствие дублей документов в 1С.

### 3.3. Read-model'и для inbound-аналитики
- `OneCCounterpartyBalance` — сальдо по контрагенту (клиент/поставщик/подрядчик),
  валюта, сумма, дата актуальности. Сверяется с `BuildingDebtLedgerEntry`.
- `OneCFinancialSnapshot` — фин. показатели по ЖК/компании за период
  (выручка, расходы, прибыль), для дашбордов.
- Справочники (мастер-данные) синхронизируются в существующие справочники Building
  (`BuildingProduct`, `BuildingSupplier`, `BuildingClient`) с пометкой источника
  и внешним ID 1С (`onec_external_id`).

---

## 4. Outbound: выгрузка операций

### 4.1. Точки подключения в коде (где ставить `enqueue_push`)
Все вызовы — через `transaction.on_commit` внутри существующих транзакций.

| Операция | Где (текущий код) | Документ 1С (предв.) |
|----------|-------------------|----------------------|
| Продажа/бронь/подписание договора | `services.create_sale_commission_adjustment` / сохранение `BuildingTreaty` (active/signed) | Реализация / Договор |
| Платёж по рассрочке | `BuildingTreatyInstallmentPaymentView` | ПКО (приходный ордер) |
| Одобрение заявки на кассу → создание CashFlow | `CashRegisterRequestApproveView` | ПКО / РКО (по типу) |
| Движение по кассе (income/expense) | `BuildingCashFlow` create / approve | ПКО / РКО |
| Закупка одобрена кассой | `services.approve_procurement_cash` | Заказ поставщику / Поступление |
| Приёмка передачи (закупка в долг) | `services.accept_transfer` | Поступление товаров + долг |
| Выплата ЗП | `BuildingPayrollPaymentApproveView` | Ведомость / РКО |
| Аванс по ЗП | `AdvanceRequestApproveView` | РКО |
| Запись долга/бартера | `BuildingDebtsLedgerListCreateView`, бартерные зачёты | Корректировка долга |

> Существующий `services.request_treaty_create_in_erp` (env `BUILDING_ERP_*`)
> обобщается/заменяется новым механизмом: текущая логика становится частным
> случаем выгрузки `treaty`.

### 4.2. Контракт запроса (пример)
```
POST {base_url}/documents/sale
Authorization: <basic|bearer>
Content-Type: application/json

{
  "external_id": "<UUID nurCRM>",          // ключ идемпотентности на стороне 1С
  "source_type": "treaty",
  "operation": "create",
  "occurred_at": "2026-06-12T10:00:00Z",
  "company_inn": "...",
  "residential_complex": {"id": "...", "name": "..."},
  "counterparty": {"type": "client", "id": "...", "name": "...", "inn": "..."},
  "amount": "1500000.00",
  "currency": "KGS",
  "lines": [ ... ],
  "meta": { ... }
}
```
Ответ 1С:
```
{ "status": "ok", "onec_id": "<GUID>", "number": "РЕАЛ-0001", "posted": true }
```

### 4.3. Поведение задачи `push_to_1c`
1. Берёт `OneCSyncRecord` по id, проверяет статус (`pending`/`failed`).
2. Строит payload через `mappers.build_payload(source_type, source)`.
3. Вызывает `OneCHttpClient.request_json("POST", endpoint, json_body=payload)`.
4. Успех → `onec_external_id`, `onec_number`, статус `sent` (или `posted`, если
   1С сразу проводит); `response_payload` сохраняется.
5. Ошибка сети/5xx → исключение → Celery autoretry (backoff). После лимита —
   `failed`, алерт.
6. Бизнес-ошибка 1С (4xx с телом) → `failed` + `last_error` без бессмысленных ретраев.

---

## 5. Inbound: данные из 1С

### 5.1. Подтверждение проведения (push от 1С)
- Эндпоинт: `POST /api/onec/callbacks/posting/`.
- Аутентификация: HMAC-подпись тела (`X-OneC-Signature: sha256=...`,
  секрет из `OneCIntegration.inbound_secret_cipher`) — по образцу webhooks.
- Тело: `{ "external_id", "onec_id", "number", "posted": true, "posted_at" }`.
- Действие: находим `OneCSyncRecord` по `external_id`, ставим `posted`,
  сохраняем номер/дату; при необходимости — обновляем статус исходного объекта
  Building (например, `BuildingTreaty.erp_*`).

### 5.2. Аналитика (pull, Celery beat)
| Задача | Источник 1С | Куда |
|--------|-------------|------|
| `pull_balances` | `GET /balances?date=...` | `OneCCounterpartyBalance` + сверка с реестром долгов |
| `pull_financials` | `GET /reports/financial?period=...` | `OneCFinancialSnapshot` (дашборды) |
| `pull_master_data` | `GET /catalogs/{nomenclature,counterparties}` | upsert в справочники Building |

- Периодичность настраивается (по умолчанию: сальдо/показатели — раз в час,
  справочники — раз в сутки/по требованию).
- Инкрементальность: храним `last_pull_at`, запрашиваем изменения «с момента».
- Конфликты мастер-данных: 1С — источник истины; локальные записи помечаются
  `onec_external_id`, сопоставление по ИНН/коду/штрихкоду.

### 5.3. Наш API для фронта (чтение аналитики)
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/onec/sync-records/` | Журнал синхронизации (мониторинг, фильтры по статусу/типу) |
| POST | `/api/onec/sync-records/<id>/retry/` | Повторить выгрузку (owner-like) |
| GET | `/api/onec/balances/` | Сальдо контрагентов из 1С |
| GET | `/api/onec/financials/` | Финансовые показатели по ЖК/компании |
| GET/PUT | `/api/onec/settings/` | Настройки интеграции (owner-like) |

---

## 6. Безопасность и надёжность (NFR)

- **NFR-1. Мультитенантность.** Настройки и sync-записи изолированы по `company`.
- **NFR-2. Секреты.** Пароли/токены/HMAC-секреты — только в зашифрованном виде
  (`crypto`), не в логах и не в API-ответах.
- **NFR-3. Идемпотентность.** `idempotency_key` + передача нашего UUID как
  `external_id`; 1С обязана дедуплицировать по нему.
- **NFR-4. Доставка at-least-once.** Outbox + `transaction.on_commit` + Celery
  retry с backoff + dead-letter (статус `failed`, ручной retry).
- **NFR-5. Целостность.** Никаких внешних вызовов внутри транзакции; выгрузка —
  только после commit.
- **NFR-6. Аутентификация callback'ов.** HMAC-подпись обязательна; запросы без
  валидной подписи отклоняются (401).
- **NFR-7. Наблюдаемость.** Логирование (как в `ekassa`/`webhooks`), статусы в
  админке, метрики по `failed`/`pending`.
- **NFR-8. Отказоустойчивость.** Недоступность 1С не ломает работу пользователя
  (операция проходит, синхронизация догоняет позже).
- **NFR-9. Конфигурируемость.** Включение/выключение по типам операций
  (`enabled_sources`) и по компании.

---

## 7. Конфигурация

| Источник | Параметр | Назначение |
|----------|----------|------------|
| `OneCIntegration` (БД) | `base_url`, `auth_type`, `login`, `password`, `inbound_secret`, `enabled_sources`, `request_timeout` | Пер-компанийные настройки |
| `settings`/env | `ONEC_REQUEST_TIMEOUT` | Дефолтный таймаут |
| `settings`/env | `ONEC_TOKEN_CACHE_SECONDS` | TTL кеша токена |
| Celery beat | расписание pull-задач | Периодичность входящей аналитики |

> Существующие `BUILDING_ERP_TREATY_ENDPOINT` / `BUILDING_ERP_TOKEN` —
> переносятся в `OneCIntegration` (как частный случай выгрузки договоров).

---

## 8. Что требуется от стороны 1С (зависимости)

1. **Опубликованные HTTP-сервисы** с описанием контракта: список эндпоинтов,
   схемы JSON запросов/ответов, способ авторизации (Basic/Token).
2. **Идемпотентность по `external_id`** (наш UUID) — обязательна.
3. **Маппинг документов:** какой документ 1С соответствует каждой операции
   (Реализация, ПКО/РКО, Поступление, Ведомость, Корректировка долга).
4. **Ключи сопоставления контрагентов и номенклатуры** (ИНН/код/штрихкод).
5. **Callback проведения** (либо согласие на наш polling), формат и подпись.
6. **Тестовый контур (sandbox)** 1С для отладки.
7. **Единая валютная политика** (в Building встречаются `KGS` и `KZT` —
   уточнить, в какой валюте формируются документы 1С).

---

## 9. Этапы внедрения (roadmap)

| Этап | Содержание | Результат |
|------|------------|-----------|
| 0. Подготовка | Контракт HTTP-сервисов 1С, sandbox, маппинг документов | Согласованная спецификация обмена |
| 1. Каркас | App `apps/onec`: модели, `OneCIntegration`, `client`, `crypto`, миграции, админка | Настройка интеграции, ручной тест-вызов |
| 2. Outbound MVP | Outbox + Celery `push_to_1c` + mapper для договоров и кассы | Продажи и касса уходят в 1С |
| 3. Outbound full | Закупки/оплаты, рассрочка, ЗП/авансы, долги/бартер | Все денежные операции в 1С |
| 4. Inbound подтверждения | Callback `/posting/` + обновление статусов | Статусы проведения в nurCRM |
| 5. Inbound аналитика | `pull_balances`, `pull_financials`, read-model'и, API для фронта | Сальдо и показатели из 1С |
| 6. Мастер-данные | `pull_master_data` + сопоставление справочников | Синхронизация номенклатуры/контрагентов |
| 7. Харднинг | Мониторинг, алерты, ретраи, нагрузочное тестирование | Прод-готовность |

---

## 10. Открытые вопросы

1. **Валюта документов** — Building использует и `KGS`, и `KZT`; в какой валюте
   формировать документы 1С и как конвертировать?
2. **Кто «источник истины» по долгам** — наш `BuildingDebtLedgerEntry` или 1С?
   (влияет на правила сверки сальдо).
3. **Проведение в 1С** — автоматическое при создании документа или ручное
   бухгалтером? (влияет на статусную модель `sent` vs `posted`).
4. **Отмены/сторно** — как обрабатывать отмену операции в nurCRM
   (cancel → сторно-документ в 1С)?
5. **Гранулярность мастер-данных** — какие справочники 1С считаем главными,
   а какие ведём в nurCRM.
