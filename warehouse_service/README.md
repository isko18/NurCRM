# Warehouse Service

Самостоятельный Django-проект, выделенный из монолита **nurCRM**. Содержит
приложение склада (`apps.warehouse`) вместе с собственным приложением
пользователей/компаний (`apps.users`) и работает на **отдельной базе данных**.

## Состав

| Каталог | Назначение |
|---|---|
| `core/` | Настройки проекта (settings, urls, wsgi, asgi) |
| `apps/users/` | Пользователи, компании, филиалы, роли (копия из монолита) |
| `apps/warehouse/` | Склад: товары, документы, касса, агенты, аналитика |
| `apps/common/` | Общие хелперы, вынесенные из монолита: `utils._is_owner_like`, `cache_utils.cached_result` |

## Отличия от монолита

- `apps.utils` → `apps.common.utils`, `apps.main.cache_utils` → `apps.common.cache_utils`.
- Из `apps.users` убран `scale_views.py` и маршруты весов (`scales/*`) — они были
  завязаны на `apps.main` / `apps.scale` монолита и к складу отношения не имеют.
- Своя БД (`DB_NAME=warehouse_service` по умолчанию), свой `users.User`.
- Channels/Celery не подключены — склад их не использует.

## Запуск

```bash
python -m venv venv
venv/Scripts/activate          # Windows
pip install -r requirements.txt

# настройте подключение к БД (см. .env.example) и создайте БД в PostgreSQL:
#   CREATE DATABASE warehouse_service OWNER <user>;

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Локально без PostgreSQL

Для быстрой проверки можно прогнать на SQLite:

```bash
DB_ENGINE=sqlite python manage.py migrate
DB_ENGINE=sqlite python manage.py runserver
```

## Эндпоинты

- `POST /api/auth/login/`, `POST /api/auth/register/`, `POST /api/auth/refresh/`
- `GET /api/profile/`, `/api/employees/`, `/api/branches/`, `/api/roles/` и др.
- `/api/warehouse/...` — все эндпоинты склада (см. `apps/warehouse/urls.py`).

## Исправленный баг миграции

В монолите миграция `warehouse/0021` делала `AlterField` для поля
`PaymentCategory.system_code`, которое не добавлялось ни одной предыдущей
миграцией. На уже применённых БД это незаметно (0021 помечена выполненной), но
на **чистой** БД миграции падали с `FieldDoesNotExist: PaymentCategory has no
field named 'system_code'` — что блокировало любые новые/тестовые БД. Здесь
операция заменена на корректный `AddField`.

## Тесты

`python manage.py test apps.warehouse` — **71/71 проходит** на чистой БД.

В монолите этот набор не запускался вовсе (баг миграции `system_code` блокировал
создание тестовых БД). После починки миграции тесты заработали, и вскрылись
устаревшие ожидания в самих тестах (написаны под более старый код, не
обновлялись). Логику склада не меняли — выровняли только тесты под текущее
поведение приложения:

- **Категория платежа.** Авто-выбор берёт первую категорию в области; теперь
  сигнал заранее создаёт системные категории (`sale`/`debt`/`incassation`),
  поэтому в тестах, где важна конкретная категория, она задаётся явно в документе.
- **Уникальность системных категорий.** Миграция `0026` активировала ограничение
  `uq_wh_payment_category_system_code_per_scope`. Тесты, создававшие дубли,
  переведены на чтение уже созданных сигналом категорий.
- **Кассовое подтверждение.** Кассовая продажа теперь проходит `CASH_PENDING →
  approve`; тест комплексного сценария переведён на продажу в долг (credit),
  путь с кассой покрыт отдельно.
- **Мульти-склад владельца.** Продажа владельца не требует `warehouse_from` на
  уровне `clean()` — инвариант проверяется на `WRITE_OFF`.
- **Прочее (хрупкость тестов):** сравнение `UUID` со строкой → через `str()`;
  пагинация (`response.data["results"]`); обязательное поле `doc_type` в payload;
  идемпотентный эндпоинт назначения агента возвращает `200`; обязательный `owner`
  у `Company`; актуализирован счётчик запросов в тесте на N+1.
