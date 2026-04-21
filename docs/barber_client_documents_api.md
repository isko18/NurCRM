# Документы клиента (barber)

## Базовое

- **Раздел**: документы в карточке клиента (много файлов на 1 клиента)
- **Приложение**: `apps/barber`
- **Аутентификация**: требуется (как в остальных эндпоинтах barber)
- **Файл**: передаётся через `multipart/form-data`

Поля документа клиента:

- **id**: UUID
- **client**: UUID (read-only)
- **company**: UUID (read-only)
- **branch**: UUID или `null` (read-only)
- **file**: файл
- **file_comment**: string/null
- **file_create_date**: datetime (read-only)

## 1) Получить карточку клиента с документами

**GET** `/api/barbershop/clients/{client_id}/`

Возвращает стандартные поля клиента + **`documents`**.

Пример ответа (сокращённо):

```json
{
  "id": "0e8b0db2-4e02-4b6b-b0c1-7d3be9fa96b2",
  "company": "0cf0a9b2-4c79-46ee-8755-3dfc7f5a0b1f",
  "branch": null,
  "full_name": "Иван Иванов",
  "phone": "+996700000000",
  "created_at": "2026-04-21T09:30:00Z",
  "documents": [
    {
      "id": "b8433a7f-7cc9-4dd0-9cb3-8f3f7f6a4d1d",
      "company": "0cf0a9b2-4c79-46ee-8755-3dfc7f5a0b1f",
      "branch": null,
      "client": "0e8b0db2-4e02-4b6b-b0c1-7d3be9fa96b2",
      "file": "/media/client_documents/passport.pdf",
      "file_comment": "Паспорт (стр. 1)",
      "file_create_date": "2026-04-21T09:45:10Z"
    }
  ]
}
```

## 2) Список документов клиента

**GET** `/api/barbershop/clients/{client_id}/documents/`

Ответ: массив документов (см. поля выше).

## 3) Добавить документ клиенту

**POST** `/api/barbershop/clients/{client_id}/documents/`

Content-Type: `multipart/form-data`

Поля формы:

- **file**: файл (обязательно)
- **file_comment**: string (опционально)

Пример (curl):

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  -F "file=@passport.pdf" \
  -F "file_comment=Паспорт (стр. 1)" \
  "https://app.nurcrm.kg/api/barbershop/clients/{client_id}/documents/"
```

Пример ответа:

```json
{
  "id": "b8433a7f-7cc9-4dd0-9cb3-8f3f7f6a4d1d",
  "company": "0cf0a9b2-4c79-46ee-8755-3dfc7f5a0b1f",
  "branch": null,
  "client": "0e8b0db2-4e02-4b6b-b0c1-7d3be9fa96b2",
  "file": "/media/client_documents/passport.pdf",
  "file_comment": "Паспорт (стр. 1)",
  "file_create_date": "2026-04-21T09:45:10Z"
}
```

## 4) Обновить документ клиента (comment и/или замена файла)

**PATCH** `/api/barbershop/clients/{client_id}/documents/{doc_id}/`

Content-Type: `multipart/form-data` (если меняете `file`) или JSON (если меняете только `file_comment`).

Разрешено обновлять:

- **file**
- **file_comment**

Пример (замена файла + комментарий):

```bash
curl -X PATCH \
  -H "Authorization: Bearer <token>" \
  -F "file=@passport_v2.pdf" \
  -F "file_comment=Паспорт (обновлён)" \
  "https://app.nurcrm.kg/api/barbershop/clients/{client_id}/documents/{doc_id}/"
```

## 5) Удалить документ клиента

**DELETE** `/api/barbershop/clients/{client_id}/documents/{doc_id}/`

Ответ: `204 No Content`.

