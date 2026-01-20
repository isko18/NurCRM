# Warehouse API Документация

Документация по API эндпоинтам модуля складского учета.

**Базовый URL:** `/api/warehouse/`

**Аутентификация:** Требуется JWT токен в заголовке `Authorization: Bearer <token>`

---

## Содержание

1. [Склады (Warehouses)](#склады-warehouses)
2. [Бренды (Brands)](#бренды-brands)
3. [Категории (Categories)](#категории-categories)
4. [Товары (Products)](#товары-products)
5. [Изображения товаров](#изображения-товаров)
6. [Упаковки товаров](#упаковки-товаров)
7. [Документы (Documents)](#документы-documents)
8. [CRUD операции](#crud-операции)
9. [Контрагенты (Counterparties)](#контрагенты-counterparties)

---

## Склады (Warehouses)

### Список складов
```
GET /api/warehouse/
```

**Фильтры:**
- `name` - поиск по названию (частичное совпадение)
- `company` - фильтр по компании (UUID)
- `branch` - фильтр по филиалу (UUID)

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Основной склад",
    "company": "uuid",
    "branch": "uuid",
    "address": "Адрес склада",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### Создать склад
```
POST /api/warehouse/
```

**Тело запроса:**
```json
{
  "name": "Новый склад",
  "address": "Адрес склада"
}
```

### Получить склад
```
GET /api/warehouse/{warehouse_uuid}/
```

### Обновить склад
```
PUT /api/warehouse/{warehouse_uuid}/
PATCH /api/warehouse/{warehouse_uuid}/
```

### Удалить склад
```
DELETE /api/warehouse/{warehouse_uuid}/
```

---

## Бренды (Brands)

### Список брендов
```
GET /api/warehouse/brands/
```

**Фильтры:**
- `name` - поиск по названию (частичное совпадение)
- `company` - фильтр по компании (UUID)
- `branch` - фильтр по филиалу (UUID)

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название бренда",
    "company": "uuid",
    "branch": "uuid"
  }
]
```

### Создать бренд
```
POST /api/warehouse/brands/
```

**Тело запроса:**
```json
{
  "name": "Название бренда"
}
```

### Получить бренд
```
GET /api/warehouse/brands/{brand_uuid}/
```

### Обновить бренд
```
PUT /api/warehouse/brands/{brand_uuid}/
PATCH /api/warehouse/brands/{brand_uuid}/
```

### Удалить бренд
```
DELETE /api/warehouse/brands/{brand_uuid}/
```

---

## Категории (Categories)

### Список категорий
```
GET /api/warehouse/category/
```

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название категории",
    "company": "uuid",
    "branch": "uuid"
  }
]
```

### Создать категорию
```
POST /api/warehouse/category/
```

**Тело запроса:**
```json
{
  "name": "Название категории"
}
```

### Получить категорию
```
GET /api/warehouse/category/{category_uuid}/
```

### Обновить категорию
```
PUT /api/warehouse/category/{category_uuid}/
PATCH /api/warehouse/category/{category_uuid}/
```

### Удалить категорию
```
DELETE /api/warehouse/category/{category_uuid}/
```

---

## Товары (Products)

### Список товаров на складе
```
GET /api/warehouse/{warehouse_uuid}/products/
```

**Фильтры:**
- `name` - поиск по названию
- `article` - поиск по артикулу
- `barcode` - поиск по штрихкоду
- `brand` - фильтр по бренду (UUID)
- `category` - фильтр по категории (UUID)
- `status` - фильтр по статусу

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название товара",
    "article": "ART001",
    "barcode": "1234567890123",
    "warehouse": "uuid",
    "brand": "uuid",
    "category": "uuid",
    "unit": "шт.",
    "quantity": "10.000",
    "purchase_price": "100.00",
    "price": "150.00",
    "is_weight": false,
    "plu": 1,
    "status": "active"
  }
]
```

### Создать товар на складе
```
POST /api/warehouse/{warehouse_uuid}/products/
```

**Тело запроса:**
```json
{
  "name": "Название товара",
  "article": "ART001",
  "barcode": "1234567890123",
  "brand": "uuid",
  "category": "uuid",
  "unit": "шт.",
  "quantity": "10.000",
  "purchase_price": "100.00",
  "price": "150.00",
  "is_weight": false
}
```

### Получить товар (глобально)
```
GET /api/warehouse/products/{product_uuid}/
```

### Обновить товар
```
PUT /api/warehouse/products/{product_uuid}/
PATCH /api/warehouse/products/{product_uuid}/
```

### Удалить товар
```
DELETE /api/warehouse/products/{product_uuid}/
```

---

## Изображения товаров

### Список изображений товара
```
GET /api/warehouse/products/{product_uuid}/images/
```

**Ответ:**
```json
[
  {
    "id": "uuid",
    "image": "url",
    "alt": "Описание",
    "is_primary": true,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### Добавить изображение товара
```
POST /api/warehouse/products/{product_uuid}/images/
Content-Type: multipart/form-data
```

**Поля:**
- `image` - файл изображения
- `alt` - описание изображения (опционально)
- `is_primary` - основное изображение (boolean, опционально)

### Получить изображение
```
GET /api/warehouse/products/{product_uuid}/images/{image_uuid}/
```

### Обновить изображение
```
PUT /api/warehouse/products/{product_uuid}/images/{image_uuid}/
PATCH /api/warehouse/products/{product_uuid}/images/{image_uuid}/
```

### Удалить изображение
```
DELETE /api/warehouse/products/{product_uuid}/images/{image_uuid}/
```

---

## Упаковки товаров

### Список упаковок товара
```
GET /api/warehouse/products/{product_uuid}/packages/
```

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Упаковка 10 шт",
    "quantity_in_package": "10.000",
    "unit": "шт."
  }
]
```

### Добавить упаковку товара
```
POST /api/warehouse/products/{product_uuid}/packages/
```

**Тело запроса:**
```json
{
  "name": "Упаковка 10 шт",
  "quantity_in_package": "10.000",
  "unit": "шт."
}
```

### Получить упаковку
```
GET /api/warehouse/products/{product_uuid}/packages/{package_uuid}/
```

### Обновить упаковку
```
PUT /api/warehouse/products/{product_uuid}/packages/{package_uuid}/
PATCH /api/warehouse/products/{product_uuid}/packages/{package_uuid}/
```

### Удалить упаковку
```
DELETE /api/warehouse/products/{product_uuid}/packages/{package_uuid}/
```

---

## Документы (Documents)

### Список документов
```
GET /api/warehouse/documents/
```

**Фильтры:**
- `doc_type` - тип документа (SALE, PURCHASE, SALE_RETURN, PURCHASE_RETURN, INVENTORY, RECEIPT, WRITE_OFF, TRANSFER)
- `status` - статус (DRAFT, POSTED)
- `warehouse_from` - склад-источник (UUID)
- `warehouse_to` - склад-приемник (UUID)
- `counterparty` - контрагент (UUID)
- `search` - поиск по номеру документа или комментарию

**Параметры пагинации:**
- `page` - номер страницы (по умолчанию 1)
- `page_size` - размер страницы (по умолчанию 100)

**Ответ:**
```json
{
  "count": 100,
  "next": "http://example.com/api/warehouse/documents/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "doc_type": "SALE",
      "status": "DRAFT",
      "number": "SALE-20240101-0001",
      "date": "2024-01-01T00:00:00Z",
      "warehouse_from": "uuid",
      "warehouse_to": null,
      "counterparty": "uuid",
      "counterparty_display_name": "Название контрагента",
      "comment": "Комментарий",
      "total": "1500.00",
      "items": [
        {
          "id": "uuid",
          "product": "uuid",
          "qty": "10.000",
          "price": "150.00",
          "discount_percent": "0.00",
          "line_total": "1500.00"
        }
      ]
    }
  ]
}
```

### Создать документ
```
POST /api/warehouse/documents/
```

**Тело запроса:**
```json
{
  "doc_type": "SALE",
  "warehouse_from": "uuid",
  "counterparty": "uuid",
  "comment": "Комментарий",
  "items": [
    {
      "product": "uuid",
      "qty": "10.000",
      "price": "150.00",
      "discount_percent": "5.00"
    }
  ]
}
```

**Типы документов:**
- `SALE` - Продажа (требует `warehouse_from` и `counterparty`)
- `PURCHASE` - Покупка (требует `warehouse_from` и `counterparty`)
- `SALE_RETURN` - Возврат продажи (требует `warehouse_from` и `counterparty`)
- `PURCHASE_RETURN` - Возврат покупки (требует `warehouse_from` и `counterparty`)
- `INVENTORY` - Инвентаризация (требует `warehouse_from`)
- `RECEIPT` - Приход (требует `warehouse_from`)
- `WRITE_OFF` - Списание (требует `warehouse_from`)
- `TRANSFER` - Перемещение (требует `warehouse_from` и `warehouse_to`)

### Получить документ
```
GET /api/warehouse/documents/{document_uuid}/
```

### Обновить документ
```
PUT /api/warehouse/documents/{document_uuid}/
PATCH /api/warehouse/documents/{document_uuid}/
```

**Важно:** Нельзя изменять проведенный документ. Сначала нужно отменить проведение.

### Удалить документ
```
DELETE /api/warehouse/documents/{document_uuid}/
```

### Провести документ
```
POST /api/warehouse/documents/{document_uuid}/post/
```

**Тело запроса (опционально):**
```json
{
  "allow_negative": true
}
```

**Параметры:**
- `allow_negative` (boolean, опционально) - разрешить проведение при недостаточном количестве товара (отрицательные остатки)

**Что происходит при проведении:**
1. Генерируется номер документа (если не указан)
2. Пересчитывается итоговая сумма документа
3. Проверяются остатки товаров на складе (если `allow_negative` не установлен)
4. Создаются движения товаров (StockMove)
5. Обновляются остатки на складе (StockBalance)
6. Статус документа меняется на `POSTED`

**Ошибки:**
- `"Document already posted"` - документ уже проведен
- `"Cannot post empty document"` - документ не содержит товаров
- `"Недостаточно товара 'артикул' на складе 'название'. Доступно: X, требуется: Y"` - недостаточно товара на складе

### Отменить проведение документа
```
POST /api/warehouse/documents/{document_uuid}/unpost/
```

**Что происходит при отмене:**
1. Отменяются движения товаров (StockMove)
2. Восстанавливаются остатки на складе (StockBalance)
3. Статус документа меняется на `DRAFT`

**Ошибки:**
- `"Document is not posted"` - документ не проведен

---

## CRUD операции

### Товары (CRUD)

#### Список товаров
```
GET /api/warehouse/crud/products/
```

**Параметры поиска:**
- `search` - поиск по названию, артикулу или штрихкоду

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название товара",
    "article": "ART001",
    "barcode": "1234567890123",
    "unit": "шт.",
    "quantity": "10.000"
  }
]
```

#### Создать товар
```
POST /api/warehouse/crud/products/
```

**Тело запроса:**
```json
{
  "name": "Название товара",
  "article": "ART001",
  "barcode": "1234567890123",
  "unit": "шт.",
  "quantity": "10.000"
}
```

#### Получить товар
```
GET /api/warehouse/crud/products/{product_uuid}/
```

#### Обновить товар
```
PUT /api/warehouse/crud/products/{product_uuid}/
PATCH /api/warehouse/crud/products/{product_uuid}/
```

#### Удалить товар
```
DELETE /api/warehouse/crud/products/{product_uuid}/
```

### Склады (CRUD)

#### Список складов
```
GET /api/warehouse/crud/warehouses/
```

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название склада"
  }
]
```

#### Создать склад
```
POST /api/warehouse/crud/warehouses/
```

**Тело запроса:**
```json
{
  "name": "Название склада"
}
```

#### Получить склад
```
GET /api/warehouse/crud/warehouses/{warehouse_uuid}/
```

#### Обновить склад
```
PUT /api/warehouse/crud/warehouses/{warehouse_uuid}/
PATCH /api/warehouse/crud/warehouses/{warehouse_uuid}/
```

#### Удалить склад
```
DELETE /api/warehouse/crud/warehouses/{warehouse_uuid}/
```

---

## Контрагенты (Counterparties)

### Список контрагентов
```
GET /api/warehouse/crud/counterparties/
```

**Ответ:**
```json
[
  {
    "id": "uuid",
    "name": "Название контрагента",
    "type": "CLIENT"
  }
]
```

**Типы контрагентов:**
- `CLIENT` - Клиент
- `SUPPLIER` - Поставщик
- `BOTH` - Оба

### Создать контрагента
```
POST /api/warehouse/crud/counterparties/
```

**Тело запроса:**
```json
{
  "name": "Название контрагента",
  "type": "CLIENT"
}
```

### Получить контрагента
```
GET /api/warehouse/crud/counterparties/{counterparty_uuid}/
```

### Обновить контрагента
```
PUT /api/warehouse/crud/counterparties/{counterparty_uuid}/
PATCH /api/warehouse/crud/counterparties/{counterparty_uuid}/
```

### Удалить контрагента
```
DELETE /api/warehouse/crud/counterparties/{counterparty_uuid}/
```

---

## Примеры использования

### Пример 1: Создание документа продажи

```bash
# 1. Создать документ
POST /api/warehouse/documents/
{
  "doc_type": "SALE",
  "warehouse_from": "uuid-склада",
  "counterparty": "uuid-контрагента",
  "comment": "Продажа товара клиенту",
  "items": [
    {
      "product": "uuid-товара",
      "qty": "5.000",
      "price": "150.00",
      "discount_percent": "10.00"
    }
  ]
}

# 2. Провести документ
POST /api/warehouse/documents/{document_uuid}/post/
```

### Пример 2: Перемещение товара между складами

```bash
# 1. Создать документ перемещения
POST /api/warehouse/documents/
{
  "doc_type": "TRANSFER",
  "warehouse_from": "uuid-склада-источника",
  "warehouse_to": "uuid-склада-приемника",
  "comment": "Перемещение товара",
  "items": [
    {
      "product": "uuid-товара",
      "qty": "10.000",
      "price": "100.00",
      "discount_percent": "0.00"
    }
  ]
}

# 2. Провести документ
POST /api/warehouse/documents/{document_uuid}/post/
```

### Пример 3: Проведение документа с разрешением отрицательных остатков

```bash
POST /api/warehouse/documents/{document_uuid}/post/
{
  "allow_negative": true
}
```

### Пример 4: Поиск товара по штрихкоду

```bash
GET /api/warehouse/crud/products/?search=1234567890123
```

---

## Обработка ошибок

Все ошибки возвращаются в формате:

```json
{
  "detail": "Описание ошибки"
}
```

**Коды статусов:**
- `200` - Успешно
- `201` - Создано
- `400` - Ошибка валидации
- `401` - Не авторизован
- `403` - Доступ запрещен
- `404` - Не найдено
- `500` - Внутренняя ошибка сервера

**Типичные ошибки:**

1. **Недостаточно товара на складе:**
```json
{
  "detail": "Недостаточно товара 'ART001' на складе 'Основной склад'. Доступно: 5, требуется: 10"
}
```

2. **Документ уже проведен:**
```json
{
  "detail": "Document already posted"
}
```

3. **Нельзя изменить проведенный документ:**
```json
{
  "status": "Нельзя изменять проведенный документ. Сначала отмените проведение."
}
```

---

## Примечания

1. **Автоматическая генерация номера документа:** Номер документа генерируется автоматически при проведении в формате `{DOC_TYPE}-{YYYYMMDD}-{NNNN}` (например, `SALE-20240101-0001`)

2. **Автоматический расчет итогов:** Итоговая сумма документа (`total`) рассчитывается автоматически на основе позиций документа

3. **Проверка остатков:** По умолчанию система проверяет наличие достаточного количества товара на складе перед проведением документа. Можно обойти проверку, передав `allow_negative: true`

4. **Фильтрация по компании и филиалу:** Все запросы автоматически фильтруются по компании и филиалу текущего пользователя

5. **Кэширование:** Поиск товаров по штрихкоду кэшируется на 5 минут для улучшения производительности
