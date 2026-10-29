# Документация — логин/авторизация (JWT)

В проекте используется **JWT авторизация** через `djangorestframework-simplejwt`.

Базовый префикс API: ` /api/` (см. `core/urls.py`).  
Пути приложения users: ` /api/users/...` (см. `apps/users/urls.py`).

---

## 1) Как устроена авторизация

- **Тип**: JWT (Bearer token)
- **Заголовок**: `Authorization: Bearer <access>`
- **Логин по полю**: `email` (см. `User.USERNAME_FIELD = "email"` в `apps/users/models.py`)
- **Время жизни токенов** (см. `core/settings.py`):
  - `access`: 3 дня
  - `refresh`: 120 дней
- **Logout** как серверный endpoint **не реализован** (blacklist для JWT не подключён в приложении) — обычно на фронте “logout” = удалить токены.

---

## 2) Регистрация владельца компании

`POST /api/users/auth/register/`

Тело:
```json
{
  "email": "owner@example.com",
  "password": "password123",
  "password2": "password123",
  "first_name": "Имя",
  "last_name": "Фамилия",
  "avatar": "https://... (опционально)",
  "company_name": "Nur Market",
  "company_sector_id": "uuid-сектора",
  "subscription_plan_id": "uuid-тарифа"
}
```

Поведение (см. `OwnerRegisterSerializer`):
- создаётся пользователь с ролью `owner` и включаются все `can_view_*` флаги;
- создаётся `Company` и привязывается к пользователю.

Типичные ошибки:
- `{"email": ["Этот email уже используется."]}`
- `{"password2": ["Пароли не совпадают."]}`
- `{"company_sector_id": ["Выбранный сектор не найден."]}`
- `{"subscription_plan_id": ["Выбранный тариф не найден."]}`

---

## 3) Логин (получить access/refresh)

`POST /api/users/auth/login/`

Тело:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Ответ:
- стандартный SimpleJWT: `access`, `refresh`
- плюс расширенные поля из `CustomTokenObtainPairSerializer`:
  - `user_id`, `email`, `first_name`, `last_name`, `avatar`, `phone_number`, `track_number`
  - `company` (имя компании)
  - `role` (display)
  - `branch_ids` (список доступных филиалов)
  - `primary_branch_id`

Пример ответа (схематично):
```json
{
  "refresh": "....",
  "access": "....",
  "user_id": "uuid",
  "email": "user@example.com",
  "first_name": "Имя",
  "last_name": "Фамилия",
  "company": "Nur Market",
  "role": "Владелец",
  "branch_ids": ["..."],
  "primary_branch_id": "..."
}
```

---

## 4) Обновить access по refresh

`POST /api/users/auth/refresh/`

Тело:
```json
{ "refresh": "ваш_refresh_токен" }
```

Особенность настроек:
- `ROTATE_REFRESH_TOKENS = False`, поэтому обычно вернётся **только новый access** (refresh не меняется).

---

## 5) Проверить “кто я” (профиль)

`GET /api/users/profile/`

Заголовки:
- `Authorization: Bearer <access>`

Также endpoint поддерживает обновление профиля:
`PATCH /api/users/profile/` (см. `CurrentUserAPIView`).

---

## 6) Смена пароля

`POST /api/users/settings/change-password/`

Тело:
```json
{
  "current_password": "старый",
  "new_password": "новый",
  "new_password2": "новый"
}
```

Ответ:
`200 {"detail":"Пароль успешно изменён."}`

Типичные ошибки (см. `ChangePasswordSerializer`):
- `{"current_password": ["Неверный текущий пароль."]}`
- `{"new_password2": ["Пароли не совпадают."]}`
- `{"new_password": ["Новый пароль должен отличаться от текущего."]}`

Важно:
- так как серверный “logout/blacklist” не реализован, **старые access токены могут оставаться валидными до истечения срока**. Если нужно “сбросить все сессии” — потребуется добавить blacklist/rotation или хранить серверную ревокацию.

---

## 7) Как использовать токен в запросах

Пример заголовка:

```text
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

