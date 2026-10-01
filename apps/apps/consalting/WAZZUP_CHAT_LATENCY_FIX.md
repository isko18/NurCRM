# Wazzup chat latency fix for `apps/consalting`

## Цель

Убрать заметную задержку в Wazzup-чате консалтинга. Входящее сообщение из
Wazzup должно появляться в открытом чате почти сразу после webhook, а исходящее
сообщение менеджера должно появляться у отправителя сразу из `ack`/REST-ответа
со статусом `pending`, без ожидания ответа внешнего Wazzup API.

Целевые метрики:

- входящее сообщение: webhook -> WebSocket `new_message` до 300-500 мс при
  нормальной нагрузке;
- исходящее сообщение: клик "отправить" -> локальный пузырь/`ack` до 100-300 мс;
- статусы `sent`, `delivered`, `read`, `failed`: обновление через WebSocket
  `message_status`, без обязательного REST-перезагрузки чата;
- список чатов не должен грузить все сообщения компании в память.

## Как сейчас устроено

Основные точки кода:

- `wazzup_views.py`
  - `WazzupWebhookConsaltingView.post()` принимает webhook Wazzup и вызывает
    `WazzupConsaltingService.enqueue_webhook(payload)`.
  - `WazzupAccountConsaltingViewSet.send_message()` отправляет исходящее
    сообщение через сервис.
  - `WazzupChatListView.get()` собирает список диалогов.
- `funnel/wazzup.py`
  - `_broadcast_consalting_message()` шлет `new_message` в группы Channels.
  - `_broadcast_message_status()` шлет `message_status`.
  - `send_message()` создает исходящее сообщение со статусом `pending` и ставит
    Celery-задачу реальной отправки в Wazzup.
  - `enqueue_webhook()` кладет входящий webhook в Celery.
  - `handle_wazzup_webhook()` в Celery создает/обновляет `InboundLeadConsalting`,
    `LeadConsalting`, `WhatsAppMessageConsalting`, активности, уведомления и
    WebSocket-события.
- `tasks.py`
  - `process_wazzup_webhook()` вызывает `handle_wazzup_webhook()`.
  - `send_wazzup_message()` делает внешний `POST /v3/message` в Wazzup.
- WebSocket:
  - `/ws/wazzup/` и `/ws/wazzup/chat/<chat_id>/` реализованы в
    `apps/crm/wazzup/consumers.py`, но умеют отправлять сообщения консалтинга.
  - `/ws/consalting/funnel/` реализован в `apps/consalting/consumers.py` и тоже
    получает события Wazzup.

Важное ограничение: Wazzup сам не подключается к нашему WebSocket. Wazzup -> NurCRM
идет только через HTTP webhook. WebSocket нужен только для NurCRM -> фронтенд.

## Главная причина задержки

Входящий webhook сейчас не обрабатывается в HTTP-запросе. Метод
`WazzupWebhookConsaltingView.post()` только ставит задачу `process_wazzup_webhook`
в Celery и сразу возвращает `200`.

Это защищает webhook от таймаутов, но realtime чата становится зависимым от:

- очереди Redis/Celery;
- свободного Celery worker;
- общей нагрузки от других задач;
- корректной изоляции broker между окружениями.

Из-за этого комментарии вида "мгновенная трансляция" в `funnel/wazzup.py` сейчас
не означают мгновенность для входящих сообщений: broadcast выполняется только
после того, как Celery реально начал задачу.

## Дополнительные проблемы

1. `message_status` из webhook почти не доходит в чат

   В `handle_wazzup_webhook()` блок `statuses` обновляет статус сообщения и шлет
   `realtime.lead_updated(msg.lead)`, но не вызывает `_broadcast_message_status()`.
   Поэтому `delivered/read/failed`, пришедшие от Wazzup webhook, могут появиться в
   UI только после REST-перезагрузки.

2. Broadcast делается внутри транзакции

   И входящие, и исходящие сообщения могут отправить WebSocket-событие до commit
   транзакции. Клиент уже получил `new_message`, но запись еще может быть не видна
   в REST-истории или транзакция может откатиться. Все рассылки по сообщению нужно
   вызывать через `transaction.on_commit(...)`.

3. Список чатов грузит слишком много данных

   `WazzupChatListView.get()` собирает все лиды, все входящие заявки и все
   сообщения компании, затем проходит по ним в Python. На больших компаниях это
   станет отдельной задержкой при открытии чата.

4. Два WebSocket-контракта для одного чата

   Фронт может слушать `/ws/wazzup/`, `/ws/wazzup/chat/<chat_id>/` или
   `/ws/consalting/funnel/`. Сейчас это совместимо, но нужно закрепить один
   рекомендуемый путь для чат-экрана, иначе легко получить дубли или ощущение
   "сообщение пришло позже", когда экран слушает не тот канал.

5. Тесты маскируют продовую задержку

   В `core.settings_test_sqlite` Celery работает в eager-режиме. Поэтому
   `tests_wazzup_full.py` видит webhook как синхронный, хотя в production это
   очередь. Нужны тесты, где входящий realtime не зависит от eager Celery.

## Что нужно сделать

### P0. Разделить webhook на быстрый realtime-путь и тяжелые сайд-эффекты

Нельзя держать входящий чат за Celery-очередью. Нужно вынести из
`handle_wazzup_webhook()` быстрый слой, который выполняется прямо в
`WazzupWebhookConsaltingView.post()`:

1. распарсить `messages` и `statuses`;
2. найти активный `WazzupAccountConsalting` по `channelId`;
3. нормализовать `chatId`/телефон;
4. идемпотентно создать или найти `LeadConsalting`;
5. идемпотентно создать `WhatsAppMessageConsalting` по `messageId`;
6. после commit отправить `new_message` в Channels;
7. для `statuses` обновить сообщение и после commit отправить `message_status`;
8. вернуть Wazzup `200`.

В Celery оставить тяжелые операции:

- авто-распределение, если оно делает много запросов;
- `ActivityLogger.log`;
- системные уведомления;
- `events.emit`;
- любые внешние HTTP-вызовы;
- медленную аналитику/пересчет.

Рекомендуемое разбиение:

```python
class WazzupConsaltingService:
    @staticmethod
    def handle_wazzup_webhook_realtime(payload) -> list[dict]:
        """Синхронный быстрый путь: БД для чата + WS after commit."""

    @staticmethod
    def enqueue_webhook_side_effects(payload, realtime_result):
        """Фоновые действия без повторного создания сообщения."""
```

`WazzupWebhookConsaltingView.post()` должен вызывать быстрый путь до постановки
фоновой задачи:

```python
result = WazzupConsaltingService.handle_wazzup_webhook_realtime(payload)
WazzupConsaltingService.enqueue_webhook_side_effects(payload, result)
return Response({"status": "ok"})
```

Для защиты от дублей использовать `WhatsAppMessageConsalting.message_id`
(`unique=True`) и обрабатывать `IntegrityError`: если параллельный webhook уже
создал сообщение, второй запрос не должен слать второй `new_message`.

### P1. Перенести все WebSocket-рассылки на `transaction.on_commit`

В `funnel/wazzup.py` не вызывать `_broadcast_consalting_message()` напрямую внутри
`transaction.atomic()`.

Нужно:

```python
transaction.on_commit(
    lambda: _broadcast_consalting_message(...)
)
```

То же для `_broadcast_message_status()`. Это убирает гонку "сокет пришел раньше,
чем запись появилась в БД".

### P1. Исправить realtime статусов

В блоке `statuses` метода `handle_wazzup_webhook()` или в новом быстром методе:

- после `msg.status = st` и `save()` вызвать `_broadcast_message_status(...)`;
- передать телефон лида в `_broadcast_message_status(company_id, msg, phone)`;
- если `messageId` Wazzup уже заменил локальный `message_id` исходящего
  сообщения, фронт все равно должен матчить статус по стабильному `id` записи
  `WhatsAppMessageConsalting.id`.

Ожидаемый WebSocket:

```json
{
  "type": "message_status",
  "data": {
    "id": "uuid-local-message-row",
    "message_id": "wazzup-message-id",
    "lead_id": "uuid-lead",
    "status": "delivered",
    "timestamp": "..."
  }
}
```

### P1. Оставить исходящую отправку оптимистичной

Текущая идея правильная:

- `send_message()` сразу создает `WhatsAppMessageConsalting(status="pending")`;
- фронт рисует свое сообщение из REST-ответа или `send_message_ack`;
- реальный `POST /v3/message` в Wazzup остается в Celery;
- после ответа Wazzup сервер шлет `message_status`.

Нужно только перенести broadcast исходящего сообщения и статуса на
`transaction.on_commit`. Отправитель по сокету свое исходящее не получает из-за
`origin_user_id`, поэтому фронт обязан рисовать сообщение из `ack`/REST-ответа.

### P2. Оптимизировать список чатов

`WazzupChatListView.get()` нужно переписать так, чтобы он не грузил всю историю
сообщений компании.

Минимум:

- добавить пагинацию: `limit`, `offset` или DRF pagination;
- получать последнее сообщение через `Subquery/OuterRef` или PostgreSQL
  `distinct on`, а не через проход по всем `WhatsAppMessageConsalting`;
- добавить `select_related("lead", "owner")`, если остаются проходы по сообщениям;
- добавить индекс для поиска лидов по компании и телефону:

```python
models.Index(fields=["company", "phone"])
```

Лучше:

- хранить на `LeadConsalting` денормализованные поля
  `last_whatsapp_message_at`, `last_whatsapp_message_text`,
  `unread_whatsapp_count`;
- обновлять их при создании входящего/исходящего сообщения;
- список чатов строить из этих полей и открытых inbound-заявок.

### P2. Зафиксировать один контракт WebSocket для фронта

Для чат-экрана рекомендовать:

- основной сокет: `/ws/wazzup/`;
- для конкретного диалога: `/ws/wazzup/chat/<digits_phone>/`;
- `/ws/consalting/funnel/` использовать для канбана и уведомлений, но он может
  получать те же чат-события для обновления карточек.

Фронт должен:

- делать upsert сообщений по `data.id`;
- свое исходящее рисовать из `send_message_ack` или REST `201`;
- `message_status` применять к существующему сообщению по `data.id`;
- сортировать по `timestamp`, а не по порядку прихода;
- при reconnect дозагружать историю через
  `/api/consalting/wazzup-messages/?lead=<lead_id>`.

### P3. Обновить документацию

После кода привести к факту:

- `NurCRM/docs/CONSALTING_WAZZUP_FRONTEND.md`;
- `NurCRM/docs/WAZZUP_CONSALTING_DOCUMENTATION.md`;
- `docs-consaltion/media-and-error-handling.md`.

Сейчас часть документации утверждает, что webhook работает за 10-50 мс и что
некоторые операции вынесены в поток. В коде текущая задержка входящих зависит от
Celery, поэтому эти утверждения нужно перепроверить после исправления.

## Тесты приемки

Добавить или изменить тесты в `apps/consalting/tests_wazzup_full.py`.

1. Входящий webhook не зависит от Celery eager

   - отключить eager для теста или замокать `process_wazzup_webhook.delay`;
   - `POST /api/consalting/wazzup/webhook/`;
   - сразу после ответа проверить, что `WhatsAppMessageConsalting` создан;
   - проверить, что `group_send` получил `new_message`.

2. Дубликат webhook не шлет второй `new_message`

   - два раза отправить один `messageId`;
   - в БД одно сообщение;
   - WebSocket broadcast один раз.

3. Статусы Wazzup шлют `message_status`

   - создать исходящее сообщение;
   - отправить webhook `statuses=[{"messageId": "...", "status": "delivered"}]`;
   - проверить статус в БД;
   - проверить WebSocket `message_status` с `data.id == str(wa_message.id)`.

4. Broadcast после commit

   - проверить, что событие не отправляется до commit транзакции;
   - после commit событие отправлено.

5. Список чатов не деградирует на объеме

   - создать 1000+ сообщений;
   - проверить ограниченное число SQL-запросов;
   - проверить пагинацию;
   - проверить, что endpoint не читает всю историю сообщений.

6. WebSocket отправка исходящего

   - подключиться к `/ws/wazzup/`;
   - отправить `{ "action": "send_message", "lead_id": "...", "text": "..." }`;
   - получить `send_message_ack` со статусом `pending`;
   - после выполнения `send_wazzup_message` получить `message_status`.

## Операционный чек-лист

Перед релизом:

- проверить, что `REDIS_URL` для Channels и `CELERY_BROKER_URL` для Celery
  настроены осознанно и не конфликтуют между окружениями;
- убедиться, что запущены `gunicorn`, `daphne-ws`, `celery`;
- проверить, что Wazzup webhook указывает на
  `https://app.nurcrm.kg/api/consalting/wazzup/webhook/`;
- проверить, что медиа URL из `/wazzup-accounts/<id>/upload/` публично доступен
  для Wazzup;
- добавить логирование времени:
  - `webhook_received_at`;
  - `realtime_saved_ms`;
  - `ws_broadcast_ms`;
  - `side_effects_queued_ms`;
  - `celery_queue_wait_ms` для фоновых задач.

## Definition of done

- Входящее сообщение появляется в открытом чате без ожидания Celery worker.
- `delivered/read/failed` из Wazzup webhook обновляют пузырь через
  `message_status`.
- Исходящие сообщения остаются оптимистичными: пользователь видит `pending`
  сразу, а финальный статус приходит отдельно.
- Все WS-события сообщений отправляются только после commit.
- Список чатов имеет пагинацию или денормализованные поля и не читает всю
  историю сообщений компании.
- Тесты покрывают сценарий с не-eager Celery.
