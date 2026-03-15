# Market POS — API корзины для фронтенда

Документация API корзины (Cart) и позиций (CartItem) для кассовых продаж. Цена и скидка хранятся **отдельно** — можно менять их независимо.

---

## Базовый URL

```
/api/main/pos/
```

Все запросы требуют `Authorization: Bearer <token>`.

---

## Структура корзины (Cart)

### Ответ корзины

```json
{
  "id": "uuid",
  "status": "active",
  "shift": "uuid",
  "subtotal": "100.00",
  "discount_total": "10.00",
  "order_discount_total": "0.00",
  "tax_total": "0.00",
  "total": "90.00",
  "items": [
    {
      "id": "uuid",
      "cart": "uuid",
      "product": "uuid",
      "product_name": "помидор",
      "barcode": "9079951912206",
      "quantity": "1.000",
      "unit_price": "100.00",
      "line_discount": "10.00",
      "display_name": "помидор",
      "primary_image_url": null,
      "images": []
    }
  ]
}
```

### Поля позиции (CartItem)

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | uuid | ID позиции |
| `cart` | uuid | ID корзины |
| `product` | uuid \| null | ID товара (null для кастомных позиций) |
| `product_name` | string | Название товара |
| `barcode` | string | Штрихкод |
| `quantity` | string | Количество (Decimal, 3 знака) |
| `unit_price` | string | **Базовая цена** за единицу (до скидки) |
| `line_discount` | string | **Скидка на строку** (сумма, не процент) |
| `display_name` | string | Отображаемое название |

### Расчёт суммы по позиции

```
line_total = (unit_price × quantity) - line_discount
```

Пример: `unit_price=100`, `quantity=2`, `line_discount=10` → сумма = 190.

---

## Добавление товара

### POST `/api/main/pos/sales/<cart_id>/add-item/`

Можно передать `unit_price`, `discount_total` или **оба**.

```json
{
  "product_id": "uuid",
  "quantity": "1.000",
  "unit_price": "100.00",
  "discount_total": "10.00"
}
```

| Поле | Обязательное | Описание |
|------|--------------|----------|
| `product_id` | да | UUID товара |
| `quantity` | нет | По умолчанию `1.000` |
| `unit_price` | нет | Базовая цена. Если не передано — берётся `product.price` |
| `discount_total` | нет | Скидка на строку (сумма) |

**Логика:**
- `unit_price` — базовая цена позиции
- `discount_total` — скидка на всю строку
- Эффективная цена за единицу = `unit_price - (discount_total / quantity)`

**Ограничение:** эффективная цена не может быть ниже закупочной (`product.purchase_price`).

---

## Изменение позиции (PATCH)

### PATCH `/api/main/pos/carts/<cart_id>/items/<item_id>/`

Цена и скидка меняются **независимо** — можно менять одно, другое или оба.

```json
{
  "quantity": "2.000",
  "unit_price": "100.00",
  "discount_total": "15.00"
}
```

| Поле | Описание |
|------|----------|
| `quantity` | Количество. `0` = удалить позицию |
| `unit_price` | Базовая цена (только цена, скидка не трогается) |
| `discount_total` | Скидка на строку (только скидка, цена не трогается) |

**Примеры:**
1. Только цена: `{ "unit_price": "100" }` — меняется `unit_price`, `line_discount` не трогается
2. Только скидка: `{ "discount_total": "10" }` — меняется `line_discount`, `unit_price` не трогается
3. Оба: `{ "unit_price": "100", "discount_total": "20" }` — меняются оба поля

**Ограничение:** эффективная цена `unit_price - (line_discount / quantity)` не может быть ниже закупочной.

---

## Удаление позиции

### DELETE `/api/main/pos/carts/<cart_id>/items/<item_id>/`

Удаляет позицию из корзины.

---

## Ошибки валидации

| Код | Сообщение |
|-----|-----------|
| 400 | `{"unit_price": "Цена продажи не может быть ниже закупочной (X)."}` |
| 400 | `{"quantity": "Количество должно быть > 0."}` |
| 404 | Позиция или корзина не найдена |

---

## Агентские продажи (Agent)

Те же эндпоинты с префиксом `/api/main/pos/agent/`:
- `POST /api/main/pos/agent/sales/<cart_id>/add-item/`
- `PATCH /api/main/pos/agent/carts/<cart_id>/items/<item_id>/`
- `DELETE /api/main/pos/agent/carts/<cart_id>/items/<item_id>/`

Логика цены и скидки совпадает с обычным POS.

---

## Рекомендации для UI

1. **Отображение:** показывать `unit_price` и `line_discount` отдельно; итог по строке считать как `(unit_price × quantity) - line_discount`.
2. **Редактирование:** отдельные поля для цены и скидки; при изменении одного — отправлять только его в PATCH.
3. **Валидация:** на фронте проверять, что эффективная цена ≥ закупочной (если `purchase_price` доступен).
