Документация API для фронтенда — модуль склада (apps/warehouse)

Введение
- Все запросы требуют аутентификации (Token/Bearer/Session).
- Базовый префикс: /api/warehouse/ (используйте ваш основной префикс API).
- Эндпойнты ограничены `CompanyBranchRestrictedMixin`: пользователь видит данные своей компании/филиала.

Основные эндпойнты
- Документы
  - GET/POST  /api/warehouse/documents/
  - GET/PUT/PATCH/DELETE  /api/warehouse/documents/{id}/
  - POST /api/warehouse/documents/{id}/post/  — провести документ
  - POST /api/warehouse/documents/{id}/unpost/ — отменить проведение

- CRUD (простые)
  - Товары: GET/POST /api/warehouse/crud/products/  и GET/PUT/PATCH/DELETE /api/warehouse/crud/products/{id}/
    - Поиск: ?search=text (по name/article/barcode)
  - Склады: /api/warehouse/crud/warehouses/
  - Контрагенты: /api/warehouse/crud/counterparties/

Фильтры и поиск
- Документы: ?doc_type=SALE&status=POSTED&warehouse_from=<uuid>&warehouse_to=<uuid>&counterparty=<uuid>
- Даты: клиентская фильтрация или внедрите date_from/date_to
- Пагинация: стандартный DRF paging (если включён)

Формат запроса — создание документа (пример)
POST /api/warehouse/documents/
JSON body:
{
  "doc_type": "SALE",
  "warehouse_from": "11111111-1111-1111-1111-111111111111",
  "counterparty": "22222222-2222-2222-2222-222222222222",
  "comment": "Продажа",
  "items": [
    {"product": "33333333-3333-3333-3333-333333333333", "qty": "3", "price": "150.00", "discount_percent": "0"}
  ]
}

- `number`, `status`, `total`, `date` — поля только для чтения. Номер генерируется при `post`.

Проведение и отмена
- Проведение: POST /api/warehouse/documents/{id}/post/  (без тела)
  - При успехе: 200 + сериализованный документ (status: POSTED, number сгенерирован)
- Отмена: POST /api/warehouse/documents/{id}/unpost/  (без тела)
  - При успехе: документ возвращается в DRAFT, движения удалены, остатки откатились

Правила по типам документов (важно для фронта)
- TRANSFER: требует warehouse_from и warehouse_to (и они должны быть разными). При проведении создаются 2 движения: -qty на from и +qty на to.
- INVENTORY: items[].qty — фактический остаток; при проведении delta = fact - current.
- SALE / WRITE_OFF / PURCHASE_RETURN: уменьшают остаток (qty_delta = -qty) на warehouse_from.
- PURCHASE / RECEIPT / SALE_RETURN: увеличивают остаток (qty_delta = +qty) на warehouse_from.
- Для SALE/PURCHASE/... обязательно указать counterparty.
- Нельзя проводить пустой документ.

Валидации на сервере (повторите на фронте для UX)
- items не пуст.
- Для piece items (шт) — qty должно быть целым.
- discount_percent в диапазоне 0..100.
- Нельзя проводить, если операция приведёт к отрицательному остатку и ALLOW_NEGATIVE_STOCK=False.
- Повторное проведение запрещено.

Формат ошибок
- Поле-валидация: 400, тело {"field": ["error1", ...]}
- Бизнес-ошибки: 400, тело {"detail": "текст ошибки"}

Примеры вызовов (axios)
```javascript
import axios from 'axios'
const api = axios.create({ baseURL: '/api/warehouse/', headers: { Authorization: `Token ${token}` } })

export function createDocument(payload) {
  return api.post('documents/', payload).then(r => r.data)
}

export function postDocument(id) {
  return api.post(`documents/${id}/post/`).then(r => r.data)
}

export function unpostDocument(id) {
  return api.post(`documents/${id}/unpost/`).then(r => r.data)
}

export function listDocuments(params) {
  return api.get('documents/', { params }).then(r => r.data)
}
```

UI/UX рекомендации
- Отдельный экран/модал для редактирования items (таблица с добавлением/удалением строк).
- Локальная валидация qty (целое для шт), discount_percent.
- Подтверждение перед `post`/`unpost`.
- Обновление списка остатков после `post` (или перезагрузка данных).

Дополнительно
- Могу сгенерировать OpenAPI/Swagger фрагмент или готовые frontend‑helpers (TypeScript). Напишите, что нужно.
