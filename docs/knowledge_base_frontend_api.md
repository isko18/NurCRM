# Knowledge Base Frontend API

Базовый URL: `/api/main/public/knowledge-base/`

Авторизация не нужна: эндпоинты публичные.

## Типы

```ts
type KnowledgeBaseCourse = {
  id: string;
  title: string;
  lessons: KnowledgeBaseLesson[];
  created_at: string;
  updated_at: string;
};

type KnowledgeBaseLesson = {
  id: string;
  title: string;
  description: string;
  url: string;
  order: number;
  created_at: string;
};
```

## Получить Все Курсы

`GET /api/main/public/knowledge-base/`

Ответ: `KnowledgeBaseCourse[]`

Пример ответа:

```json
[
  {
    "id": "1f98d8f2-7a8d-4e92-b6e5-4f4a42f0fb2d",
    "title": "Старт в nurCRM",
    "lessons": [
      {
        "id": "3b2d1191-e069-4d11-9e9d-6f62ad8e7e20",
        "title": "Как создать товар",
        "description": "Короткое описание урока",
        "url": "https://example.com/lesson-1",
        "order": 0,
        "created_at": "2026-05-21T02:55:00+06:00"
      }
    ],
    "created_at": "2026-05-21T02:55:00+06:00",
    "updated_at": "2026-05-21T02:55:00+06:00"
  }
]
```

## Получить Один Курс

`GET /api/main/public/knowledge-base/{course_id}/`

Ответ: `KnowledgeBaseCourse`

## Создать Курс

`POST /api/main/public/knowledge-base/`

Body:

```json
{
  "title": "Название курса",
  "lessons": [
    {
      "title": "Название урока",
      "description": "Описание урока",
      "url": "https://example.com/lesson"
    }
  ]
}
```

Ответ: `201 Created`, объект созданного курса.

## Обновить Курс

`PATCH /api/main/public/knowledge-base/{course_id}/`

Обновить только название:

```json
{
  "title": "Новое название курса"
}
```

Заменить все уроки курса:

```json
{
  "lessons": [
    {
      "title": "Урок 1",
      "description": "Описание",
      "url": "https://example.com/lesson-1"
    },
    {
      "title": "Урок 2",
      "description": "",
      "url": "https://example.com/lesson-2"
    }
  ]
}
```

Если поле `lessons` передано в `PATCH`, backend полностью удаляет старые уроки курса и создаёт новый список из переданного массива.

Если поле `lessons` не передано, уроки не меняются.

## Поля И Правила

- `title` курса обязателен и должен быть уникальным.
- `lessons` обязателен при `POST`, минимум 1 урок.
- `lessons[].title` обязателен.
- `lessons[].url` обязателен и должен быть валидным URL.
- `lessons[].description` можно отправлять пустой строкой.
- `order` frontend не отправляет: backend выставляет порядок по индексу в массиве, начиная с `0`.
- `id`, `created_at`, `updated_at` read-only.

## Ошибки

Дубликат названия курса:

```json
{
  "title": ["Курс с таким названием уже есть."]
}
```

Пустой список уроков:

```json
{
  "lessons": ["Добавьте хотя бы один урок."]
}
```

Невалидная ссылка:

```json
{
  "lessons": [
    {
      "url": ["Enter a valid URL."]
    }
  ]
}
```

