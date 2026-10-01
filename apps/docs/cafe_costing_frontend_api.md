# Cafe Costing API (для фронта)

База: `/api/cafe/`

Этот документ описывает **новую** логику себестоимости блюд для кафе/ресторана:

- ингредиенты блюда могут быть **product** (со склада) или **preparation** (заготовка)
- у ингредиента могут быть обработки (**processing types**)
- есть потери веса и unit_cost у заготовок
- есть себестоимость блюда и маржа
- списание склада при продаже учитывает тип ингредиента
- **совместимость**: старая схема `Ingredient` (меню → ингредиенты → склад) продолжает работать

---

## 0) Единицы измерения

Поддерживаются:

- `kg`, `g`
- `l`, `ml`
- `pcs`

Правила:

- Нельзя конвертировать вес ↔ объём ↔ штуки.
- Все `quantity`/цены — строки (Decimal).

---

## 1) Сущности и поля (кратко)

### 1.1) Preparation (заготовка)

Заготовка — это преобразование сырья (продукта со склада) в полуфабрикат с учётом потерь и доп. обработки.

Ключевые поля:

- `name`: название
- `source_product`: UUID склада (`Warehouse`)
- `input_quantity`, `input_unit`: вход
- `output_quantity`, `output_unit`: выход
- `loss_quantity = input - output`
- `loss_percent = (loss / input) * 100`
- `raw_material_cost`: стоимость сырья
- `processing_cost`: доп. стоимость обработки (за всю партию)
- `total_cost = raw_material_cost + processing_cost`
- `unit_cost = total_cost / output_quantity`
- `stock_quantity`: остаток заготовки на складе
- `is_active`

### 1.2) ProcessingType (тип обработки)

Поля:

- `name`
- `cost`
- `charge_type`: `fixed` | `per_unit`
- `unit` (опционально, строка)
- `is_active`

Расчёт:

- `fixed`: добавляется `cost`
- `per_unit`: добавляется `cost * quantity` (quantity — количество ингредиента в блюде)

### 1.3) DishIngredient (ингредиент блюда, новая схема)

Поля:

- `dish`: UUID блюда (`MenuItem`)
- `ingredient_type`: `product` | `preparation`
- `product` (nullable)
- `preparation` (nullable)
- `quantity`, `unit`
- расчётные поля:
  - `unit_cost`
  - `ingredient_cost`
  - `processing_cost`
  - `total_cost`

Правила:

- если `ingredient_type=product` → `product` обязателен, `preparation` должен быть null
- если `ingredient_type=preparation` → `preparation` обязателен, `product` должен быть null
- `quantity > 0`

### 1.4) DishIngredientProcessing (обработка ингредиента)

Поля:

- `ingredient` (DishIngredient)
- `processing_type` (ProcessingType)
- `cost` (на записи хранится, но расчёт берётся из `processing_type`)

---

## 2) Как считается себестоимость блюда

### 2.1) Источник себестоимости

- если у блюда есть **хотя бы один** `dish_ingredients` (новая схема) → себестоимость считается **по новой схеме**
- если `dish_ingredients` нет → себестоимость считается **по старой схеме** (`Ingredient` + `Warehouse.unit_price`)

### 2.2) Формулы по блюду

- `cost_price = сумма(total_cost всех ингредиентов) + other_expenses`
- `margin_amount = sale_price - cost_price`
- `margin_percent = (margin_amount / sale_price) * 100`

Примечание:

- `sale_price` в текущей модели — это `MenuItem.price`
- маржа хранится в полях `MenuItem.margin_amount` и `MenuItem.margin_percent`

---

## 3) Preparations API

### 3.1) `GET /api/cafe/preparations/`

Query (опционально):

- `branch=<uuid>`
- `source_product=<uuid>`
- `is_active=true|false`
- `search=<text>` (по `name`)
- `ordering=name|created_at|id`

### 3.2) `POST /api/cafe/preparations/`

Создать заготовку. Расчётные поля вычисляются на бэке.

Request body:

```json
{
  "name": "Очищенная картошка",
  "source_product": "UUID_WAREHOUSE",
  "input_quantity": "1",
  "input_unit": "kg",
  "output_quantity": "0.8",
  "output_unit": "kg",
  "processing_cost": "10.00",
  "stock_quantity": "0",
  "is_active": true
}
```

Response (пример, важные поля):

```json
{
  "id": "UUID",
  "name": "Очищенная картошка",
  "source_product": "UUID_WAREHOUSE",
  "input_quantity": "1.000000",
  "input_unit": "kg",
  "output_quantity": "0.800000",
  "output_unit": "kg",
  "loss_quantity": "0.200000",
  "loss_percent": "20.00",
  "raw_material_cost": "100.00",
  "processing_cost": "10.00",
  "total_cost": "110.00",
  "unit_cost": "137.5000",
  "stock_quantity": "0.000000",
  "is_active": true
}
```

### 3.3) `PATCH /api/cafe/preparations/<id>/`

Обновить заготовку.

- поля `loss_quantity/loss_percent/raw_material_cost/total_cost/unit_cost` пересчитываются автоматически
- блюда, использующие эту заготовку, пересчитываются автоматически

### 3.4) `DELETE /api/cafe/preparations/<id>/`

Удалить заготовку.

---

## 4) Processing types API

### 4.1) `GET /api/cafe/processing-types/`

Query (опционально):

- `branch=<uuid>`
- `is_active=true|false`
- `charge_type=fixed|per_unit`
- `search=<text>` (по `name`)
- `ordering=name|id`

### 4.2) `POST /api/cafe/processing-types/`

Request body:

```json
{
  "name": "Жарка",
  "cost": "15.00",
  "charge_type": "fixed",
  "unit": "",
  "is_active": true
}
```

### 4.3) `PATCH /api/cafe/processing-types/<id>/`

Обновить тип обработки.

Блюда, где этот тип используется, пересчитываются автоматически.

### 4.4) `DELETE /api/cafe/processing-types/<id>/`

Удалить тип обработки.

---

## 5) Dish ingredients (новая схема) API

### 5.1) `POST /api/cafe/dishes/<dish_id>/ingredients/`

Добавить ингредиент блюду.

#### Вариант A — product (со склада)

```json
{
  "ingredient_type": "product",
  "product": "UUID_WAREHOUSE",
  "quantity": "50",
  "unit": "g"
}
```

#### Вариант B — preparation (заготовка)

```json
{
  "ingredient_type": "preparation",
  "preparation": "UUID_PREPARATION",
  "quantity": "300",
  "unit": "g"
}
```

Поведение:

- ингредиент создаётся
- блюдо пересчитывается автоматически

### 5.2) `PATCH /api/cafe/dish-ingredients/<id>/`

Изменить ингредиент.

Пример:

```json
{ "quantity": "0.35", "unit": "kg" }
```

После изменения блюдо пересчитывается автоматически.

### 5.3) `DELETE /api/cafe/dish-ingredients/<id>/`

Удалить ингредиент.

После удаления блюдо пересчитывается автоматически.

---

## 6) Обработки ингредиента API

### 6.1) `POST /api/cafe/dish-ingredient-processings/`

Добавить обработку ингредиенту.

Request body:

```json
{
  "ingredient": "UUID_DISH_INGREDIENT",
  "processing_type": "UUID_PROCESSING_TYPE"
}
```

Response:

```json
{ "detail": "ok" }
```

После добавления блюдо пересчитывается автоматически.

### 6.2) `DELETE /api/cafe/dish-ingredient-processings/<id>/`

Удалить обработку по UUID записи `DishIngredientProcessing`.

После удаления блюдо пересчитывается автоматически.

---

## 7) Себестоимость блюда API

### 7.1) `GET /api/cafe/dishes/<dish_id>/cost/`

Бэкенд перед ответом пересчитывает блюдо.

Response:

```json
{
  "dish_id": "UUID_MENU_ITEM",
  "cost_price": "51.25",
  "sale_price": "300.000",
  "margin_amount": "248.75",
  "margin_percent": "82.92"
}
```

---

## 8) Preview (без сохранения)

### `POST /api/cafe/dishes/calculate-preview/`

Считает себестоимость по входным данным, ничего не создаёт.

Request body:

```json
{
  "sale_price": "300.00",
  "other_expenses": "0.00",
  "ingredients": [
    {
      "ingredient_type": "product",
      "product": "UUID_WAREHOUSE",
      "quantity": "50",
      "unit": "g",
      "processing_type_ids": []
    },
    {
      "ingredient_type": "preparation",
      "preparation": "UUID_PREPARATION",
      "quantity": "300",
      "unit": "g",
      "processing_type_ids": ["UUID_PROCESSING_TYPE"]
    }
  ]
}
```

Response:

```json
{
  "ingredients": [
    {
      "ingredient_type": "product",
      "quantity": "50",
      "unit": "g",
      "unit_cost": "50.0000",
      "ingredient_cost": "2.50",
      "processing_cost": "0.00",
      "total_cost": "2.50"
    }
  ],
  "other_expenses": "0.00",
  "cost_price": "53.75",
  "sale_price": "300.000",
  "margin_amount": "246.25",
  "margin_percent": "82.08"
}
```

---

## 9) Списание склада при продаже (важно для фронта)

Списание происходит при оплате заказа (`POST /api/cafe/orders/<id>/pay/`), если `order.stock_deducted=false`.

Правило:

- `DishIngredient.ingredient_type=product` → списываем `Warehouse.remainder`
- `DishIngredient.ingredient_type=preparation` → списываем `Preparation.stock_quantity`
- если у блюда нет `dish_ingredients` → списание идёт по старым `Ingredient` (как раньше)

