# Консалтинг · Wazzup/WhatsApp чат — гайд для фронтенда

Как правильно работать с realtime-чатом воронки консалтинга после перевода на
асинхронную модель. Соблюдение контракта дедупликации ниже — обязательно, иначе
вернутся дубликаты и «прыгающие» сообщения.

---

## 1. Модель в двух словах

- Каждое сообщение приходит по WebSocket **ровно один раз**.
- **Своё исходящее сообщение отправитель по сокету НЕ получает.** У отправителя
  уже есть локальный ответ (ack сокета или HTTP-ответ REST) — по нему и рисуем
  пузырь. Другие сотрудники компании получат это сообщение обычным `new_message`.
- Отправка не блокирует: сервер сразу отвечает `pending`, реальная доставка в
  Wazzup идёт в фоне, финальный статус прилетает событием `message_status`.

**Главное правило:** ключуйте сообщения по `data.id` и делайте **upsert** (не
append). Тогда оптимистичный пузырь, статусы и любые повторы схлопнутся в одну
запись.

---

## 2. Подключение по WebSocket

Аутентификация — JWT access-токен, через query `?token=` (или заголовок
`Authorization: Bearer <token>`, если умеете задавать заголовки на WS).

Два консьюмера:

| Назначение | URL |
|---|---|
| **Чат** (приём + отправка сообщений) | `wss://app.nurcrm.kg/ws/wazzup/` или `wss://app.nurcrm.kg/ws/wazzup/chat/<chat_id>/` |
| **Доска воронки** (карточки лидов + те же чат-события + уведомления) | `wss://app.nurcrm.kg/ws/consalting/funnel/?token=<JWT>` |

- Для чат-экрана используйте `ws/wazzup/`. Если открыт конкретный диалог —
  `ws/wazzup/chat/<chat_id>/`, где `chat_id` — **номер только из цифр** (`77001234567`).
- Оба консьюмера доставляют чат-события (`new_message`, `message_status`) в
  одинаковом формате, так что канбан-доска тоже видит сообщения.

```js
const token = getAccessToken();
const ws = new WebSocket(`wss://app.nurcrm.kg/ws/wazzup/chat/77001234567/?token=${token}`);
```

### Пинг для удержания соединения
Раз в ~25–30 сек шлите `{"action":"ping"}` — в ответ придёт `{"action":"pong"}`.

---

## 3. Входящие события (сервер → клиент)

Все события — JSON вида `{ "type": "...", "data": {...} }`.

### 3.1 `new_message` — новое сообщение (входящее или чужое исходящее)

```json
{
  "type": "new_message",
  "data": {
    "id": "b0f3…",                 // ← КЛЮЧ дедупликации
    "message_id": "wamid…",         // id со стороны Wazzup
    "lead_id": "7c2a…",
    "chat_id": "+77001234567",      // ⚠️ формат непостоянен, см. §6
    "text": "Здравствуйте!",
    "content_uri": null,             // ссылка на медиа (или null)
    "contentUri": null,              // дубль ключа (совместимость)
    "media_type": null,              // "image" | "video" | "audio" | "voice" | "document" | null
    "type": "text",                  // media_type или "text"
    "is_incoming": true,
    "direction": "inbound",          // "inbound" | "outbound"
    "status": "read",
    "timestamp": "2026-07-27T17:40:11.064+06:00",
    "contact_name": "Иван Иванов"
  }
}
```

Действие: **upsert по `data.id`** в списке сообщений соответствующего чата
(находите чат по `lead_id` или по нормализованному `chat_id`, см. §6).

### 3.2 `message_status` — смена статуса исходящего

```json
{
  "type": "message_status",
  "data": {
    "id": "b0f3…",          // тот же id, что вернул ack/REST при отправке
    "message_id": "wamid…", // серверный id Wazzup (появляется после доставки)
    "lead_id": "7c2a…",
    "status": "sent",       // "pending" → "sent" | "failed" | "delivered" | "read"
    "timestamp": "…"
  }
}
```

Действие: найти сообщение по `data.id` и обновить `status` (не создавать новое).

### 3.3 `connection_established` — только на `ws/consalting/funnel/`

```json
{ "type": "connection_established", "company_id": "…", "branch_id": "…|null", "user_id": "…", "is_manager": true }
```

### 3.4 События доски (канбан) — на `ws/consalting/funnel/`

`{ "type": "lead.created" | "lead.updated" | "lead.claimed" | "lead.released" | "lead.deleted" | "lead.removed", "data": {…lead…} }`.
К чату не относятся — используйте для обновления карточек воронки.

---

## 4. Отправка сообщения

Два способа. **Оба возвращают отправителю его сообщение — рисуйте пузырь из
ответа, по сокету дубликат себе НЕ прилетит.**

### 4.1 Через WebSocket (рекомендуется для чат-экрана)

Запрос:
```json
{ "action": "send_message", "lead_id": "7c2a…", "text": "Ваш ответ", "content_uri": null, "account_id": null }
```
- `account_id` можно не передавать — возьмётся активный аккаунт Wazzup компании.
- Для медиа передайте `content_uri` (URL, полученный из upload, см. §5.3).

Ответ (ack):
```json
{ "action": "send_message_ack", "status": "success",
  "data": { "id": "b0f3…", "message_id": "wz_out_…", "status": "pending", "text": "Ваш ответ", "lead_id": "7c2a…" } }
```
При ошибке:
```json
{ "action": "send_message_ack", "status": "error", "detail": "текст ошибки" }
```

Действие: по ack добавляем пузырь с `id = data.id`, `status="pending"`. Дальше
`pending → sent/failed` придёт событием `message_status` с тем же `id`.

### 4.2 Через REST

```
POST /api/consalting/wazzup-accounts/<account_id>/send-message/
Authorization: Bearer <JWT>
Content-Type: application/json

{ "lead_id": "7c2a…", "message": "Ваш ответ", "content_uri": null }
```
Ответ `201`:
```json
{ "id": "b0f3…", "message_id": "wz_out_…", "status": "pending", "text": "Ваш ответ" }
```
Дальше — так же реагируем на `message_status` по `id`.

> Тело: принимаются `message` или `text`; медиа — `content_uri` / `contentUri` /
> `media_url` / `file_url`. Можно слать `multipart/form-data` с файлом — сервер
> сам зальёт и подставит `content_uri`.

---

## 5. REST-эндпоинты чата

Все — под `Authorization: Bearer <JWT>`, компания берётся из токена.

### 5.1 Список чатов/диалогов
```
GET /api/consalting/chats/        (алиас: /api/consalting/wazzup-chats/)
```

### 5.2 История сообщений лида
```
GET /api/consalting/wazzup-messages/?lead=<lead_id>
    (алиасы: /whatsapp-messages/ , /messages/ ; параметр lead или lead_id)
```
Отсортировано по `created_at`, пагинации нет. Грузим при открытии диалога, затем
живём на сокете.

### 5.3 Загрузка медиа (получить `content_uri` перед отправкой)
```
POST /api/consalting/wazzup-accounts/<account_id>/upload/   (multipart: file=<...>)
→ 201 { "url": "https://…", "content_uri": "https://…" }
```

---

## 6. Важные нюансы (обязательно к учёту)

1. **Дедуп по `id`, upsert.** `new_message` может прийти и как оптимистичный, и
   как подтверждённый — совпадающий `id` позволяет заменить, а не задвоить.
2. **Своё сообщение по сокету не приходит.** Источник своего пузыря — ответ
   отправки (ack/REST). Если рисуете пузырь только по сокет-событию — своё
   сообщение не появится. Всегда рендерите из ответа отправки.
3. **`chat_id` формат непостоянен**: у входящих `"+77001234567"`, у исходящих и в
   `message_status` — `"77001234567"`. **Нормализуйте**: `chatId.replace(/\D/g,'')`
   при группировке сообщений по чату. Надёжнее матчить по `lead_id`.
4. **`status`**: `pending → sent → delivered → read`, либо `failed`. Показывайте
   часики на `pending`, галочки/ошибку по остальным.
5. **Медиа**: если `text` пустой, а есть `content_uri` — рендерите вложение по
   `media_type`; сервер также кладёт человекочитаемую метку в `text`
   (`📷 [Фотография]` и т.п.) как фолбэк.
6. **Реконнект**: при обрыве переподключайтесь и **дозагрузите историю по REST**
   (§5.2) — события, пришедшие пока сокет был закрыт, так не теряются. Дедуп по
   `id` уберёт возможные пересечения.

---

## 7. Мини-псевдокод

```js
const byId = new Map(); // id -> message

function upsert(m) {
  const prev = byId.get(m.id) || {};
  byId.set(m.id, { ...prev, ...m });
  render();
}

ws.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.action === "pong") return;
  if (ev.action === "send_message_ack") {
    if (ev.status === "success") upsert({ ...ev.data, mine: true });
    else toastError(ev.detail);
    return;
  }
  switch (ev.type) {
    case "new_message":     upsert(ev.data); break;         // чужие/входящие
    case "message_status":  upsert(ev.data); break;         // обновит status по id
    // lead.* — события доски, к чату не относятся
  }
};

async function send(leadId, text) {
  ws.send(JSON.stringify({ action: "send_message", lead_id: leadId, text }));
  // пузырь появится из ack (status: "pending"), статус догонит message_status
}
```
