# 📗 Документация по интеграции Wazzup API v3 в NurCRM (Модуль Консалтинг)

Данный документ содержит полное руководство по архитектуре, подключению, принципам работы и API-эндпоинтам интеграции мессенджеров (WhatsApp, Instagram, Telegram) через сервис **Wazzup** в воронке продаж модуля **Консалтинг** (`apps/consalting`).

---

## 🏛 1. Архитектура работы интеграции

```text
📱 Клиент (WhatsApp / Instagram / Telegram)
      │
      ▼
💬 Wazzup API v3 (Wazzup24.com)
      │
      │ HTTP POST Webhook
      ▼
🌐 NurCRM Backend (Django / DRF) — /api/consalting/wazzup/webhook/
      │
      ├── 1. Проверка идемпотентности (message_id / external_id)
      ├── 2. Регистрация InboundLeadConsalting
      ├── 3. Авто-распределение лида менеджерам (Round-Robin / Least-Loaded)
      ├── 4. Создание / поиск LeadConsalting в Канбан-воронке («Новый лид»)
      ├── 5. Сохранение сообщения в WhatsAppMessageConsalting и таймлайне лида
      │
      ├───► 📢 Общий WebSocket (consalting_company_<company_id>) ──► Канбан-доска (React)
      │
      └───► 🔔 Персональный WebSocket (consalting_user_<user_id>) ──► Уведомление менеджеру
```

---

## 🔑 2. Где взять ключи в Wazzup

Для подключения интеграции вам понадобятся 2 значения из личного кабинета **[Wazzup24.com](https://wazzup24.com/)**:

1. **API Key (Ключ API):**
   * Раздел **«Интеграции с CRM»** / **«API»** → поле **«Ключ API»**.
2. **Channel ID (ID Канала):**
   * Раздел **«Каналы»** → нажмите на ваш подключенный WhatsApp/Instagram канал.
   * Скопируйте ID канала (набор букв и цифр, например `c_1029384`).

> 📌 **Примечание:** Для подключения WhatsApp откройте раздел **«Каналы»** → **«Добавить WhatsApp»** и отсканируйте QR-код в мобильном приложении WhatsApp (*Связанные устройства*).

---

## 🚀 3. Пошаговое подключение Wazzup к компании в NurCRM

### Способ 1: Через Панель Администратора (Django Admin)
1. Откройте панель управления `https://app.nurcrm.kg/admin/`.
2. Перейдите в раздел **Консалтинг** → **Wazzup аккаунты консалтинга**.
3. Нажмите **Добавить Wazzup аккаунт консалтинга**.
4. Укажите:
   * **Компания:** Ваша компания.
   * **API Ключ Wazzup:** ваш скопированный API Key.
   * **Channel ID (ID Канала):** ваш скопированный ID канала.
   * **Тип интеграции:** `whatsapp` (или `instagram` / `telegram`).
5. Нажмите **Сохранить**.

---

### Способ 2: Через REST API (из Фронтенда)

#### 1. Создание аккаунта:
**Запрос:** `POST /api/consalting/wazzup-accounts/`  
**Headers:** `Authorization: Bearer <JWT_TOKEN>`  
**Body:**
```json
{
  "api_key": "ваш_api_ключ_из_wazzup",
  "channel_id": "ваш_channel_id_из_wazzup",
  "integration_type": "whatsapp"
}
```

#### 2. Автоматическая привязка Webhook:
**Запрос:** `POST /api/consalting/wazzup-accounts/<ACCOUNT_UUID>/setup-webhook/`  
**Headers:** `Authorization: Bearer <JWT_TOKEN>`  
**Body:**
```json
{
  "webhook_url": "https://app.nurcrm.kg/api/consalting/wazzup/webhook/"
}
```
*После выполнения запроса Wazzup привяжет сервер NurCRM для мгновенной передачи входящих сообщений.*

---

## 🔄 4. Логика авто-распределения и защита от дублей

### 1. Защита от дублей (Идемпотентность):
* Каждое входящее сообщение содержит уникальный `messageId` от Wazzup.
* Если система получает повторный вебхук с тем же `messageId`, запись игнорируется, исключая дублирование лидов и сообщений.

### 2. Алгоритмы авто-распределения лидов:
В разделе **Настройки распределения** (`/api/consalting/lead-distribution/`) доступны 3 режима:

* 🔄 **Round-Robin (Поочередное):**  
  Каждый новый входящий лид назначается следующему менеджеру по кругу из выбранных ролей. Позиция фиксируется в курсоре `_rr_cursor` с блокировкой строк на уровне базы данных (`select_for_update`).
* 📊 **Least-Loaded (Менее загруженному):**  
  Лид назначается менеджеру с наименьшим количеством активных лидов в статусах `NEW`, `ASSIGNED`, `IN_WORK`.
* ✋ **Manual (Ручное распределение):**  
  Лид попадает в общий пул нераспределенных входящих заявках (`InboundLeadConsalting`), руководителю доступен эндпоинт ручного назначения `/assign/`.

### 3. Персональные WebSocket-уведомления:
Как только лид назначен менеджеру, сервер посылает персональное уведомление через Django Channels:
* **Канал:** `consalting_user_<user_id>`
* **Событие:** `lead.assigned`
* Менеджер моментально получает всплывающее уведомление на своем экране.

---

## 📤 5. Отправка исходящих сообщений из карточки лида

Для отправки ответа клиенту из NurCRM в WhatsApp / Instagram:

**Запрос:** `POST /api/consalting/wazzup-accounts/<ACCOUNT_UUID>/send-message/`  
**Headers:** `Authorization: Bearer <JWT_TOKEN>`  
**Body:**
```json
{
  "lead_id": "uuid-карточки-лида",
  "message": "Здравствуйте! Мы получили вашу заявку.",
  "media_url": "https://example.com/file.pdf"  // опционально
}
```

---

## 📋 6. Полная REST API Спецификация

| Метод | URL Эндпоинта | Описание | Доступ |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/consalting/wazzup/webhook/` | Входящий вебхук от Wazzup | Public (Wazzup) |
| `GET` | `/api/consalting/wazzup-accounts/` | Список аккаунтов Wazzup компании | Authenticated |
| `POST` | `/api/consalting/wazzup-accounts/` | Подключение нового аккаунта Wazzup | Authenticated |
| `DELETE`| `/api/consalting/wazzup-accounts/{id}/` | Удаление аккаунта Wazzup | Authenticated |
| `POST` | `/api/consalting/wazzup-accounts/{id}/setup-webhook/` | Авто-привязка Webhook | Authenticated |
| `POST` | `/api/consalting/wazzup-accounts/{id}/send-message/` | Отправка сообщения клиенту | Authenticated |
| `GET` | `/api/consalting/inbound-leads/` | Список всех входящих лидов | Authenticated |
| `POST` | `/api/consalting/inbound-leads/{id}/assign/` | Ручное назначение менеджера | Руководитель |
| `GET` | `/api/consalting/lead-distribution/` | Просмотр настроек авто-распределения | Authenticated |
| `PUT` | `/api/consalting/lead-distribution/` | Изменение алгоритма (Round-Robin/Least-Loaded) | Руководитель |

---

## 🧪 7. Локальное тестирование

Запуск проверочного набора тестов в папке проекта:

```bash
cd NurCRM
.venv/bin/python manage.py test apps.consalting.tests_wazzup_full --settings=core.settings_test_sqlite
```
