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
| GET / POST | `/api/consalting/funnel-stages/` | стадии (фильтр `?funnel=`) |
| GET / PATCH / DELETE | `/api/consalting/funnel-stages/<id>/` | стадия |
| GET / POST | `/api/consalting/leads/` | лиды (фильтры funnel/stage/owner/status) |
| GET / PATCH / DELETE | `/api/consalting/leads/<id>/` | карточка лида |
| **POST** | `/api/consalting/leads/<id>/move-stage/` | **переместить лид в стадию** |
