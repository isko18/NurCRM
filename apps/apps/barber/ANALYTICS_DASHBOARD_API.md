# Дашборд аналитики услуг — API для фронта

Единый агрегирующий эндпоинт для страницы «Аналитика» сферы услуг (barber / services / dentistry).
Заменяет 6 пагинированных списков, все cashflows и до 60 detail-запросов POS одним ответом за период.

```
GET /api/barbershop/analytics/dashboard/?date_from=2026-08-01&date_to=2026-08-31
```

| | |
|---|---|
| Запросов на экран | 1 |
| Кэш ответа | 60 сек (ключ: компания + филиал + период) |
| Блоков в ответе | 9 |
| Макс. период | 366 дней |

Реализация: [`apps/barber/analytics_dashboard.py`](analytics_dashboard.py), маршрут в [`apps/barber/urls.py`](urls.py).

---

## 1. Быстрый старт

Авторизация — обычный Bearer-токен. Компания и филиал берутся из профиля пользователя, передавать их не нужно.

```js
export async function fetchDashboard({ dateFrom, dateTo }) {
  const qs = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  const res = await api.get(`/barbershop/analytics/dashboard/?${qs}`);
  return mapDashboardResponse(res.data);
}
```

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://app.nurcrm.kg/api/barbershop/analytics/dashboard/?date_from=2026-08-01&date_to=2026-08-31"
```

Смена месяца — это повторный запрос с новыми `date_from` / `date_to`. Ответ кэшируется на бэкенде
60 секунд, так что переключение туда-обратно почти бесплатно.

---

## 2. Параметры

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| `date_from` | `YYYY-MM-DD` | нет | Первый день периода включительно. По умолчанию — 1-е число текущего месяца |
| `date_to` | `YYYY-MM-DD` | нет | Последний день включительно, до `23:59:59` по Asia/Bishkek. По умолчанию — сегодня |
| `branch` | `uuid` | нет | Учитывается **только** если у сотрудника нет жёстко закреплённого филиала. У закреплённого параметр игнорируется — филиал берётся из профиля |

Границы периода полуоткрытые: `[date_from 00:00:00, date_to+1 00:00:00)` в таймзоне `Asia/Bishkek`.
Группировка по дням — тоже в местном времени.

---

## 3. Доступ и ошибки

Эндпоинт видят: `owner`, `admin`, суперпользователь, а также сотрудник с флагом
`can_view_barber_history` либо `can_view_barber_records`. Правило то же, что у старого
`/barbershop/analytics/`.

| Код | Когда | Что показать |
|---|---|---|
| `400` | Битая дата, `date_to < date_from`, период больше 366 дней | Текст ошибки из тела ответа — он human-readable и на русском |
| `401` | Нет токена или он протух | Обычный redirect на логин |
| `403` | Нет прав на аналитику, либо у пользователя не настроена компания | «Нет доступа к аналитике» — страницу не рендерим |
| `200` | Всё хорошо, в том числе за пустой месяц | Пустой месяц — это нули и пустые массивы, а не ошибка |

Тело `400`:

```json
{ "date_to": ["date_to должен быть >= date_from."] }
```

---

## 4. Форма ответа

Девять блоков верхнего уровня. Денежные значения — `number` с двумя знаками, количества — `number`
с тремя. Массивы всегда присутствуют, пустой период даёт `[]`, а не `null`.

```jsonc
{
  "period": { "date_from": "2026-08-01", "date_to": "2026-08-31", "label": "2026-08" },

  "totals": {
    "appointments_total": 128,      "appointments_completed": 97,
    "appointments_canceled": 18,    "appointments_no_show": 13,
    "revenue_completed": 184500.0,  "services_total": 24,
    "clients_barber_total": 312,    "clients_market_total": 86,
    "clients_market_active": 14,    "income_unified": 221300.0,
    "expense_unified": 96800.0,     "sale_fund": 72000.0
  },

  "cash": {
    "totals": { "income": 36800.0, "expense": 24800.0, "net": 12000.0 },
    "by_cashbox": [
      { "cashbox_id": "6f1c…", "name": "Основная касса", "ops": 42,
        "income": 36800.0, "expense": 24800.0 }
    ]
  },

  "charts": {
    "weekday_appointments": [21, 19, 16, 22, 27, 18, 5],   // Пн … Вс
    "daily_cashflow": {
      "labels":  ["1", "2", "3", … "31"],
      "income":  [4200.0, 0.0, 7350.0, …],
      "expense": [800.0, 1200.0, 0.0, …]
    }
  },

  "rankings": {
    "masters":       [{ "master_id": "…", "master_name": "Алексей Ким", "count": 41, "revenue": 78400.0 }],
    "services":      [{ "service_id": "…", "name": "Стрижка", "count": 63, "revenue": 63000.0 }],
    "clients_visits":[{ "client_id": "…", "name": "Иван П.", "count": 4, "revenue": 6000.0 }],
    "clients_sales": [{ "client_id": "…", "name": "Пётр С.", "orders": 2, "revenue": 1200.0 }]
  },

  "bookings": {
    "statuses":     [{ "status": "confirmed", "label": "Подтверждена", "count": 17 }],
    "top_services": [{ "service_id": "…", "name": "Стрижка", "count": 8 }]
  },

  "products": {
    "sales_rows":     [{ "name": "Шампунь", "qty": 3.0, "revenue": 900.0 }],
    "suppliers_rows": [{ "supplier_id": "…", "name": "Поставщик А", "items": 2, "amount": 5000.0 }],
    "stock":          { "positions": 120, "total_qty": 450.0, "stock_value_retail": 89000.0 },
    "summary":        { "total_qty": 3.0, "total_revenue": 900.0 }
  },

  "details": {
    "income": [
      { "source": "Запись", "title": "Стрижка • Клиент: Иван • Мастер: Алексей",
        "amount": 500.0, "date": "15.08.2026" },
      { "source": "Касса", "title": "Оплата аренды", "amount": 1000.0, "date": "10.08.2026" }
    ],
    "expense": [
      { "source": "Выплаты мастерам", "title": "Период 2026-08",
        "amount": 72000.0, "date": "2026-08" }
    ]
  },

  "navigation": { "default_cashbox_id": "6f1c…" }
}
```

---

## 5. Поля по блокам

### `totals` — KPI верхней панели

| Поле | Тип | Что это |
|---|---|---|
| `appointments_total` | int | Все записи за период, любой статус |
| `appointments_completed` | int | Статус «Завершено». Знаменатель конверсии — `appointments_total` |
| `appointments_canceled` | int | Первая половина блока «Отменены и не пришёл» |
| `appointments_no_show` | int | Вторая половина того же блока |
| `revenue_completed` | number | Выручка завершённых записей с учётом скидки. Средний чек = `revenue_completed / appointments_completed` |
| `services_total` | int | Весь каталог услуг, **не** за период |
| `clients_barber_total` | int | Все клиенты барбершопа, не за период |
| `clients_market_total` | int | Клиенты продаж без поставщиков, не за период |
| `clients_market_active` | int | Сколько из них покупали в этом периоде |
| `income_unified` | number | KPI «Приход (месяц)» |
| `expense_unified` | number | KPI «Расход (месяц)» |
| `sale_fund` | number | Фонд выплат мастерам за `period.label` |

### `cash` — движения по кассам

Только операции со статусом «Успешно». Автоматическая запись `Выплаты мастерам YYYY-MM` из движений
**исключена** — она приходит отдельно в `totals.sale_fund`, иначе расход задвоится.

| Поле | Тип | Что это |
|---|---|---|
| `totals.income` / `expense` / `net` | number | Суммы за период, `net = income − expense` |
| `by_cashbox[].cashbox_id` | uuid | Для перехода на страницу кассы |
| `by_cashbox[].name` | string | Название кассы, пустое заменено на «Касса» |
| `by_cashbox[].ops` | int | Количество операций |
| `by_cashbox[].income` / `expense` | number | Суммы по этой кассе |

### `charts` — графики

| Поле | Тип | Что это |
|---|---|---|
| `weekday_appointments` | int[7] | Записи по дням недели, любой статус. **Индекс 0 = понедельник**, 6 = воскресенье |
| `daily_cashflow.labels` | string[] | Номера дней периода: `"1"` … `"31"` |
| `daily_cashflow.income` | number[] | По дням: выручка завершённых записей + приход по кассе |
| `daily_cashflow.expense` | number[] | По дням: расход по кассе, без выплат мастерам |

Все три массива `daily_cashflow` одной длины и выровнены по индексу — дни без движений это `0.0`,
а не пропуски.

### `rankings` — рейтинги

| Список | Сортировка | `count` / `orders` | `revenue` |
|---|---|---|---|
| `masters` | revenue ↓, затем count | Записи в статусах `booked`, `confirmed`, `completed`, `no_show` | Только `completed` |
| `services` | revenue ↓, затем count | Те же 4 статуса, уникальные записи | Только `completed` |
| `clients_visits` | revenue ↓ | Только `completed` | Сумма `completed` |
| `clients_sales` | revenue ↓ | Число продаж POS + продаж объектов | Сумма продаж |

Каждый список — максимум 10 строк. У мастера без имени в `master_name` придёт email,
а если и его нет — `—`.

### `bookings` — онлайн-заявки

| Поле | Тип | Что это |
|---|---|---|
| `statuses[].status` | string | Машинный код: `confirmed`, `no_show`, `spam`. Статус `new` в список не попадает |
| `statuses[].label` | string | Готовая русская подпись — свой словарь не нужен |
| `top_services[]` | array | Топ-5 услуг по числу заявок за период |
| `top_services[].service_id` | string | Ключ группировки. Обычно uuid услуги, но в старых заявках может оказаться название — используйте как строковый key, не приводите к uuid |

### `products` — товары

| Поле | Тип | Что это |
|---|---|---|
| `sales_rows[]` | array | Проданные товары: POS + продажи объектов, топ-50 по выручке |
| `suppliers_rows[]` | array | Приходы от поставщиков за период: `items` — позиций, `amount` — сумма закупки |
| `stock` | object | Текущий склад: `positions`, `total_qty`, `stock_value_retail`. **Не** фильтруется по периоду |
| `summary` | object | Итоги продаж за период по *всем* товарам, а не только по видимым топ-50 |

> **`summary` ≠ сумма `sales_rows`.** Строки обрезаны до 50, итоги посчитаны по всем.
> Не пересчитывайте итог на клиенте — берите `summary`.

### `details` — модалки «Приход» и «Расход»

| Поле | Формат | Что это |
|---|---|---|
| `source` | string | `"Запись"` · `"Касса"` · `"Выплаты мастерам"` |
| `title` | string | Готовая строка, разделитель — `•` |
| `amount` | number | Всегда положительное |
| `date` | string | `DD.MM.YYYY`, а у строки выплат мастерам — `YYYY-MM` |

Отсортировано новыми сверху. Каждый список — максимум 500 строк.

### `navigation`

`default_cashbox_id` — первая касса компании или филиала, для перехода по клику на KPI:
`/crm/kassa/{id}?tab=income|expense`. Может быть `null`, если касс нет — тогда KPI не кликабельны.

---

## 6. Формулы KPI

```
// приходит готовым
income_unified  = revenue_completed + cash.totals.income
expense_unified = sale_fund + cash.totals.expense

// считает фронт
profit     = income_unified - expense_unified
conversion = appointments_completed / appointments_total * 100
```

> **Делите на ноль осторожно.** За пустой месяц `appointments_total` и `appointments_completed`
> равны нулю — и конверсия, и средний чек требуют защиты на клиенте.

---

## 7. Лимиты и кэш

| Что | Предел | Зачем |
|---|---|---|
| Рейтинги `rankings.*` | 10 строк | Верхний блок страницы, больше не показывается |
| `bookings.top_services` | 5 строк | По спеке |
| `products.sales_rows`, `suppliers_rows` | 50 строк | Таблицы товаров |
| `details.income`, `details.expense` | 500 строк | Содержимое модалок |
| Длина периода | 366 дней | Больше — `400` |
| TTL кэша | 60 сек | Ключ: компания + филиал + период |

Свежую продажу или движение по кассе ответ может не показать до минуты. Если после действия
пользователя нужны мгновенные цифры — подождите или дайте кнопку «Обновить», а не считайте это багом.

---

## 8. Отличия от исходной спеки

Первые четыре — добавления, они не ломают маппер. Последние два меняют цифры.

| Пункт | В спеке | В реализации |
|---|---|---|
| `*_id` в рейтингах | Только имена в `clients_sales`, `suppliers_rows`, `by_cashbox` | Добавлены `client_id`, `supplier_id`, `cashbox_id` — стабильные ключи для React и переходы по клику |
| `clients_market_active` | Опционально | Возвращается всегда |
| `date_from` / `date_to` | Обязательные | Необязательные: без них берётся текущий месяц. Битая дата по-прежнему `400` |
| Лимиты строк | Не заданы | Топ-10 / 5 / 50 / 500, см. раздел 7 |
| Статусы POS-продаж | Не оговорены | Считаются `paid` **и** `debt`. Дата продажи — `paid_at`, а у долга `created_at`. Рыночная аналитика берёт только `paid`, поэтому цифры товаров могут разойтись с разделом «Маркет» |
| Приходы поставщиков | Движения из `main/products/list/` | Журнал оприходований (`SupplierReceipt`) — источник точнее, но суммы могут отличаться от того, что показывала старая страница |

### Филиалы

Скоуп по филиалу разный, и это осознанно:

- **Записи, услуги, клиенты, заявки** — строго по активному филиалу, ровно как в списках этих же
  сущностей. Цифры сходятся со страницами записей и услуг.
- **Кассы, движения, продажи, товары** — активный филиал *плюс* записи без филиала:
  общекомпанейская касса принадлежит всем, иначе она выпала бы из аналитики филиала.

---

## 9. Чек-лист интеграции

1. При открытии страницы уходит ровно один запрос `GET …/analytics/dashboard/` — старые вызовы
   appointments, bookings, employees, cashflows и detail-запросы POS удалены.
2. Смена месяца шлёт повторный запрос с новыми `date_from` / `date_to`.
3. KPI «Приход» и «Расход» берутся из `income_unified` / `expense_unified`, а не пересчитываются
   на клиенте.
4. График по дням недели начинается с понедельника: `weekday_appointments[0]` — это Пн.
5. Длина `daily_cashflow.labels` совпадает с длиной `income` и `expense`.
6. Итоги товаров берутся из `products.summary`, а не суммированием `sales_rows`.
7. Конверсия и средний чек защищены от деления на ноль.
8. Пустой месяц рендерится как нули и пустые состояния, без спиннера и без ошибки.
9. `403` показывает «Нет доступа к аналитике», `400` — текст ошибки из тела ответа.
10. Клик по KPI ведёт на `/crm/kassa/{default_cashbox_id}`, а при `null` KPI не кликабельны.

---

## 10. Legacy

`GET /barbershop/analytics/` (без `/dashboard/`) отдаёт частичные агрегаты и остаётся для обратной
совместимости. Для страницы «Аналитика» он больше не нужен.
