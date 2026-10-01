# Уведомления Market / POS — Implementation Guide для бэкенда

**Фронт:** `nur-market` (готов: WS + колокольчик + toast)  
**Бэкенд:** NurCRM API (Django 4.2+ / 5.x + Django Channels + Redis)  
**Статус бэка:** руководство по реализации и актуализации существующей кодовой базы  
**Связано:** `apps/main/models.py` (`Notification`), `apps/main/realtime.py`, `apps/main/consumers.py`, `apps/main/views.py`

---

## Содержание

1. [Цель и архитектура](#1-цель-и-архитектура)
2. [Модель данных (`Notification`)](#2-модель-данных-notification)
3. [WebSocket (Django Channels)](#3-websocket-django-channels)
4. [Сервисный слой (`realtime.py`)](#4-сервисный-слой-realtimepy)
5. [REST API (контракт с фронтендом)](#5-rest-api-контракт-с-фронтендом)
6. [Market-сценарии: триггеры и payload](#6-market-сценарии-триггеры-и-payload)
7. [Cron и фоновые задачи (Celery)](#7-cron-и-фоновые-задачи-celery)
8. [Идемпотентность и Dedupe](#8-идемпотентность-и-dedupe)
9. [Приоритеты реализации (P0 → P2)](#9-приоритеты-реализации-p0--p2)
10. [Тестирование и QA](#10-тестирование-и-qa)
11. [Чеклист перед выкатом](#11-чеклист-перед-выкатом)

---

## 1. Цель и архитектура

### 1.1. Гибридная схема

Система использует **два параллельных канала** доставки:

| Канал | Назначение | Когда используется |
| :--- | :--- | :--- |
| **PostgreSQL** (`Notification`) | Персистентная история уведомлений | Оффлайн-пользователь прочитает через REST API при входе в систему |
| **WebSocket** (`/ws/notifications/`) | Мгновенная доставка (real-time) | Онлайн-пользователь видит toast + badge счетчика без перезагрузки страницы |
| **REST API** (`/api/main/notifications/`) | CRUD + отметка о прочтении | Cold start, список колокольчика, пагинация, фильтрация |

### 1.2. Технический поток

```
[ Бизнес-событие в Market / CRM ]
        │
        ▼
[ signals.py / Service Layer ]
        │
        ▼
transaction.on_commit(...)   ← отправка только после успешного завершения БД-транзакции
        │
        ▼
[ realtime.py: create_and_publish_notification ]
        ├── 1. INSERT Notification (PostgreSQL: user_id, type, data, is_read=False)
        └── 2. ChannelLayer.group_send(group_name, ...)
                    │
                    ▼
            [ WebSocket Consumer (NotificationsConsumer) ]
                    │
                    ▼
            [ Frontend (nur-market / SPA) ]
                ├── Redux / State → колокольчик (badge + unread_count)
                └── Toast Manager → всплывающее уведомление + звук
```

### 1.3. Контракт с фронтендом

Фронтенд ожидает стандартизированную структуру уведомлений:
- **Badge / счетчик:** обновляется при получении сообщений `unread_count` или нового уведомления.
- **Toast:** всплывает при поступлении сообщения с типом `"type": "notification"` и объектом `"data"`.
- **Поля:** `id`, `title`, `message` (или `body`), `level`, `type`, `category`, `url` (или `cta_url`), `cta_label`, `data` (или `meta`), `created_at`.

---

## 2. Модель данных (`Notification`)

В приложении `apps/main/models.py` модель `Notification` спроектирована следующим образом:

### 2.1. Схема модели

```python
# apps/main/models.py
import uuid
from django.db import models
from apps.users.models import User, Company, Branch

class Notification(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Инфо"
        SUCCESS = "success", "Успех"
        WARNING = "warning", "Предупреждение"
        HIGH = "high", "Важно"
        CRITICAL = "critical", "Критично"

    class Category(models.TextChoices):
        TARIFF = "tariff", "Тариф"
        SYSTEM = "system", "Системные"
        NEWS = "news", "Новости"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="notifications", db_index=True)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="crm_notifications",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications",
        db_index=True, verbose_name="Получатель"
    )

    title = models.CharField("Заголовок", max_length=255, blank=True, default="")
    message = models.TextField("Текст сообщения")
    category = models.CharField("Категория", max_length=40, choices=Category.choices, default=Category.OTHER, db_index=True)
    type = models.CharField("Тип события", max_length=64, default="system", db_index=True)
    level = models.CharField("Важность", max_length=16, choices=Level.choices, default=Level.INFO)
    url = models.CharField("Ссылка для перехода", max_length=512, blank=True, default="")
    
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="actor_notifications", verbose_name="Инициатор"
    )
    
    is_read = models.BooleanField("Прочитано", default=False, db_index=True)
    read_at = models.DateTimeField("Дата прочтения", null=True, blank=True)
    
    data = models.JSONField("Данные для UI", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"]),
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["company", "branch", "-created_at"]),
            models.Index(fields=["type", "created_at"]),
        ]

    def __str__(self):
        return f"[{self.type}] {self.user.email}: {self.title or self.message[:30]}"
```

### 2.2. Сериализатор (`NotificationSerializer`)

Сериализатор в `apps/main/serializers.py` обеспечивает обратную совместимость и передает поля под требования фронтенда (`body`, `cta_url`, `cta_label`, `meta`):

```python
# apps/main/serializers.py
class NotificationSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    actor_name = serializers.SerializerMethodField()

    body = serializers.CharField(source="message", read_only=True)
    cta_url = serializers.CharField(source="url", read_only=True)
    cta_label = serializers.SerializerMethodField()
    meta = serializers.JSONField(source="data", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id", "company", "branch", "category", "type", "title", "message", "body",
            "url", "cta_url", "cta_label", "level", "is_read", "actor_name",
            "data", "meta", "created_at",
        ]
        read_only_fields = ["id", "company", "branch", "actor_name", "created_at"]

    def get_cta_label(self, obj):
        if isinstance(obj.data, dict) and obj.data.get("cta_label"):
            return obj.data.get("cta_label")
        if obj.category == Notification.Category.TARIFF:
            return "Продлить"
        return ""

    def get_actor_name(self, obj):
        actor = getattr(obj, "actor", None)
        if not actor:
            return ""
        full = f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip()
        return full or getattr(actor, "email", "") or ""
```

---

## 3. WebSocket (Django Channels)

### 3.1. Подключение и авторизация

- **Endpoint:** `wss://{host}/ws/notifications/?token={access_token}`
- **Авторизация:** JWT Access Token в Query Params (через `core/ws_jwt.py`).
- **Код ошибки авторизации:** `4401` (клиент автоматически обновляет токен и переподключается).

### 3.2. Правила именования групп Channels

> [!IMPORTANT]
> **Ограничение `channels_redis`:** имена групп могут содержать **только** латинские символы, цифры, дефисы и знаки подчеркивания (`[a-zA-Z0-9_.-]`). Длина — до 100 символов.
> **Символ двоеточия (`:`) запрещен** и вызывает критическую ошибку в `channels_redis`!

Используем стандартные префиксы с подчеркиванием `_`:

```python
# Именование групп без двоеточий:
GROUP_USER = f"notif_user_{user_id}"                      # Персональная группа (P0)
GROUP_COMPANY = f"notif_company_{company_id}"            # Вся компания
GROUP_OWNERS = f"notif_company_owners_{company_id}"      # Владельцы / админы компании
GROUP_BRANCH = f"notif_branch_{branch_id}"               # Филиал
GROUP_CASHBOX = f"notif_cashbox_{cashbox_id}"            # Касса
GROUP_WAREHOUSE = f"notif_warehouse_{warehouse_id}"      # Склад
GROUP_ROLE = f"notif_role_{role}"                        # Роль пользователя
```

### 3.3. Реализация `NotificationsConsumer`

```python
# apps/main/consumers.py
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from core.ws_consumer_utils import (
    is_anonymous_scope_user,
    reject_websocket_unauthorized,
    reject_websocket_forbidden,
)
from apps.cafe.consumers import resolve_user_company_and_branch
from apps.main.realtime import (
    user_group_name,
    company_group_name,
    company_owners_group_name,
    branch_group_name,
    cashbox_group_name,
    warehouse_group_name,
    role_group_name,
)

logger = logging.getLogger("nurcrm.websocket.notifications")

class NotificationsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if is_anonymous_scope_user(self.scope):
            await reject_websocket_unauthorized(self)  # close 4401
            return

        user = self.scope["user"]
        company, branch = await self._get_company_and_branch(user)
        if not company:
            await reject_websocket_forbidden(self, reason="no_company")
            return

        self.user_id = str(user.id)
        self.company_id = str(company.id)
        self.branch_id = str(branch.id) if branch else None
        role = getattr(user, "role", None)

        # Формируем список подписок
        self.groups_subscribed = [
            user_group_name(self.user_id),
            company_group_name(self.company_id),
        ]
        
        # Группа владельцев
        if role in ("owner", "admin") or getattr(user, "is_owner_or_admin", False):
            self.groups_subscribed.append(company_owners_group_name(self.company_id))

        if self.branch_id:
            self.groups_subscribed.append(branch_group_name(self.branch_id))

        if role:
            self.groups_subscribed.append(role_group_name(role))

        # Подписка на назначенные кассы и склады
        assigned_cashbox_ids, assigned_warehouse_ids = await self._get_user_assignments(user)
        for c_id in assigned_cashbox_ids:
            self.groups_subscribed.append(cashbox_group_name(c_id))
        for w_id in assigned_warehouse_ids:
            self.groups_subscribed.append(warehouse_group_name(w_id))

        for group in self.groups_subscribed:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()

        await self.send(json.dumps({
            "type": "connection_established",
            "user_id": self.user_id,
        }))

    async def disconnect(self, code):
        for group in getattr(self, "groups_subscribed", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except Exception:
            return

        if data.get("action") == "ping":
            await self.send(json.dumps({"type": "pong"}))

    # Обработчик публикации уведомления (group_send: {"type": "notify", "data": {...}})
    async def notify(self, event):
        await self.send(json.dumps({
            "type": "notification",
            "data": event.get("data") or {},
        }))

    # Обработчик обновления счетчика (group_send: {"type": "unread_count", "count": N})
    async def unread_count(self, event):
        await self.send(json.dumps({
            "type": "unread_count",
            "count": event.get("count", 0),
        }))

    @database_sync_to_async
    def _get_company_and_branch(self, user):
        return resolve_user_company_and_branch(user)

    @database_sync_to_async
    def _get_user_assignments(self, user):
        cashboxes = []
        warehouses = []
        if hasattr(user, "assigned_cashboxes"):
            cashboxes = list(user.assigned_cashboxes.values_list("id", flat=True))
        if hasattr(user, "assigned_warehouses"):
            warehouses = list(user.assigned_warehouses.values_list("id", flat=True))
        return [str(i) for i in cashboxes], [str(i) for i in warehouses]
```

### 3.4. Исходящие сообщения по WebSocket

#### 1. Уведомление (`notification`)
```json
{
  "type": "notification",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "company": "uuid",
    "branch": null,
    "category": "system",
    "type": "market.sale.created",
    "title": "Новая продажа",
    "message": "Касса «Основная»: 1 250,00 сом",
    "body": "Касса «Основная»: 1 250,00 сом",
    "url": "/crm/market/cashier",
    "cta_url": "/crm/market/cashier",
    "cta_label": "Открыть кассу",
    "level": "info",
    "is_read": false,
    "actor_name": "Айбек Касымов",
    "data": {
      "source_kind": "pos_sale",
      "source_id": "sale-uuid-1234",
      "cashbox_id": "cashbox-uuid",
      "amount": "1250.00"
    },
    "meta": {
      "source_kind": "pos_sale",
      "source_id": "sale-uuid-1234",
      "cashbox_id": "cashbox-uuid",
      "amount": "1250.00"
    },
    "created_at": "2026-09-01T14:30:00Z"
  }
}
```

#### 2. Обновление счетчика непрочитанных (`unread_count`)
```json
{
  "type": "unread_count",
  "count": 4
}
```

#### 3. Heartbeat (Ping / Pong)
- Клиент: `{"action": "ping"}`
- Сервер: `{"type": "pong"}`

---

## 4. Сервисный слой (`realtime.py`)

Все функции публикации вынесены в `apps/main/realtime.py`.

```python
# apps/main/realtime.py
from __future__ import annotations
import logging
from typing import Iterable
from django.db import transaction

logger = logging.getLogger("nurcrm.websocket.notifications")

def user_group_name(user_id) -> str:
    return f"notif_user_{user_id}"

def company_group_name(company_id) -> str:
    return f"notif_company_{company_id}"

def company_owners_group_name(company_id) -> str:
    return f"notif_company_owners_{company_id}"

def branch_group_name(branch_id) -> str:
    return f"notif_branch_{branch_id}"

def cashbox_group_name(cashbox_id) -> str:
    return f"notif_cashbox_{cashbox_id}"

def warehouse_group_name(warehouse_id) -> str:
    return f"notif_warehouse_{warehouse_id}"

def role_group_name(role) -> str:
    return f"notif_role_{role}"


def notification_payload(notification) -> dict:
    # Единый формат сериализации для WS и REST
    from apps.main.serializers import NotificationSerializer
    return NotificationSerializer(notification).data


def push_unread_count(user_id) -> None:
    # Отправляет актуальное число непрочитанных в личную WS-группу пользователя
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        from apps.main.models import Notification

        layer = get_channel_layer()
        if not layer:
            return

        count = Notification.objects.filter(user_id=user_id, is_read=False).count()
        group = user_group_name(user_id)

        async_to_sync(layer.group_send)(
            group,
            {
                "type": "unread_count",
                "count": count,
            }
        )
    except Exception:
        logger.error("Failed to push unread_count for user_id=%s", user_id, exc_info=True)


def publish_notification(notification, extra_groups: list[str] | None = None) -> None:
    # Публикует событие в WS-группы после коммита транзакции
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        layer = get_channel_layer()
        if not layer:
            logger.warning("Channel layer is None, cannot publish notification %s", notification.id)
            return

        payload = notification_payload(notification)
        groups = {user_group_name(notification.user_id)}
        if extra_groups:
            groups.update(extra_groups)

        for group in groups:
            async_to_sync(layer.group_send)(
                group,
                {"type": "notify", "data": payload},
            )

        push_unread_count(notification.user_id)
    except Exception:
        logger.error("Failed to publish notification id=%s over WS", getattr(notification, "id", None), exc_info=True)


def create_and_publish_notification(
    *,
    company,
    user,
    message: str,
    title: str = "",
    category: str = "other",
    type: str = "system",
    level: str = "info",
    url: str = "",
    actor = None,
    branch = None,
    data: dict | None = None,
    extra_groups: list[str] | None = None,
    dedupe: bool = True,
):
    # Создает запись Notification в БД и отправляет WS-событие после завершения транзакции
    from apps.main.models import Notification

    data = data or {}
    company_id = getattr(company, "id", company)
    user_id = getattr(user, "id", user)

    # Идемпотентность по source_id
    source_id = data.get("source_id")
    if dedupe and source_id:
        existing = Notification.objects.filter(
            company_id=company_id,
            user_id=user_id,
            type=type,
            data__source_id=str(source_id),
        ).first()
        if existing:
            return existing

    cat = category if category in ("tariff", "system", "news", "other") else "other"

    notification = Notification.objects.create(
        company_id=company_id,
        branch=branch,
        user_id=user_id,
        title=title or "",
        message=message,
        category=cat,
        type=type,
        level=level,
        url=url or "",
        actor=actor,
        data=data,
        is_read=False,
    )

    def _publish():
        publish_notification(notification, extra_groups=extra_groups)

    try:
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(_publish)
        else:
            _publish()
    except Exception:
        _publish()

    return notification


def publish_to_users(
    *,
    user_ids: Iterable,
    company,
    message: str,
    title: str = "",
    category: str = "system",
    type: str = "system",
    level: str = "info",
    url: str = "",
    actor = None,
    branch = None,
    data: dict | None = None,
    dedupe: bool = True,
):
    # Рассылает персональные уведомления нескольким пользователям
    results = []
    for u_id in user_ids:
        notif = create_and_publish_notification(
            company=company,
            user=u_id,
            message=message,
            title=title,
            category=category,
            type=type,
            level=level,
            url=url,
            actor=actor,
            branch=branch,
            data=data,
            dedupe=dedupe,
        )
        if notif:
            results.append(notif)
    return results
```

---

## 5. REST API (Контракт с фронтендом)

Базовый путь: `/api/main/notifications/`

### 5.1. Список уведомлений: `GET /api/main/notifications/`

**Query параметры:**
- `limit` (int, default: 20) — пагинация.
- `offset` (int, default: 0).
- `is_read` (bool, опционально) — фильтр по прочитанным (`false` — только непрочитанные).
- `category` / `type` (str, опционально) — фильтр по категории (`tariff`, `system`, `news` и т.д.).

**Response (200 OK):**
```json
{
  "count": 42,
  "unread_count": 5,
  "results": [
    {
      "id": "a90b6a67-1d89-4a94-8ec5-1cfc1e012345",
      "company": "company-uuid",
      "branch": null,
      "category": "system",
      "type": "market.sale.created",
      "title": "Новая продажа",
      "message": "Касса «Основная»: 1 250,00 сом",
      "body": "Касса «Основная»: 1 250,00 сом",
      "url": "/crm/market/cashier",
      "cta_url": "/crm/market/cashier",
      "cta_label": "Открыть кассу",
      "level": "info",
      "is_read": false,
      "actor_name": "Кассир 1",
      "data": {
        "source_kind": "pos_sale",
        "source_id": "sale-uuid",
        "amount": "1250.00"
      },
      "meta": {
        "source_kind": "pos_sale",
        "source_id": "sale-uuid",
        "amount": "1250.00"
      },
      "created_at": "2026-09-01T14:30:00Z"
    }
  ]
}
```

### 5.2. Отметить одно прочитанным: `POST /api/main/notifications/<uuid:id>/read/`

> **Важно:** Фронтенд вызывает метод **POST** (не PATCH!).

- **Response (200 OK):**
  ```json
  {
    "id": "a90b6a67-1d89-4a94-8ec5-1cfc1e012345",
    "is_read": true
  }
  ```
- **Сайд-эффект:** отправка обновленного `unread_count` по WS в группу `notif_user_{user_id}`.

### 5.3. Отметить все прочитанными: `POST /api/main/notifications/mark-all-read/`

- **Параметры:** опционально `category` в query params или теле запроса.
- **Response (200 OK):**
  ```json
  {
    "status": "Все уведомления прочитаны"
  }
  ```
- **Сайд-эффект:** отправка `{"type": "unread_count", "count": 0}` по WS.

### 5.4. Детали и удаление: `GET` / `DELETE /api/main/notifications/<uuid:id>/`

- `GET` — получение одного уведомления текущего пользователя.
- `DELETE` — удаление уведомления + пуш пересчитанного `unread_count` по WS.

---

## 6. Market-сценарии: триггеры и payload

Ниже приведена матрица системных событий Market/POS, согласованная с фронтенд-типами:

### 6.1. Матрица событий

| Тип события (`type`) | Триггер / Контекст | Получатели (БД) | WS Группа | `level` |
| :--- | :--- | :--- | :--- | :--- |
| `market.sale.created` | Успешная продажа на кассе (checkout) | Кассиры / Владелец | `notif_cashbox_{id}`, `notif_company_{id}` | `info` |
| `market.payment.received` | Поступление оплаты / split payment | Кассир | `notif_cashbox_{id}` | `success` |
| `market.cashflow.created` | Создан авто-кэшфлоу | Владелец | `notif_company_owners_{id}` | `success` |
| `market.cashflow.pending` | Требуется подтверждение кэшфлоу | Владельцы | `notif_company_owners_{id}` | `warning` |
| `market.transfer.received` | Передача партии товара агенту/филиалу | Получатель партии | `notif_user_{id}` | `info` |
| `market.product.status_changed` | Одобрен / отклонен возврат или брак | Инициатор заявки | `notif_user_{id}` | `success` / `warning` |
| `market.product.written_off` | Факт списания брака | Менеджеры склада | `notif_warehouse_{id}` | `info` |
| `market.supplier.return` | Оформлен возврат поставщику | Автор + Владельцы | `notif_user_{id}`, `notif_company_owners_{id}` | `info` |
| `market.stock.low` | Нехватка товара на складе | Менеджеры склада | `notif_warehouse_{id}` | `warning` |
| `market.shift.opened` | Открытие кассовой смены | Владельцы | `notif_company_owners_{id}` | `info` |
| `market.shift.closed` | Закрытие смены (расхождения) | Владельцы | `notif_company_owners_{id}` | `info` / `warning` |
| `market.debt.created` | Продажа в долг | Владельцы / Кассир | `notif_cashbox_{id}` | `info` |
| `market.debt.paid` | Погашение долга клиентом | Кассир / Владельцы | `notif_company_owners_{id}` | `success` |
| `market.debt.overdue` | Просроченный долг (Cron nightly) | Владельцы | `notif_company_owners_{id}` | `warning` |
| `tariff.expiring` | Истечение тарифа (7, 3, 1 дн.) | Владелец компании | `notif_company_owners_{id}` | `warning` / `critical` |

---

### 6.2. Примеры вызова из кода

#### 1. Продажа на кассе (`market.sale.created`)
```python
# apps/main/services/pos.py или signals.py
create_and_publish_notification(
    company=sale.company,
    user=sale.cashier,
    title="Новая продажа",
    message=f"Касса «{sale.cashbox.name}»: {sale.total_amount:,.2f} сом",
    category="system",
    type="market.sale.created",
    level="info",
    url="/crm/market/cashier",
    data={
        "source_kind": "pos_sale",
        "source_id": str(sale.id),
        "cashbox_id": str(sale.cashbox_id),
        "amount": str(sale.total_amount),
    },
    extra_groups=[cashbox_group_name(sale.cashbox_id)],
)
```

#### 2. Передача партии агенту (`market.transfer.received`)
```python
# apps/warehouse/services.py
create_and_publish_notification(
    company=transfer.company,
    user=transfer.recipient_user,
    title="Вам передана новая партия товара",
    message=f"Партия №{transfer.number} со склада «{transfer.from_warehouse.name}»: {transfer.items_count} поз.",
    category="system",
    type="market.transfer.received",
    level="info",
    url="/crm/pending",
    actor=transfer.author,
    data={
        "source_kind": "subreal_transfer",
        "source_id": str(transfer.id),
        "warehouse_id": str(transfer.to_warehouse_id),
        "cta_label": "Принять",
    },
)
```

#### 3. Решение по возврату / браку (`market.product.status_changed`)
```python
# apps/main/views.py
is_approved = status == "approved"
create_and_publish_notification(
    company=doc.company,
    user=doc.created_by,
    title="Заявка одобрена" if is_approved else "Заявка отклонена",
    message=f"Документ №{doc.number} ({doc.title}): статус изменен на {status}",
    category="system",
    type="market.product.status_changed",
    level="success" if is_approved else "warning",
    url="/crm/documents",
    actor=request.user,
    data={
        "source_kind": "return_request",
        "source_id": str(doc.id),
        "status": status,
    },
)
```

---

## 7. Cron и фоновые задачи (Celery)

### 7.1. Оповещения об окончании тарифа (`tariff.expiring`)

Задача выполняется 1 раз в сутки (например, в 09:00 Bishkek):

```python
# apps/main/tasks.py или management/commands/send_tariff_notifications.py
from celery import shared_task
from django.utils import timezone
from apps.users.models import Company
from apps.main.realtime import create_and_publish_notification, company_owners_group_name

@shared_task
def send_tariff_notifications_task():
    today = timezone.localdate()
    companies = Company.objects.filter(is_active=True, end_date__isnull=False, owner__isnull=False)

    for company in companies:
        days_left = (company.end_date.date() - today).days
        if days_left in (7, 3, 1, 0):
            level = "info" if days_left > 3 else ("warning" if days_left > 0 else "critical")
            title = f"Срок подписки истекает сегодня!" if days_left == 0 else f"Тариф заканчивается через {days_left} дн."
            
            create_and_publish_notification(
                company=company,
                user=company.owner,
                title=title,
                message=f"Подписка для компании '{company.name}' истекает ({company.end_date.strftime('%d.%m.%Y')}). Пожалуйста, продлите тариф.",
                category="tariff",
                type="tariff.expiring",
                level=level,
                url="/crm/subscription",
                data={
                    "source_kind": "tariff",
                    "source_id": f"{company.id}-{today}-{days_left}d",
                    "days_left": days_left,
                    "cta_label": "Продлить",
                },
                extra_groups=[company_owners_group_name(company.id)],
                dedupe=True,
            )
```

---

## 8. Идемпотентность и Dedupe

1. **Правило:** при повторном триггере с одинаковым `(company_id, user_id, type, data.source_id)` повторная запись в БД не создается и лишний WS-пуш не отправляется.
2. **Ключ дедупликации:** передается в `data["source_id"]` (например, UUID чека, заявки или ID фоновой задачи с датой).
3. **Исключения:** пользовательские чаты и прямые сообщения, где каждое сообщение должно доставляться отдельно.

---

## 9. Приоритеты реализации (P0 → P2)

### P0 — Критично для запуска Market/POS
- [x] Модель `Notification` с полями `type`, `level`, `category`, `data`, `actor`.
- [x] Сериализатор `NotificationSerializer` с поддержкой `meta`, `body`, `cta_url`, `cta_label`.
- [x] Корректные имена групп в Django Channels (без двоеточий).
- [x] Методы `notify` и `unread_count` в `NotificationsConsumer`.
- [x] Сервисные функции `create_and_publish_notification` и `push_unread_count` с `transaction.on_commit`.
- [x] REST endpoints: `GET /api/main/notifications/`, `POST mark-all-read/`, `POST <id>/read/`.
- [x] Триггеры: `market.sale.created`, `market.cashflow.created` / `.pending`, `market.transfer.received`.

### P1 — Склад, смены, долги
- [ ] Триггеры закрытия/открытия смен: `market.shift.opened`, `market.shift.closed`.
- [ ] Оповещения по долгам: `market.debt.created`, `market.debt.paid`, `market.debt.overdue`.
- [ ] Списания и возвраты поставщикам: `market.product.written_off`, `market.supplier.return`.

### P2 — Расширенные сценарии
- [ ] Оповещения о низком остатке товаров на складе (`market.stock.low`).
- [ ] Локализация сообщений (`ru` / `ky`).

---

## 10. Тестирование и QA

### 10.1. Тестирование через CLI (`websocat`)

1. Подключение к WebSocket:
```bash
websocat "wss://stageapp.nurcrm.kg/ws/notifications/?token=<ACCESS_TOKEN>"
```
2. Ожидаемый первый ответ:
```json
{"type": "connection_established", "user_id": "..."}
```
3. Отправка Ping:
```json
{"action": "ping"}
```
Ответ:
```json
{"type": "pong"}
```

### 10.2. Тестирование REST API (`curl`)

```bash
# 1. Получить список и unread_count
curl -H "Authorization: Bearer $TOKEN"   "https://stageapp.nurcrm.kg/api/main/notifications/?limit=20"

# 2. Отметить одно уведомление прочитанным (POST)
curl -X POST -H "Authorization: Bearer $TOKEN"   "https://stageapp.nurcrm.kg/api/main/notifications/<UUID>/read/"

# 3. Отметить все прочитанными
curl -X POST -H "Authorization: Bearer $TOKEN"   "https://stageapp.nurcrm.kg/api/main/notifications/mark-all-read/"
```

---

## 11. Чеклист перед выкатом

| Пункт проверки | Статус |
| :--- | :--- |
| В именах групп WebSocket **нет двоеточий `:`** (только `_`) | ✅ Исправлено |
| Поле получателя в `Notification` — `user` (Foreign Key к `User`) | ✅ Исправлено |
| Поле JSON в модели — `data` (алиас `meta` на уровне сериализатора) | ✅ Исправлено |
| Поле срока подписки в `Company` — `end_date` | ✅ Исправлено |
| Роут `POST /api/main/notifications/<id>/read/` отвечает HTTP 200 | ✅ Проверено |
| Роут `POST /api/main/notifications/mark-all-read/` отвечает HTTP 200 | ✅ Проверено |
| В ответе `GET /api/main/notifications/` присутствует поле `unread_count` | ✅ Проверено |
| Отправка WS выполняется в `transaction.on_commit` | ✅ Проверено |
