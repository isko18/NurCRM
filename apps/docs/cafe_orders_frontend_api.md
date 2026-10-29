# Cafe Orders API (для фронта)

База: `/api/cafe/`

## Создать заказ

**POST** `/api/cafe/orders/`

### Request body (JSON)

- `guests` (number, optional, default=1): количество гостей
- `status` (string, optional, default=`open`): `open | closed | cancelled`
- `table` (uuid, optional, nullable): стол
- `client` (uuid, optional, nullable): клиент
- `waiter` (uuid, optional, nullable): официант
- `discount_amount` (string/number, optional): скидка (если используется)
- `items` (array, optional): позиции заказа

#### `items[]`

- `line_kind` (string): `menu | service`
- `menu_item` (uuid, required если `line_kind=menu`)
- `service_title` (string, required если `line_kind=service`)
- `unit_price` (string/number, optional): цена за единицу (если не указать для `menu`, возьмётся цена из меню)
- `quantity` (number, optional, default=1)
- `comment` (string, optional): комментарий к позиции (например: `без лука`)
- `is_rejected` (boolean, optional, default=false)
- `rejection_reason` (string, required если `is_rejected=true`)

Важно:
- Внутри `items[]` **НЕ передавать** поле `order`.
- Если добавить одно и то же блюдо (`menu_item`) несколько раз, сервер **склеит в одну строку**:
  - количество увеличит (`quantity += ...`)
  - комментарии объединит через `; ` (пример: `без лука; остро`)

### Пример

```json
{
  "guests": 2,
  "status": "open",
  "items": [
    {
      "line_kind": "menu",
      "menu_item": "04a20fc0-1391-4fb8-8d30-b93ab328e70a",
      "quantity": 1,
      "comment": "без лука"
    }
  ]
}
```

## Получить список заказов

**GET** `/api/cafe/orders/`

Полезные query-параметры (как в DRF):
- `page`, `page_size`
- `ordering` (например `-created_at`)

## Получить заказ

**GET** `/api/cafe/orders/<order_id>/`

## Обновить заказ

**PATCH** `/api/cafe/orders/<order_id>/`

### Важно про `items`

Если отправить `items` в `PATCH`, сервер:
- удалит все старые позиции заказа
- создаст новые из присланного списка

Поэтому при изменении позиций через `PATCH /orders/<id>/` отправляйте **полный актуальный список `items`**, а не только разницу.

### Примеры

Обновить только гостей:

```json
{ "guests": 3 }
```

Заменить весь список позиций:

```json
{
  "items": [
    {
      "line_kind": "menu",
      "menu_item": "249801c2-e4f8-449d-8cb3-a4309c8dedfe",
      "quantity": 2,
      "comment": "остро"
    }
  ]
}
```

## Позиции заказа отдельным эндпоинтом

Если нужно управлять позициями отдельно (без пересоздания всего `items`):

- **POST** `/api/cafe/order-items/` — создать позицию (тут **`order` обязателен**)
- **PATCH** `/api/cafe/order-items/<id>/` — обновить позицию
- **DELETE** `/api/cafe/order-items/<id>/` — удалить позицию

