# Склад: дополнительные штрихкоды товара

У складского товара (`WarehouseProduct`) основной штрихкод хранится в поле **`barcode`**. К одному товару можно привязать **несколько дополнительных** кодов — они хранятся в модели **`WarehouseProductAlternateBarcode`** и участвуют в поиске/сканировании так же, как основной.

## Эндпоинты (префикс API)

База: `https://<хост>/api/warehouse/` (как в остальной документации склада).

| Действие | Метод | URL |
|----------|--------|-----|
| Список / создание товаров на складе | `GET`, `POST` | `/api/warehouse/<warehouse_uuid>/products/` |
| Карточка товара | `GET`, `PATCH`, `PUT` | `/api/warehouse/products/<product_uuid>/` |
| Сканер (поиск по коду) | `POST` | `/api/warehouse/<warehouse_uuid>/products/scan/` |

Сериализатор карточки: **`WarehouseProductSerializer`** (`apps/warehouse/serializers.py`).

---

## Поля API

| Поле | Направление | Описание |
|------|-------------|----------|
| `barcode` | чтение/запись | Основной штрихкод (как раньше). |
| `alternate_barcodes` | **чтение**: массив строк; **запись**: только в теле `POST`/`PATCH`/`PUT` | Список дополнительных штрихкодов. При записи поле **write-only** (в том же JSON, ответ дополняется списком через сериализатор). |

### Пример: создать товар с доп. кодами

```http
POST /api/warehouse/<warehouse_uuid>/products/
Content-Type: application/json
Authorization: Bearer <token>

{
  "name": "Вода 1.5л",
  "barcode": "4607025391234",
  "alternate_barcodes": ["4607025399999", "2000000000015"],
  "quantity": "10.000",
  "purchase_price": "50.000",
  "markup_percent": "20"
}
```

### Пример: изменить только доп. коды

```http
PATCH /api/warehouse/products/<product_uuid>/
Content-Type: application/json

{
  "alternate_barcodes": ["4607025399999"]
}
```

- Передать **`"alternate_barcodes": []`** — удалить все дополнительные коды.
- **Не передавать** ключ `alternate_barcodes` в `PATCH` — доп. коды **не меняются**.

---

## Правила валидации

1. Доп. код **не должен совпадать** с основным **`barcode`** этого же товара.
2. Любой доп. код **не должен** совпадать с **основным** штрихкодом **другого** товара на **том же складе** (и компании).
3. Любой доп. код **не должен** быть уже привязан как **доп.** к **другому** товару на том же складе.
4. Пустые строки и дубликаты во входном массиве отбрасываются.

---

## Сканирование и поиск

- **`POST .../products/scan/`** — товар находится по **основному** или **любому доп.** штрихкоду.
- Универсальный фильтр товаров **`search`** (в т.ч. `ProductFilter`) — поиск по имени, артикулу, основному штрихкоду и **доп. штрихкодам** (`icontains`).
- В списке документов кэш по длинной строке `search` также учитывает доп. коды.

---

## Перемещение между складами

При создании **новой** карточки товара на складе‑приёмнике (логика перемещения в `apps/warehouse/services.py`) с источника копируются **дополнительные** штрихкоды, если такой код ещё не занят на складе назначения.

---

## Технические файлы

- Модель: `apps/warehouse/models.py` → `WarehouseProductAlternateBarcode`
- Сериализатор: `apps/warehouse/serializers.py` → `WarehouseProductSerializer`, `_sync_warehouse_product_alternate_barcodes`
- Сканер: `apps/warehouse/views.py` → `ProductScanView`
- Фильтр: `apps/warehouse/filters/product.py`
- Упрощённая карточка в документах: `apps/warehouse/serializers_documents.py` → `ProductSimpleSerializer` (поле `alternate_barcodes` только на чтение)

После добавления модели выполните миграции: `python manage.py makemigrations warehouse` и `migrate`.
