# Воронка продаж и лиды — документация для фронтенда

API консалтинга: **воронки продаж (funnels)**, **стадии (stages)** и **карточки лидов (leads)**.

Базовый префикс всех URL: `/api/consalting/`

---

## 1. Общие правила

### Авторизация
Все запросы требуют JWT-токен в заголовке:

```
Authorization: Bearer <access_token>
Content-Type: application/json
```

Без токена — `401 Unauthorized`.

### Компания и филиал (важно!)
- `company` и `branch` **проставляются сервером автоматически** из пользователя. Их **не нужно** (и нельзя) передавать в теле запроса — они приходят только на чтение.
- Если у сотрудника жёстко привязан филиал — он видит и создаёт записи только в своём филиале.
- Если филиала нет — видны все записи компании. Можно явно выбрать филиал через query-параметр `?branch=<uuid>` (если он не привязан жёстко).

### Формат ID
Все идентификаторы — **UUID** (например `c2f1e0a4-...`).

### Пагинация
Списки возвращаются в стандартном DRF-формате:

```json
{
  "count": 42,
  "next": "http://.../leads/?page=2",
  "previous": null,
  "results": [ ... ]
}
```

---

## 2. Быстрый сценарий (с чего начать)

1. Создать **воронку** → `POST /funnels/`
2. Добавить ей **стадии** → `POST /funnel-stages/` (для каждой стадии)
3. Создавать **лиды** в воронке → `POST /leads/`
4. Показывать **доску (канбан)** → `GET /funnels/<id>/board/`
5. Перетаскивание карточки между колонками → `POST /leads/<id>/move-stage/`

---

## 3. Воронки (Funnels)

### Список воронок
```
GET /api/consalting/funnels/
```
Фильтры: `?is_active=true`, `?branch=<uuid>`

Каждая воронка **сразу содержит свои стадии** (`stages`) и количество лидов (`leads_count`) — удобно для отрисовки выбора воронки и колонок.

**Ответ:**
```json
{
  "count": 1,
  "results": [
    {
      "id": "f1a2...",
      "company": "co-uuid",
      "branch": null,
      "name": "Основная воронка",
      "description": "",
      "is_active": true,
      "leads_count": 12,
      "stages": [
        {
          "id": "s1-uuid",
          "funnel": "f1a2...",
          "name": "Новые",
          "order": 0,
          "color": "#3498db",
          "is_final": false,
          "is_success": false,
          "leads_count": 5
        }
      ],
      "created_at": "2026-06-15T10:00:00Z",
      "updated_at": "2026-06-15T10:00:00Z"
    }
  ]
}
```

### Создать воронку
```
POST /api/consalting/funnels/
```
```json
{
  "name": "Основная воронка",
  "description": "Воронка для входящих заявок",
  "is_active": true
}
```
> `name` обязателен. Название уникально в рамках филиала/компании.

### Получить / изменить / удалить
```
GET    /api/consalting/funnels/<id>/
PUT    /api/consalting/funnels/<id>/      (полное обновление)
PATCH  /api/consalting/funnels/<id>/      (частичное)
DELETE /api/consalting/funnels/<id>/
```

---

## 4. Стадии воронки (Funnel Stages)

Стадия = колонка на доске. `company`/`branch` берутся **из воронки** автоматически.

### Список стадий
```
GET /api/consalting/funnel-stages/?funnel=<funnel_id>
```
Фильтры: `?funnel=`, `?is_final=`, `?is_success=`

### Создать стадию
```
POST /api/consalting/funnel-stages/
```
```json
{
  "funnel": "f1a2...",
  "name": "В работе",
  "order": 1,
  "color": "#f39c12",
  "is_final": false,
  "is_success": false
}
```

**Поля стадии:**
| Поле | Тип | Описание |
|---|---|---|
| `funnel` | uuid | Воронка (обязательно) |
| `name` | string | Название стадии |
| `order` | int | Порядок колонки слева направо. Уникален внутри воронки |
| `color` | string | HEX-цвет, напр. `#3498db` |
| `is_final` | bool | Финальная стадия (закрытие лида) |
| `is_success` | bool | Успешное закрытие (используется вместе с `is_final`) |

> Рекомендуемый набор стадий: `Новые` → `В работе` → `Переговоры` → `Успех` (is_final + is_success) / `Отказ` (is_final).

### Изменить / удалить
```
PATCH  /api/consalting/funnel-stages/<id>/
DELETE /api/consalting/funnel-stages/<id>/
```
> При удалении стадии лиды не удаляются — у них `stage` станет `null` (попадут в `unassigned`).

---

## 5. Лиды / карточки (Leads)

### Список лидов
```
GET /api/consalting/leads/
```
**Фильтры:** `?funnel=<id>`, `?stage=<id>`, `?owner=<user_id>`, `?client=<id>`, `?status=new|in_work|won|lost`, `?branch=<id>`

### Карточка лида (структура)
```json
{
  "id": "l1-uuid",
  "company": "co-uuid",
  "branch": null,

  "funnel": "f1a2...",
  "funnel_name": "Основная воронка",

  "stage": "s1-uuid",
  "stage_name": "Новые",
  "stage_color": "#3498db",

  "client": null,
  "client_display": null,

  "owner": "user-uuid",
  "owner_display": "Иван Петров",

  "title": "Заявка с сайта — внедрение CRM",
  "description": "Хотят автоматизировать продажи",

  "full_name": "Иван Иванов",
  "phone": "+996700123456",
  "email": "ivan@mail.com",

  "source": "Сайт",
  "estimated_value": "50000.00",
  "probability": 40,
  "status": "new",
  "closed_at": null,

  "created_at": "2026-06-15T10:05:00Z",
  "updated_at": "2026-06-15T10:05:00Z"
}
```

### Создать лид
```
POST /api/consalting/leads/
```
```json
{
  "funnel": "f1a2...",
  "stage": "s1-uuid",
  "title": "Заявка с сайта",
  "full_name": "Иван Иванов",
  "phone": "+996700123456",
  "email": "ivan@mail.com",
  "source": "Сайт",
  "estimated_value": 50000,
  "probability": 40,
  "client": null
}
```

**Поля при создании:**
| Поле | Обяз. | Описание |
|---|---|---|
| `funnel` | да | Воронка |
| `title` | да | Название лида (заголовок карточки) |
| `stage` | нет | Текущая стадия. Если не указать — карточка без стадии (`unassigned`) |
| `client` | нет | UUID клиента из `main.Client` (если лид уже привязан к клиенту) |
| `owner` | нет | Ответственный. Если не указать — **автоматически текущий пользователь** |
| `full_name`, `phone`, `email` | нет | Контактные данные карточки (когда клиента ещё нет) |
| `description`, `source` | нет | Текстовые поля |
| `estimated_value` | нет | Оценочная сумма (по умолчанию 0) |
| `probability` | нет | Вероятность 0–100 |
| `status` | нет | `new` (по умолч.), `in_work`, `won`, `lost` |

> Важно: `stage` должна принадлежать той же `funnel`, иначе `400`.

### Изменить / удалить
```
PATCH  /api/consalting/leads/<id>/
DELETE /api/consalting/leads/<id>/
```

---

## 6. Доска (канбан) — главный экран воронки

```
GET /api/consalting/funnels/<funnel_id>/board/
```

Возвращает воронку, **колонки по стадиям** (каждая со своими лидами) и список лидов без стадии. Один запрос — вся доска.

**Ответ:**
```json
{
  "funnel": {
    "id": "f1a2...",
    "name": "Основная воронка",
    "stages": [ ... ],
    "leads_count": 12
  },
  "columns": [
    {
      "stage": {
        "id": "s1-uuid",
        "name": "Новые",
        "order": 0,
        "color": "#3498db",
        "is_final": false,
        "is_success": false,
        "leads_count": 5
      },
      "leads": [ { ...карточка лида... } ]
    },
    {
      "stage": { "id": "s2-uuid", "name": "В работе", "order": 1, ... },
      "leads": [ ... ]
    }
  ],
  "unassigned": [ { ...лиды без стадии... } ]
}
```

Рендеринг на фронте:
- `columns` → колонки слева направо (уже отсортированы по `order`)
- `columns[].leads` → карточки внутри колонки
- `unassigned` → колонка «Без стадии» (опционально)

---

## 7. Перемещение лида между стадиями (drag & drop)

```
POST /api/consalting/leads/<lead_id>/move-stage/
```
```json
{ "stage": "s2-uuid" }
```

**Что делает сервер автоматически:**
- меняет `stage` лида;
- если стадия `is_final` и `is_success` → `status = "won"`, `closed_at = now`;
- если стадия `is_final` и не `is_success` → `status = "lost"`, `closed_at = now`;
- если стадия не финальная, а лид был закрыт → возвращает `status = "in_work"`, `closed_at = null`.

**Ответ** — обновлённая карточка лида (та же структура, что в разделе 5).

Ошибка, если стадия из другой воронки:
```json
{ "stage": "Стадия относится к другой воронке." }   // 400
```

### Рекомендованный UX drag&drop
1. Пользователь перетащил карточку в другую колонку.
2. Оптимистично переместить карточку в UI.
3. Отправить `POST .../move-stage/` с `stage` = id колонки.
4. На `200` — заменить карточку данными из ответа (там уже обновлён `status`/`closed_at`).
5. На ошибке — вернуть карточку обратно и показать сообщение.

---

## 8. Коды ответов и ошибки

| Код | Когда |
|---|---|
| `200` | Успех (GET, PATCH, move-stage) |
| `201` | Создано (POST) |
| `204` | Удалено (DELETE) |
| `400` | Ошибка валидации (см. тело — `{ "поле": ["сообщение"] }`) |
| `401` | Нет/неверный токен |
| `403` | Нет доступа / у пользователя не настроена компания |
| `404` | Объект не найден (или принадлежит другой компании/филиалу) |

Пример тела ошибки валидации:
```json
{
  "stage": ["Стадия относится к другой воронке."],
  "client": ["Клиент принадлежит другой компании."]
}
```

---

## 9. Шпаргалка по эндпоинтам

| Метод | URL | Назначение |
|---|---|---|
| GET / POST | `/api/consalting/funnels/` | список / создание воронок |
| GET / PATCH / DELETE | `/api/consalting/funnels/<id>/` | воронка |
| **GET** | `/api/consalting/funnels/<id>/board/` | **доска (канбан)** |
| **GET** | `/api/consalting/funnels/<id>/analytics/` | **аналитика воронки** |
| GET / POST | `/api/consalting/funnel-stages/` | стадии (фильтр `?funnel=`) |
| GET / PATCH / DELETE | `/api/consalting/funnel-stages/<id>/` | стадия |
| GET / POST | `/api/consalting/leads/` | лиды (фильтры funnel/stage/owner/status…) |
| GET / PATCH / DELETE | `/api/consalting/leads/<id>/` | карточка лида |
| **POST** | `/api/consalting/leads/<id>/move-stage/` | **переместить лид в стадию** |
| GET | `/api/consalting/leads/<id>/allowed-transitions/` | разрешённые переходы |
| GET | `/api/consalting/leads/<id>/timeline/` | лента активностей |
| POST | `/api/consalting/leads/<id>/activities/` | добавить активность |
| POST | `/api/consalting/leads/<id>/recalculate-score/` | пересчитать скоринг |
| POST | `/api/consalting/leads/<id>/win/` | закрыть успешно |
| POST | `/api/consalting/leads/<id>/lose/` | закрыть с потерей |
| GET / POST | `/api/consalting/lead-tasks/` | задачи по лидам |
| GET / PATCH / DELETE | `/api/consalting/lead-tasks/<id>/` | задача |
| GET / POST | `/api/consalting/loss-reasons/` | причины проигрыша |
| GET / PATCH / DELETE | `/api/consalting/loss-reasons/<id>/` | причина |

---

# ЧАСТЬ 2. Воронка 2.0 — расширенные возможности

Базовая часть (выше) описывает CRUD воронок/стадий/лидов. Ниже — апгрейд: типы стадий,
машина состояний, скоринг, обязательное «следующее действие», лента событий, задачи,
закрытие win/lose, аналитика. **Все базовые поля и эндпоинты продолжают работать.**

---

## 10. Стадии: семантический тип (`stage_type`)

У стадии теперь есть **тип** — он управляет логикой переходов и аналитикой,
независимо от названия колонки. Названия/порядок/цвет настраиваете как угодно — логика
завязана на `stage_type`.

| `stage_type` | Смысл | Терминальная? |
|---|---|---|
| `new_lead` | Новый лид | нет |
| `first_contact` | Первый контакт | нет |
| `qualification` | Квалификация | нет |
| `nurture` | Прогрев / в работе | нет |
| `proposal_sent` | КП отправлено | нет |
| `negotiation` | Переговоры | нет |
| `decision_pending` | Ожидание решения | нет |
| `won` | Выиграно / оплачено | да (успех) |
| `onboarding` | Онбординг | нет |
| `completed` | Завершено | да (успех) |
| `lost` | Потеряно | да (провал) |

Дополнительные поля стадии (POST/PATCH `/funnel-stages/`):
- `stage_type` — один из выше (по умолчанию `new_lead`);
- `allowed_next` — массив `stage_type`, переопределяет матрицу переходов (пусто = дефолтная);
- `required_fields` — массив имён полей лида, обязательных перед уходом со стадии;
- `sla_hours` — через сколько часов в стадии лид помечается «под риском»;
- `allow_skip` — `true` разрешает прыжки через стадии.
- `is_final` / `is_success` — **read-only**, выводятся из `stage_type`.

> Готовый набор из 11 стадий создаётся сидом: `seed_consalting_funnel --with-funnel`.

---

## 11. Расширенная карточка лида

`GET /leads/<id>/` теперь возвращает (в дополнение к базовым полям):

```json
{
  "...": "базовые поля из части 1",
  "stage_type": "proposal_sent",

  "score_grade": "A",
  "score_value": 100,
  "score_updated_at": "2026-06-15T10:00:00Z",
  "budget_confirmed": true,
  "urgency": "high",
  "decision_maker_engaged": true,
  "avg_response_minutes": 12,

  "next_action_type": "call",
  "next_action_date": "2026-06-16T09:00:00Z",
  "next_action_note": "Перезвонить по КП",

  "is_at_risk": false,
  "risk_reason": "",
  "last_activity_at": "2026-06-15T09:30:00Z",
  "stage_entered_at": "2026-06-14T12:00:00Z",

  "loss_reason": null,
  "loss_reason_label": null,
  "loss_comment": "",

  "first_contact_at": "2026-06-10T08:00:00Z",
  "won_at": null,
  "lost_at": null,
  "completed_at": null
}
```

**Что писать с фронта (PATCH `/leads/<id>/`):** `budget_confirmed`, `urgency`
(`low|medium|high`), `decision_maker_engaged`, `avg_response_minutes`,
`next_action_type` (`call|message|meeting|follow_up`), `next_action_date`,
`next_action_note`, `loss_reason`, `loss_comment`.

**Только чтение:** `score_*`, `is_at_risk`, `risk_reason`, `last_activity_at`,
`stage_entered_at`, `won_at`, `lost_at`, `completed_at`, `stage_type`.

### Рабочий список менеджера
```
GET /leads/?score_grade=A&is_at_risk=true&ordering=next_action_date
```
Сортируйте по `next_action_date`, выделяйте `is_at_risk` и грейд `A`.

---

## 12. Lead scoring (A / B / C)

Скоринг считается из факторов: `budget_confirmed` (25), `urgency` (high 20 / medium 10),
`decision_maker_engaged` (20), быстрый ответ `avg_response_minutes ≤ 30` (15),
размер сделки `estimated_value` (до 20). Итог 0–100 → грейд: **A ≥ 70, B ≥ 40, C < 40**.

Пересчитать вручную (после изменения факторов он и так пересчитывается, но можно явно):
```
POST /leads/<id>/recalculate-score/   →  обновлённая карточка лида
```
UI: показывать бейдж грейда (A — красный/горячий, B — жёлтый, C — серый), сортировать
очередь по грейду + `next_action_date`.

---

## 13. Машина состояний: переходы и `move-stage`

`move-stage` теперь идёт через машину состояний. По умолчанию работает **мягкий режим**:
недопустимый переход всё равно выполняется, но нарушения записываются в timeline
(`payload.soft_violations`). В строгом режиме (настройка сервера `CONSALTING_FUNNEL_STRICT=true`)
вернётся `400`.

**Перед перетаскиванием карточки спросите доступные колонки:**
```
GET /leads/<id>/allowed-transitions/
```
```json
{
  "current_stage": "s-uuid",
  "allowed": [
    {"id":"s2","name":"Переговоры","stage_type":"negotiation","order":5,"color":"#9b59b6"},
    {"id":"s9","name":"Потеряно","stage_type":"lost","order":10,"color":"#e74c3c"}
  ]
}
```
Подсвечивайте только эти колонки как валидные для дропа.

**Правила, которые проверяет сервер** (в строгом режиме → 400, в мягком → лог):
- нельзя назад из `won`/`completed`;
- нельзя прыгать через стадии (если у стадии `allow_skip=false`);
- `→ proposal_sent`: нужен `estimated_value > 0`;
- `→ won`: нужен `budget_confirmed=true` (эндпоинт `/win/` ставит сам);
- `→ lost`: нужна `loss_reason` (эндпоинт `/lose/` ставит сам);
- переход в активную стадию: должны быть заданы `next_action_type` + `next_action_date`.

Ответ при запрете (строгий режим):
```json
{ "detail": "Переход запрещён", "errors": [
  "Недопустимый переход: Новый лид → Оплачено / выиграно.",
  "В активной стадии нужны next_action_type и next_action_date." ] }
```

При переходе сервер сам синхронизирует `status`, `won_at/lost_at/completed_at`,
`stage_entered_at` и пишет запись в timeline + лог переходов.

---

## 14. Закрытие сделки: win / lose

Не двигайте карточку в финал руками — используйте спец-эндпоинты (они проставят
обязательные поля и запустят автоматизацию).

**Выиграть:**
```
POST /leads/<id>/win/
{ "stage": "<won-stage-id>"? }      // stage можно не указывать — возьмётся первая WON-стадия
```
Ставит `budget_confirmed=true`, переводит в WON, `won_at=now`, `status=won`.

**Проиграть (причина обязательна):**
```
POST /leads/<id>/lose/
{ "loss_reason": "<id>", "loss_comment": "клиент выбрал конкурента", "stage": "<lost-stage>"? }
```
Переводит в LOST, `lost_at=now`, `status=lost`.

---

## 15. Причины проигрыша (`loss-reasons`)

Справочник на уровне компании. Заполните список один раз — фронт показывает его при
закрытии в LOST.
```
GET  /loss-reasons/?is_active=true
POST /loss-reasons/   { "code": "price_high", "label": "Дорого" }
```
> Дефолтные причины создаются сидом (`price_high`, `no_response`, `competitor`,
> `not_relevant`, `timing`).

---

## 16. Лента активностей (timeline)

История по лиду (звонки, заметки, сообщения, смены стадий, скоринг, автоматизация).
Лента **неизменяема** — только чтение и добавление.

```
GET /leads/<id>/timeline/        // пагинация, сортировка от новых к старым
```
```json
{
  "count": 5,
  "results": [
    {"id":"a1","type":"stage_change","title":"Стадия: Квалификация → КП отправлено",
     "actor":"u1","actor_display":"Иван Петров","payload":{"to_type":"proposal_sent"},
     "created_at":"2026-06-15T10:00:00Z"},
    {"id":"a2","type":"call","title":"Звонок","body":"Обсудили условия","created_at":"..."}
  ]
}
```

**Добавить активность** (фронт может создавать только «контактные» типы):
```
POST /leads/<id>/activities/
{ "type": "call", "title": "Звонок клиенту", "body": "Договорились о встрече" }
```
Допустимые `type`: `note`, `call`, `message`, `email`, `meeting`, `file`.
Типы `stage_change` / `score_change` / `automation` / `system` создаёт только сервер.

> Любая контактная активность обновляет `last_activity_at` и **снимает флаг `is_at_risk`**.

---

## 17. Задачи по лидам (follow-up)

```
GET  /lead-tasks/?lead=<id>&status=open
POST /lead-tasks/
{ "lead":"<id>", "type":"call", "title":"Перезвонить", "due_date":"2026-06-17T09:00:00Z",
  "assignee":"<user>"? }
```
Создание задачи **обновляет `next_action_*`** лида (задача = следующий шаг).

**Завершить задачу:**
```
PATCH /lead-tasks/<id>/   { "status": "done" }     // completed_at проставится сервером
```
Статусы: `open`, `done`, `overdue` (ставит сервер при просрочке), `canceled`.

> Часть задач создаёт автоматизация (`created_by_automation: true`) — например follow-up
> после отправки КП.

---

## 18. Аналитика воронки

```
GET /funnels/<id>/analytics/?date_from=&date_to=&branch=&owner=
```
```json
{
  "funnel_id": "f1", "funnel_name": "Основная воронка",
  "totals": {
    "deals": 320, "pipeline_value": 4250000.0,
    "won": 58, "lost": 92, "win_rate": 0.387,
    "avg_cycle_days": 14.2, "at_risk": 12
  },
  "stages": [
    {"stage_id":"s2","name":"Квалификация","stage_type":"qualification",
     "count":40,"value":520000.0,"avg_hours_in_stage":36.5,
     "entered":120,"conversion_to_next":0.62,"drop_off_rate":0.18}
  ],
  "by_loss_reason": [{"code":"price_high","label":"Дорого","count":31}],
  "by_score": {"A":45,"B":120,"C":155}
}
```
Для дашборда: воронка по `stages[].count`, узкие места по `conversion_to_next` /
`drop_off_rate`, причины оттока — `by_loss_reason`, распределение качества — `by_score`.

---

## 19. Автоматизация и уведомления

Правила автоматизации настраиваются **на сервере** (Django-admin / сид), отдельного
API для их CRUD на фронте нет. Что делает автоматика (видно по timeline и задачам):
- нет активности > 24ч → лид «под риском» (`is_at_risk=true`) + уведомление;
- КП отправлено → авто-задача follow-up;
- выигрыш → задача онбординга;
- просроченная задача → «под риском» + уведомление.

**Real-time:** сервер шлёт события в WS-группы `consalting_user_<owner_id>` и
`consalting_company_<company_id>` (события `stage_changed`, `lead_won`, `lead_lost`,
`no_activity`, `sla_breach`, `notify`). Подключите WebSocket и подпишитесь на свою группу,
чтобы обновлять доску и показывать пуши без перезагрузки.

---

## 20. Рекомендованные экраны

| Экран | Эндпоинты |
|---|---|
| Канбан-доска | `GET /funnels/<id>/board/` + `move-stage` + `allowed-transitions` |
| Карточка лида | `GET /leads/<id>/` + `timeline/` + `lead-tasks/?lead=` |
| Очередь менеджера | `GET /leads/?score_grade=A&is_at_risk=true&ordering=next_action_date` |
| Закрытие сделки | `POST /win/` или `POST /lose/` (+ `loss-reasons/`) |
| Дашборд | `GET /funnels/<id>/analytics/` |
