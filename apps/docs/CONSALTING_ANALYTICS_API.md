# Консалтинг · Аналитика — API для фронтенда

Все эндпоинты: `Authorization: Bearer <JWT>`, компания берётся из токена.
Общие параметры: `date_from` / `date_to` (либо `period_start` / `period_end`,
формат `YYYY-MM-DD`), `branch`. Период по умолчанию — **последние 30 дней**.

| Эндпоинт | Что показывает |
|---|---|
| `GET /api/consalting/analytics/dashboard/` | Всё главное + сравнение с прошлым периодом |
| `GET /api/consalting/analytics/messenger/` | Переписка WhatsApp: скорость ответа, объём, неотвеченные |
| `GET /api/consalting/analytics/sources/` | Источники заявок и их конверсия |
| `GET /api/consalting/analytics/managers/` | Нагрузка и результативность сотрудников |
| `GET /api/consalting/analytics/` | Продажи (было раньше) |
| `GET /api/consalting/funnels/<id>/analytics/` | Воронка по стадиям (было раньше) |

---

## 1. Дашборд — `/analytics/dashboard/`

Одна точка для главного экрана. Каждый KPI приходит **с динамикой**:

```json
{
  "period":         { "date_from": "2026-06-28", "date_to": "2026-07-27" },
  "compare_period": { "date_from": "2026-05-29", "date_to": "2026-06-27" },
  "kpis": {
    "revenue":              { "current": 62200.0, "previous": 33244.0, "diff": 28956.0, "percent": 87.1 },
    "paid_income":          { "...": "фактически полученные деньги" },
    "sales_count":          {}, "avg_check": {}, "subscription_mrr": {},
    "leads": {}, "requests": {}, "messages": {}, "avg_response_minutes": {}
  },
  "leads":     { "total": 19, "won": 4, "lost": 0, "in_work": 15, "win_rate": 1.0,
                 "pipeline_value": 76500.0, "at_risk": 0 },
  "messenger": { "totals": {}, "response": {}, "waiting_now": 10, "by_day": [], "by_hour": [] },
  "sources":   { "totals": {}, "by_status": {}, "by_source": [] },
  "sales":     { "by_day": [], "by_service": [], "by_employee": [] },
  "managers":  [ "…топ-10" ]
}
```

`percent` — рост в % к прошлому периоду той же длины (для стрелок ↑/↓).
Для `avg_response_minutes` **меньше = лучше**: рост этого показателя красьте
негативно, в отличие от остальных.

---

## 2. Мессенджер — `/analytics/messenger/`

Дополнительно принимает `owner=<user_id>`.

```json
{
  "totals":   { "messages": 573, "inbound": 287, "outbound": 286, "chats": 13,
                "failed": 22, "failure_rate": 0.077 },
  "response": { "avg_minutes": 28.0, "median_minutes": 1.2,
                "answered_chats": 5, "never_answered_chats": 8, "answer_rate": 0.385 },
  "waiting_now": { "count": 10, "items": [
      { "lead_id": "…", "name": "Клиент", "phone": "+996…", "owner": "Иван",
        "last_message_at": "…", "waiting_minutes": 143 } ] },
  "by_day":  [ { "date": "2026-07-27", "inbound": 283, "outbound": 285, "total": 568 } ],
  "by_hour": [ { "hour": 0, "inbound": 39 }, "… 24 элемента, 0–23" ],
  "by_operator": [ { "user_id": "…", "name": "…", "inbound": 33, "outbound": 71,
                     "answered": 1, "avg_response_minutes": 0.3 } ]
}
```

Как читать:
- **`response.median_minutes`** — честнее среднего: медиана не искажается одним
  диалогом, на который ответили через сутки. Показывайте оба.
- **`never_answered_chats`** и **`answer_rate`** — сколько диалогов вообще не
  получили ответа за период. Низкий `answer_rate` — прямая потеря денег.
- **`waiting_now`** — диалоги, где последнее сообщение клиентское и висит
  дольше 15 минут. Это рабочий список «ответить сейчас», не историческая метрика
  (считается по всей базе, не по периоду). `items` — максимум 50, отсортированы
  от самых давних.
- **`failure_rate`** — доля неотправленных исходящих (проблемы канала/номеров).
- **`by_hour`** — когда клиенты пишут; по нему планируют смены операторов.
- **`by_operator`** — атрибуция по **ответственному за лида**, а не по автору
  сообщения (в сообщениях автор не хранится). Строка с `user_id: null` — это
  диалоги без назначенного ответственного.

---

## 3. Источники — `/analytics/sources/`

```json
{
  "totals":    { "requests": 76, "linked_leads": 76, "won": 1,
                 "conversion_to_lead": 1.0, "conversion_to_won": 0.013 },
  "by_status": { "new": 64, "assigned": 7, "in_work": 5, "converted": 0, "rejected": 0 },
  "by_source": [ { "source": "Wazzup (whatsapp)", "count": 76, "linked_leads": 76,
                   "converted": 0, "rejected": 0, "won": 1,
                   "conversion_to_lead": 1.0, "conversion_to_won": 0.013, "share": 100.0 } ],
  "by_day":    [ { "date": "2026-07-27", "count": 71 } ]
}
```

Воронка: **заявка → карточка лида → выигранная сделка**. `share` — доля
источника в процентах от всех заявок.

---

## 4. Менеджеры — `/analytics/managers/`

```json
{ "managers": [
    { "user_id": "…", "name": "Иван Иванов", "leads": 4, "won": 2, "lost": 0,
      "win_rate": 1.0, "at_risk": 0, "pipeline_value": 29500.0,
      "messages_out": 71, "avg_response_minutes": 0.3 } ] }
```

Отсортировано по числу лидов. `user_id: null` — лиды без ответственного
(их стоит показывать отдельной строкой «Не распределено»).

---

## Замечания по отображению

1. **Пустой период — не ошибка.** Если данных нет, приходят нули и `null` в
   средних. `null` рисуйте как «—», а не как `0`.
2. **Проценты приходят долями** (`0.385`), кроме `share` и `percent` — они уже
   в процентах. Не умножайте дважды.
3. **Деньги** — числа, без валюты; валюту берите из настроек компании.
4. Тяжёлые срезы (`dashboard`) считают несколько периодов сразу — не дёргайте
   их на каждый ввод символа в фильтре, ставьте debounce.
