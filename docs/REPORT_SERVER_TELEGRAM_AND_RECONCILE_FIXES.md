# Отчёт по исправлению замечаний (Telegram-бот, Аналитика Reconcile/CashFlow/PnL, Витрина ShowcaseOrder)

**Дата проверки:** 01 октября 2026 г.  
**Сервер:** `167.233.194.233`  
**Домен:** `https://app.nurcrm.kg`  
**Ветка:** `feature/summary-products-aggregation` (коммит `2dc08ea`)

---

## 1. Сводка по пунктам из `bag.txt`

| № | Замечание из `bag.txt` | Причина | Что сделано | Статус |
|---|---|---|---|---|
| **1** | Эндпоинты `/api/main/telegram-bot/...` отдавали `404` вместо `401` | Роуты `apps.main.telegram_bot.urls` не были подключены в `apps/main/urls.py` (изменения лежали в `git stash` на сервере и не попали в коммит). | Роуты подключены в `apps/main/urls.py` (`telegram-bot/` и `telegram/`), код закоммичен, запушен и развёрнут. Эндпоинты теперь возвращают `401 Unauthorized` (требуют авторизацию). | **ИСПРАВЛЕНО** |
| **2** | Вебхук бота отдавал `404` | Публичный эндпоинт `TelegramWebhookPublicView` не был зарегистрирован в `core/urls.py` и `apps/main/telegram_bot/urls.py`. | Зарегистрированы маршруты: `/api/telegram/webhook/<uuid:bot_uuid>/`, `/api/telegram/webhook/`, `/telegram/webhook/<uuid:bot_uuid>/`, а также `/api/main/telegram-bot/webhook/<uuid:bot_uuid>/`. Добавлен метод `GET` для пинга/проверки статуса. | **ИСПРАВЛЕНО** |
| **3** | В описании API сервера (Swagger/ReDoc) не было адресов со словом `telegram` | Swagger генерируется динамически на основе зарегистрированных маршрутов Django. Из-за отсутствия `include` в `urls.py` Swagger не видел эти эндпоинты. | После регистрации маршрутов в `urls.py` все 18 эндпоинтов Telegram-бота и вебхуков появились в `/swagger/` и `/redoc/`. | **ИСПРАВЛЕНО** |
| **4** | У заказов витрины нет поля `source` (миграция 0136) | Миграция `0136` была успешно применена в базе данных PostgreSQL (колонка `source` физически существовала в таблице `main_showcaseorder`), однако в коде модели `ShowcaseOrder` (`apps/main/models.py`) и в сериализаторе `ShowcaseOrderSerializer` поле `source` отсутствовало, из-за чего API не отдавал его в JSON-ответах. | Поле `source` добавлено в модель `ShowcaseOrder`, в `ShowcaseOrderSerializer`, в `ShowcaseOrderCreateSerializer`, в логику создания заказа (`views_design.py`) и в вебхук `order.created`. Добавлена фильтрация `?source=` в списке заказов. В Swagger OpenAPI спецификации поле `source` отображается. | **ИСПРАВЛЕНО** |
| **5** | Эндпоинты части 4 (`reconcile`, `cashflow`, `pnl`) отдавали `404` | Классы `AnalyticsReconcileAPIView`, `AnalyticsCashFlowAPIView`, `AnalyticsPnLAPIView` и их роуты находились в `git stash` на сервере. | Восстановлен весь аналитический блок в `apps/main/analytics_market.py` и подключены все маршруты (как `analytics/market/...`, `analytics/...`, так и прямые алиасы `reconcile/`, `cashflow/`, `pnl/`). Теперь они отвечают `401 Unauthorized` (готовы к авторизованным запросам). | **ИСПРАВЛЕНО** |
| **6** | Прямой запрос на IP `167.233.194.233` отдавал `404` | На данном сервере размещено несколько проектов. В конфигурации Nginx запрос по IP (`server_name 167.233.194.233`) направляется в виртуальный хост проекта `Besh-Tashta` (порт 8100), у которого нет эндпоинтов NurCRM. NurCRM слушает строго домен `app.nurcrm.kg` (и `stageapp.nurcrm.kg`). | Для обращения к API NurCRM необходимо передавать заголовок `Host: app.nurcrm.kg` (или использовать домен `https://app.nurcrm.kg/`). Все запросы через домен работают штатно. | **РАЗЪЯСНЕНО** |

---

## 2. Результаты live-проверки с продакшн-сервера (`https://app.nurcrm.kg`)

### Telegram-бот и вебхук:
```bash
$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/telegram-bot/settings/
# Результат: 401 (Код на сервере работает, требует токен авторизации)

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/telegram-bot/stats/
# Результат: 401

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/telegram-bot/inquiries/
# Результат: 401

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/telegram-bot/customers/
# Результат: 401

$ curl -s https://app.nurcrm.kg/api/telegram/webhook/
# Результат: HTTP 200 {"ok":true,"detail":"Telegram webhook service active. Use POST /api/telegram/webhook/{bot_uuid}/ for updates."}
```

### Аналитика Reconcile / CashFlow / PnL (Часть 4):
```bash
$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/analytics/reconcile/
# Результат: 401

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/analytics/cashflow/
# Результат: 401

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/analytics/pnl/
# Результат: 401

$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/reconcile/
# Результат: 401
```

### Заказы витрины (ShowcaseOrder):
```bash
$ curl -s -o /dev/null -w "%{http_code}\n" https://app.nurcrm.kg/api/main/showcase/orders/
# Результат: 401

# Проверка полей модели в Python:
# ShowcaseOrder fields: ['items', 'telegram_inquiries', 'id', 'company', 'number', 'status', 
#   'customer_name', 'customer_phone', 'delivery_type', 'delivery_address', 'comment', 
#   'total', 'source', 'idempotency_key', 'created_at', 'updated_at']
# Has source field?: True
```

### Документация API (Swagger / OpenAPI):
Эндпоинты присутствуют в схеме `https://app.nurcrm.kg/swagger/?format=openapi`:
- `/api/main/telegram-bot/settings/`
- `/api/main/telegram-bot/detect-owner-chat/`
- `/api/main/telegram-bot/test-message/`
- `/api/main/telegram-bot/test-ai/`
- `/api/main/telegram-bot/stats/`
- `/api/main/telegram-bot/inquiries/`
- `/api/main/telegram-bot/customers/`
- `/api/main/telegram-bot/notify/shift-closed/`
- `/api/main/telegram-bot/webhook/{bot_uuid}/`
- `/api/telegram/webhook/{bot_uuid}/`
- `/api/main/analytics/reconcile/`
- `/api/main/analytics/cashflow/`
- `/api/main/analytics/pnl/`
- `/api/main/showcase/orders/` (свойство `source` добавлено в схему объекта `ShowcaseOrder`)

---

## 3. Сервисы перезапущены
На сервере успешно перезапущены системные демоны:
- `gunicorn.service` (NurCRM Production ASGI / HTTP)
- `celery.service` (Фоновые задачи и обработка входящих сообщений Telegram)
- `celery-beat.service` (Периодические расписания)
- `gunicorn-staging.service`
- `celery-staging.service`
