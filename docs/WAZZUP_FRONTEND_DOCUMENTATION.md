# 📘 Фронтенд-документация: Интеграция Wazzup & WhatsApp чата в NurCRM

Данное руководство предназначено для фронтенд-разработчиков (React / Vue / Mobile). Оно описывает полную архитектуру, все HTTP REST API эндпоинты, структуру данных истории сообщений и протокол работы с WebSockets для реализации чата в реальном времени.

---

## 🏛 1. Архитектура и принцип работы

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        ФРОНТЕНД (React App)                             │
└──────────────┬──────────────────────────────────────────▲──────────────┘
               │                                          │
    1. HTTP REST API                               2. Real-time WebSocket
   (Отправка, История, Настройки)                  (Новые сообщения, Статусы)
               │                                          │
               ▼                                          │
┌─────────────────────────────────────────────────────────┴──────────────┐
│                        БЭКЕНД NurCRM (Django)                          │
└──────────────┬─────────────────────────────────────────────────────────┘
               │
    3. Wazzup API v3 (HTTP Webhooks & REST)
               │
               ▼
📱 WhatsApp / Instagram / Telegram (Клиент)
```

---

## 🔑 2. Авторизация (JWT Tokens)

Все запросы от фронтенда требуют передачи JWT-токена авторизации:

1. **HTTP REST API:**
   Заголовок: `Authorization: Bearer <JWT_ACCESS_TOKEN>`
2. **WebSocket Соединения:**
   URL-параметр: `wss://app.nurcrm.kg/ws/wazzup/?token=<JWT_ACCESS_TOKEN>`

---

## 📋 3. Полный справочник REST API

Базовый URL: `https://app.nurcrm.kg`

| Метод | URL Эндпоинта | Назначение |
| :--- | :--- | :--- |
| `GET` | `/api/consalting/wazzup-accounts/` | Список аккаунтов Wazzup компании |
| `POST` | `/api/consalting/wazzup-accounts/` | Подключение нового аккаунта Wazzup |
| `DELETE`| `/api/consalting/wazzup-accounts/{id}/` | Удаление аккаунта Wazzup |
| `POST` | `/api/consalting/wazzup-accounts/{id}/setup-webhook/` | Авто-привязка Webhook к Wazzup |
| `POST` | `/api/consalting/wazzup-accounts/{id}/send-message/` | Отправка сообщения клиенту |
| `GET` | `/api/consalting/wazzup-messages/?lead={lead_id}` | **История сообщений по лиду (Основная)** |
| `GET` | `/api/consalting/leads/{id}/whatsapp/history/` | История сообщений по лиду (Альтернативная) |
| `GET` | `/api/consalting/inbound-leads/` | Список нераспределённых входящих заявок |
| `POST` | `/api/consalting/inbound-leads/{id}/assign/` | Ручное назначение менеджера на лид |

---

## 💬 4. Детальное описание API

### 1. Получение истории сообщений по лиду
> **URL:** `GET /api/consalting/wazzup-messages/?lead={LEAD_UUID}`  
> *(Также поддерживаются псевдонимы `/api/consalting/whatsapp-messages/` и `/api/consalting/messages/`)*

**Headers:**
```http
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

**Пример ответа от сервера (200 OK):**
```json
[
  {
    "id": "c7a8b9d0-1234-4567-89ab-cdef01234567",
    "company": "754b2409-0e9f-4ed3-98b2-7bfe59194666",
    "branch": null,
    "lead": "ea88bcfa-afc2-4acb-85ea-16...",
    "message_id": "wz_msg_1001",
    "direction": "inbound",
    "text": "Здравствуйте! Подскажите стоимость услуг.",
    "status": "read",
    "created_at": "2026-07-26T21:30:00.000000Z",
    "updated_at": "2026-07-26T21:30:00.000000Z"
  },
  {
    "id": "d8b9c0e1-2345-5678-9abc-def012345678",
    "company": "754b2409-0e9f-4ed3-98b2-7bfe59194666",
    "branch": null,
    "lead": "ea88bcfa-afc2-4acb-85ea-16...",
    "message_id": "wz_out_abc123",
    "direction": "outbound",
    "text": "Добрый день! Наш прайс-лист выслан вам.",
    "status": "delivered",
    "created_at": "2026-07-26T21:32:00.000000Z",
    "updated_at": "2026-07-26T21:32:05.000000Z"
  }
]
```

---

### 2. Отправка исходящего сообщения
> **URL:** `POST /api/consalting/wazzup-accounts/{ACCOUNT_UUID}/send-message/`

**Request Body:**
```json
{
  "lead_id": "ea88bcfa-afc2-4acb-85ea-16...",
  "message": "Спасибо за обращение! Наш менеджер свяжется с вами.",
  "media_url": "https://example.com/commercial_offer.pdf"
}
```

**Пример ответа от сервера (201 Created):**
```json
{
  "id": "e9c0d1f2-3456-6789-abcd-ef0123456789",
  "message_id": "wz_out_987654321",
  "status": "sent",
  "text": "Спасибо за обращение! Наш менеджер свяжется с вами."
}
```

---

### 3. Подключение и регистрация Wazzup аккаунта

#### А) Добавление токенов Wazzup:
> **URL:** `POST /api/consalting/wazzup-accounts/`

**Request Body:**
```json
{
  "api_key": "ваш_api_ключ_из_wazzup24",
  "channel_id": "ваш_channel_id_из_wazzup24",
  "integration_type": "whatsapp"
}
```

#### Б) Привязка Webhook:
> **URL:** `POST /api/consalting/wazzup-accounts/{ACCOUNT_UUID}/setup-webhook/`

**Request Body (опционально):**
```json
{
  "webhook_url": "https://app.nurcrm.kg/api/consalting/wazzup/webhook/"
}
```

---

## ⚡ 5. Интеграция WebSocket (Real-Time Чаты)

### URL для подключения:
`wss://app.nurcrm.kg/ws/wazzup/?token=<JWT_ACCESS_TOKEN>`

---

### 📡 WebSocket события от сервера

#### 1. Новое входящее или исходящее сообщение (`new_message`):
```json
{
  "type": "new_message",
  "data": {
    "id": "c7a8b9d0-1234-4567-89ab-cdef01234567",
    "message_id": "wz_msg_998877",
    "chat_id": "+77011234567",
    "text": "Здравствуйте! Хочу уточнить детали.",
    "media_url": null,
    "is_incoming": true,
    "status": "read",
    "timestamp": "2026-07-26T23:50:00.000000Z",
    "contact_name": "Айбек Иманалиев"
  }
}
```

#### 2. Изменение статуса сообщения (`message_status`):
```json
{
  "type": "message_status",
  "data": {
    "id": "c7a8b9d0-1234-4567-89ab-cdef01234567",
    "message_id": "wz_out_abc123",
    "chat_id": "+77011234567",
    "status": "read"
  }
}
```

---

## 💻 6. Готовый пример компонента React (Chat Integration)

```javascript
import React, { useEffect, useState, useRef } from 'react';
import axios from 'axios';

const WazzupChat = ({ leadId, accountId, token }) => {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState('');
  const wsRef = useRef(null);

  // 1. Загрузка истории сообщений при открытии чата
  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await axios.get(`https://app.nurcrm.kg/api/consalting/wazzup-messages/?lead=${leadId}`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        setMessages(res.data);
      } catch (err) {
        console.error("Ошибка загрузки истории:", err);
      }
    };
    if (leadId) fetchHistory();
  }, [leadId, token]);

  // 2. Подключение к WebSocket для приема новых сообщений
  useEffect(() => {
    const socket = new WebSocket(`wss://app.nurcrm.kg/ws/wazzup/?token=${token}`);
    wsRef.current = socket;

    socket.onmessage = (event) => {
      const response = JSON.parse(event.data);

      if (response.type === "new_message") {
        setMessages(prev => [...prev, response.data]);
      }

      if (response.type === "message_status") {
        setMessages(prev => prev.map(msg => 
          msg.message_id === response.data.message_id 
            ? { ...msg, status: response.data.status } 
            : msg
        ));
      }
    };

    return () => socket.close();
  }, [token]);

  // 3. Отправка сообщения
  const handleSend = async () => {
    if (!text.trim()) return;
    try {
      await axios.post(
        `https://app.nurcrm.kg/api/consalting/wazzup-accounts/${accountId}/send-message/`,
        { lead_id: leadId, message: text },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setText('');
    } catch (err) {
      console.error("Ошибка отправки:", err);
    }
  };

  return (
    <div className="chat-container">
      <div className="messages-list">
        {messages.map(msg => (
          <div key={msg.id} className={`message ${msg.direction || (msg.is_incoming ? 'inbound' : 'outbound')}`}>
            <p>{msg.text}</p>
            <span className="status">{msg.status}</span>
          </div>
        ))}
      </div>
      <div className="chat-input">
        <input 
          value={text} 
          onChange={e => setText(e.target.value)} 
          placeholder="Введите сообщение..."
        />
        <button onClick={handleSend}>Отправить</button>
      </div>
    </div>
  );
};

export default WazzupChat;
```
