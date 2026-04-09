# Маркет: зарплата продавца (фронтенд)

Документация по настройке схемы ЗП сотрудника для **чеков маркета** (`main.Sale`, поле `user` — кто оформил продажу) и по отчёту за период.

**Базовый префикс API:** `/api/main/`  
**Авторизация:** как у остальных эндпоинтов (обычно `Authorization: Bearer …`).

---

## 1. Профиль ЗП (CRUD)

### Эндпоинты

| Метод | Путь | Описание |
|--------|------|----------|
| `GET` | `/api/main/market-sale-employee-pay-profiles/` | Список профилей |
| `POST` | `/api/main/market-sale-employee-pay-profiles/` | Создание |
| `GET` | `/api/main/market-sale-employee-pay-profiles/{id}/` | Один профиль |
| `PATCH` | `/api/main/market-sale-employee-pay-profiles/{id}/` | Частичное обновление |
| `PUT` | `/api/main/market-sale-employee-pay-profiles/{id}/` | Полная замена (если используете) |
| `DELETE` | `/api/main/market-sale-employee-pay-profiles/{id}/` | Удаление |

### Query-параметры списка

Поддерживаются стандартные возможности DRF (в т.ч. фильтры, если включены):

- `user` — UUID сотрудника  
- `branch` — UUID филиала  

Сортировка: `ordering=id` и др. по `ordering_fields`.

### Контекст компании и филиала

- **`company`** и **`branch`** в ответе **read-only**; при создании/обновлении подставляются с бэкенда по правилам приложения `main` (как у других справочников с `CompanyBranchReadOnlyMixin`).
- **Список и выборка одной записи** зависят от «активного филиала» запроса (как у других `main`-ресурсов):
  - если у пользователя выбран/задан филиал — видны профили этого филиала **и** глобальные (`branch: null`);
  - если филиал в контексте не задан — видны только **глобальные** профили (`branch: null`).
- Передача филиала обычно через тот же механизм, что и для маркет-аналитики: query `?branch=<uuid>` для владельца без жёсткой привязки к одному филиалу (см. общую логику `main`).

### Тело объекта (JSON)

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | string (UUID) | Только чтение |
| `company` | string (UUID) | Только чтение |
| `branch` | string (UUID) \| `null` | Только чтение; `null` = профиль на всю компанию |
| `user` | string (UUID) | Сотрудник компании |
| `pay_scheme` | string | См. таблицу схем ниже |
| `monthly_base_salary` | string / number | Оклад **в месяц** (деньги, ≥ 0) |
| `sales_percent` | string / number | Процент от **личных** продаж за период (0–100, шаг до сотых) |

### Значения `pay_scheme`

| Значение | Подпись (как в API отображения) | Что нужно заполнить |
|----------|--------------------------------|----------------------|
| `salary` | Оклад | `monthly_base_salary` > 0 |
| `percent` | Процент от продаж | `sales_percent` > 0 |
| `salary_plus_percent` | Оклад + процент от продаж | оба > 0 |

**По умолчанию на бэкенде:** `pay_scheme = "salary_plus_percent"`.

### Ограничения и ошибки

- Один профиль на пару **(компания, сотрудник)** для глобального режима (`branch = null`).
- Один профиль на **(компания, филиал, сотрудник)** для филиального профиля.
- `sales_percent` не может быть **> 100** (ошибка валидации).
- При несоответствии схеме и полям вернутся сообщения вида:
  - `monthly_base_salary`: «Для схемы «Оклад»…»
  - `sales_percent`: «Для схемы «Процент»…»
  - для `salary_plus_percent` возможна общая строка ошибки (non-field) или по полю — ориентируйтесь на тело ответа 400.

### Примеры запросов

**Создать только оклад:**

```http
POST /api/main/market-sale-employee-pay-profiles/
Content-Type: application/json

{
  "user": "uuid-сотрудника",
  "pay_scheme": "salary",
  "monthly_base_salary": "45000.00",
  "sales_percent": "0"
}
```

**Только процент от продаж:**

```json
{
  "user": "uuid-сотрудника",
  "pay_scheme": "percent",
  "monthly_base_salary": "0",
  "sales_percent": "5.5"
}
```

**Оклад + процент:**

```json
{
  "user": "uuid-сотрудника",
  "pay_scheme": "salary_plus_percent",
  "monthly_base_salary": "30000.00",
  "sales_percent": "2.00"
}
```

---

## 2. Отчёт по ЗП за период (маркет-аналитика)

### Эндпоинт

`GET /api/main/analytics/market/?tab=salary`

Те же права и общий кеш маркет-аналитики, что и у других вкладок (`sales`, `finance`, …).

### Query-параметры

| Параметр | Описание |
|----------|----------|
| `tab` | Обязательно: `salary` |
| `date_from` **или** `period_start` | Начало периода (дата/дата-время; см. ниже) |
| `date_to` **или** `period_end` | Конец периода |
| `branch` | UUID филиала (логика как у остальной маркет-аналитики) |
| `include_global` | `1` / `true` / `yes` / `on` — при выбранном филиале учитывать продажи с `branch = null` вместе с филиалом (как на вкладке продаж) |

Если **не** передать границы периода, используется **текущий календарный месяц** (с 1-го числа 00:00 до 1-го числа следующего месяца, **конец exclusive**).

**Формат дат:** строка ISO. Если `date_to` / `period_end` передан как дата **только датой** (`YYYY-MM-DD`), бэкенд добавляет **+1 день** к концу, чтобы последний день включался в полуинтервал `[start, end)`.

### Откуда берутся продажи для процента

- Модель: `Sale` приложения `main`.
- Учитываются только чеки со статусом **оплачен** (`paid`).
- Время отбора: поле **`paid_at`**, если оно есть; иначе **`created_at`**.
- Сумма по сотруднику: агрегат **`total`** по чекам, где **`user_id`** = сотрудник из профиля.
- Фильтрация по филиалу чека — **как в коде вкладки `salary`** (согласовано с полем `branch` у `Sale` и параметром `include_global`).

> Вкладка `salary` **не** применяет к продажам дополнительные фильтры `cashbox`, `shift`, `cashier`, `payment_method` (они есть у вкладки `sales`). При необходимости узкого отчёта обсуждайте расширение API.

### Успешный ответ (структура)

Ответ включает **детальные строки** (`rows`), **сводные карточки** (`cards`), **графики** (`charts`) и **таблицы** (`tables`).

```json
{
  "tab": "salary",
  "period": {
    "from": "2026-04-01T00:00:00+06:00",
    "to": "2026-05-01T00:00:00+06:00"
  },
  "filters": {
    "branch": "uuid или null",
    "include_global": false
  },
  "cards": {
    "employees_with_profile": 5,
    "total_payroll": "150000.00",
    "total_base_prorated": "120000.00",
    "total_percent_bonus": "30000.00",
    "total_employee_sales": "500000.00",
    "sales_count": 142,
    "avg_payroll_per_employee": "30000.00",
    "blended_commission_rate_pct": 6.0
  },
  "charts": {
    "staff_sales_by_day": [
      { "date": "2026-04-01", "sales_total": "12000.00", "sales_count": 8 }
    ]
  },
  "tables": {
    "by_pay_scheme": [
      {
        "pay_scheme": "salary_plus_percent",
        "pay_scheme_label": "Оклад + процент от продаж",
        "employees_count": 3,
        "total_pay": "90000.00",
        "total_base_prorated": "70000.00",
        "total_percent_bonus": "20000.00",
        "total_employee_sales": "300000.00",
        "sales_count": 90
      }
    ],
    "top_by_payroll": [
      {
        "user_id": "uuid",
        "employee_label": "Иван Иванов",
        "total": "45000.00",
        "pay_scheme": "salary_plus_percent"
      }
    ]
  },
  "rows": [
    {
      "user_id": "uuid",
      "employee_label": "Иван Иванов",
      "profile_scope": "branch",
      "pay_scheme": "salary_plus_percent",
      "pay_scheme_label": "Оклад + процент от продаж",
      "monthly_base_salary": "30000.00",
      "sales_percent": "2.00",
      "period_days": 30,
      "base_prorated": "30000.00",
      "employee_sales_period": "125000.50",
      "percent_bonus": "2500.01",
      "total": "32500.01",
      "sales_count": 24
    }
  ]
}
```

### `filters`

| Поле | Смысл |
|------|--------|
| `branch` | UUID активного филиала или `null` |
| `include_global` | Учитывались ли чеки без филиала вместе с выбранным филиалом |

### `cards` (сводка по ЗП и продажам сотрудников с профилем)

| Поле | Смысл |
|------|--------|
| `employees_with_profile` | Сколько сотрудников попало в отчёт (есть профиль ЗП в текущем контексте) |
| `total_payroll` | Сумма итоговых `total` по всем строкам (фактическая «ЗП к начислению» за период по правилам схем) |
| `total_base_prorated` | Сумма пропорциональных окладов за период (по полю `base_prorated`, в т.ч. для схемы только % — оклад всё равно считается как компонент, но в `total` может не входить) |
| `total_percent_bonus` | Сумма `percent_bonus` по всем |
| `total_employee_sales` | Сумма личных продаж (`employee_sales_period`) по сотрудникам из отчёта |
| `sales_count` | Число оплаченных чеков этих сотрудников за период (сумма по строкам) |
| `avg_payroll_per_employee` | `total_payroll / employees_with_profile` |
| `blended_commission_rate_pct` | `total_percent_bonus / total_employee_sales * 100` по всему отчёту; `null`, если продаж нет |

### `charts.staff_sales_by_day`

По **всем** сотрудникам из отчёта вместе: оплаченные чеки за период с теми же фильтрами филиала, группировка по календарному дню (`paid_at` / `created_at`). Удобно для линейного графика «оборот кассиров с настроенной ЗП».

### `tables.by_pay_scheme`

Агрегаты по значению `pay_scheme`: сколько человек, суммы ЗП, окладной части, бонусов, продаж, число чеков.

### `tables.top_by_payroll`

До **15** сотрудников с наибольшим полем `total` (итоговая ЗП за период).

### Поля строки `rows[]`

| Поле | Смысл |
|------|--------|
| `user_id` | Сотрудник |
| `employee_label` | Имя для отображения |
| `profile_scope` | `"branch"` — использован филиальный профиль; `"global"` — глобальный |
| `pay_scheme` | Код схемы |
| `pay_scheme_label` | Человекочитаемая подпись с бэкенда |
| `monthly_base_salary` | Оклад в месяц из профиля (строка с деньгами) |
| `sales_percent` | Процент из профиля |
| `period_days` | Число дней в периоде: `(end - start)` в сутках, минимум 1 |
| `base_prorated` | Оклад за период: `monthly_base_salary * period_days / 30`, округление до копеек |
| `employee_sales_period` | Сумма `total` оплаченных чеков сотрудника за период |
| `percent_bonus` | `employee_sales_period * sales_percent / 100` |
| `total` | Итог по схеме: только оклад, только бонус, или сумма (для `salary_plus_percent`) |
| `sales_count` | Число оплаченных чеков сотрудника за период |

При отсутствии модели продаж: `"rows": []`, пустые `cards` / `charts` / `tables`, поле `"detail"` с текстом — на практике для маркета не ожидается.

### Приоритет профилей (филиал vs глобальный)

Если для одного сотрудника есть и глобальный профиль (`branch = null`), и профиль филиала, при расчёте в отчёте **приоритет у филиального** профиля (если текущий контекст — этот филиал и оба попали в выборку).

---

## 3. Рекомендации для UI

1. **Форма профиля:** три радиокнопки / селект по `pay_scheme`; показывать/прятать или подсвечивать поля `monthly_base_salary` и `sales_percent` в зависимости от схемы (см. таблицу обязательных полей).
2. **Отчёт:** сверху — карточки из `cards` (итого ЗП, средняя, продажи); круговая/столбчатая — `tables.by_pay_scheme`; график по дням — `charts.staff_sales_by_day`; мини-рейтинг — `tables.top_by_payroll`; полная таблица — `rows` (колонка `sales_count` при желании).
3. **Согласованность периода:** использовать те же `date_from`/`date_to` (или `period_*`), что на остальных вкладках маркет-аналитики, чтобы пользователь видел один и тот же интервал.

---

## 4. Связанный код (бэкенд)

- Модель: `apps/main/models.py` — `MarketSaleEmployeePayProfile`
- Сериализатор: `MarketSaleEmployeePayProfileSerializer` в `apps/main/serializers.py`
- Вьюхи и URL: `apps/main/views.py`, `apps/main/urls.py`
- Расчёт отчёта: `AnalyticsView._salary` в `apps/main/analytics_market.py` (`tab=salary`)
