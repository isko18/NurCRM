# Каталог товаров на странице создания документа (склад) — для фронта

Страница: `/crm/warehouse/documents/create?doc_type=SALE`.
Модуль **warehouse**, база `/api/warehouse/...`. Авторизация обязательна.

Каталог поддерживает: **поиск**, фильтры **бренд/группа/склад**, и возвращает **остаток
(`quantity`)** по каждому товару. Все три ручки ниже ведут себя одинаково по фильтрам и полям.

---

## Эндпоинты

| Назначение | Метод/URL |
|-----------|-----------|
| Товары одного склада | `GET /api/warehouse/{warehouse_id}/products/` |
| Глобальный каталог по всем складам компании | `GET /api/warehouse/products/` |
| Упрощённый список (crud) | `GET /api/warehouse/crud/products/` |

> Для multi-warehouse SALE (владелец, товары с разных складов) используйте
> `GET /api/warehouse/products/` — он ищет по всем складам компании.

---

## Параметры фильтра (query)

Одинаковы для всех трёх ручек. Комбинируются по **AND**.

| Параметр | Тип | Описание |
|----------|-----|----------|
| `search` | string | Поиск по `name` / `article` / `barcode` (+ доп. штрихкоды). Регистронезависимо, по подстроке. |
| `brand` | uuid | Точное совпадение по бренду. |
| `product_group` | uuid | Точное совпадение по группе товара. |
| `warehouse` | uuid | Точное совпадение по складу (полезно на глобальной ручке). |
| `page_size` | int | Размер страницы (по умолчанию 100, максимум 2000). Фронт может слать `1000`. |
| `page` | int | Номер страницы. |

> ⚠️ Имена важны: `brand` (НЕ `brand_id`), `product_group` (НЕ `group`), `search` (НЕ `q`).

Дополнительно (есть в фильтре, если пригодится): `name`, `article`, `status`, `stock`,
`price_min`/`price_max`, `purchase_price_min`/`purchase_price_max`, `markup_min`/`markup_max`,
`created_after`/`created_before`.

---

## Формат ответа

Пагинированный:
```json
{
  "count": 1234,
  "next": "...",
  "previous": null,
  "results": [ /* товары */ ]
}
```

Элемент товара:
```json
{
  "id": "5c5e38de-ce4c-4510-ab8e-2b09121ab8b9",
  "name": "Кола 0.5",
  "article": "COLA-05",
  "barcode": "7267191524231",
  "unit": "шт.",
  "is_weight": false,
  "quantity": "222.000",
  "minimum_quantity": "10.000",
  "purchase_price": "20.000",
  "price": "26.840",
  "wholesale_price": "0.000",
  "discount_percent": "0.00",
  "brand": "b1a2c3d4-...",
  "brand_name": "Coca-Cola",
  "category": "c1a2c3d4-...",
  "product_group": "e1a2c3d4-...",
  "product_group_name": "Напитки",
  "warehouse": "w1a2c3d4-...",
  "warehouse_name": "Основной склад",
  "supplier": "s1a2c3d4-...",
  "supplier_name": "Мирлан Мега",
  "status": "accepted"
}
```

### Ключевые поля
- **`quantity`** — остаток по складу. Строка-decimal (как в детальной карточке
  `GET /api/warehouse/products/{id}/`). Имя именно `quantity` (НЕ `stock`/`remaining`/`balance`).
- FK-поля (`brand`, `category`, `product_group`, `warehouse`, `supplier`) отдаются как **uuid**,
  а рядом — человекочитаемое `*_name`.
- Цены/скидки — строки-decimal.

> Упрощённая ручка `crud/products/` дополнительно отдаёт `group`/`group_name` (алиасы
> `product_group`/`product_group_name`) для обратной совместимости.

---

## Примеры

```
GET /api/warehouse/products/?search=кола&brand=<brand_id>&page_size=1000
```
→ в `results` только товары бренда `<brand_id>` с «кола» в названии/штрихкоде/артикуле,
у каждого — заполненный `quantity`.

```
GET /api/warehouse/{warehouse_id}/products/?product_group=<group_id>&page_size=1000
```
→ товары указанной группы на конкретном складе.

```
GET /api/warehouse/products/?warehouse=<warehouse_id>&search=pepsi
```
→ глобальный поиск, суженный до одного склада.

---

## Чек-лист интеграции

- [ ] Остаток берётся из поля **`quantity`** элемента списка.
- [ ] Поиск шлётся в **`search`**.
- [ ] Фильтр бренда — **`brand`** (uuid), группы — **`product_group`** (uuid).
- [ ] Для «показать всё одним запросом» — `page_size=1000` (работает, макс 2000).
- [ ] Multi-warehouse SALE использует `GET /api/warehouse/products/`.

> На сервере должны быть применены миграции warehouse (поля `supplier`, `minimum_quantity`):
> `python manage.py migrate warehouse`
