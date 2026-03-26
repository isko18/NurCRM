# Warehouse API — полный справочник эндпоинтов

Подробная документация по каждому эндпоинту модуля `warehouse`.

- Базовый префикс: `/api/warehouse/`
- Авторизация: `Authorization: Bearer <access_token>`
- Формат: JSON (кроме upload image и PDF)
- UUID в путях и полях: строка UUID

---

## 1) Склады

### 1.1 `GET /api/warehouse/`
- Назначение: получить список складов, доступных пользователю.
- Доступ: авторизованный пользователь с доступом к компании.
- Query:
  - `page`
  - `name` (icontains)
  - `status` (`active|inactive`)
  - `created_after`, `created_before`
  - `branch` (если пользователь не закреплен за одним филиалом)
- Ответ `200`: пагинированный список складов.
- Ошибки:
  - `401` неавторизован
  - `403` нет доступа к компании

### 1.2 `POST /api/warehouse/`
- Назначение: создать склад.
- Body:
```json
{
  "name": "Основной склад",
  "location": "Бишкек, ул. ...",
  "status": "active"
}
```
- Ответ `201`: объект склада.
- Ошибки:
  - `400` валидация полей
  - `403` нет прав

### 1.3 `GET /api/warehouse/{warehouse_uuid}/`
- Назначение: детали склада.
- Ответ `200`: объект склада.
- Ошибки: `404`, `403`.

### 1.4 `PATCH|PUT /api/warehouse/{warehouse_uuid}/`
- Назначение: обновить склад.
- Body: любые изменяемые поля склада.
- Ответ `200`: обновленный объект.
- Ошибки: `400`, `404`, `403`.

### 1.5 `DELETE /api/warehouse/{warehouse_uuid}/`
- Назначение: удалить склад.
- Ответ: `204`.
- Ошибки: `404`, `403`.

---

## 2) Бренды

### 2.1 `GET /api/warehouse/brands/`
- Назначение: список брендов.
- Query: `page`, `name`.
- Ответ `200`: пагинированный список.

### 2.2 `POST /api/warehouse/brands/`
- Назначение: создать бренд.
- Body:
```json
{
  "name": "Nestle",
  "parent": null
}
```
- Ответ `201`.
- Ошибки:
  - `400` дубль в рамках company/branch
  - `400` неверный parent

### 2.3 `GET /api/warehouse/brands/{brand_uuid}/`
- Назначение: детали бренда.
- Ответ `200`.

### 2.4 `PATCH|PUT /api/warehouse/brands/{brand_uuid}/`
- Назначение: обновить бренд.
- Ответ `200`.

### 2.5 `DELETE /api/warehouse/brands/{brand_uuid}/`
- Назначение: удалить бренд.
- Ответ `204`.

---

## 3) Категории

### 3.1 `GET /api/warehouse/category/`
- Назначение: список категорий.
- Query: `page`.
- Ответ `200`.

### 3.2 `POST /api/warehouse/category/`
- Назначение: создать категорию.
- Body:
```json
{
  "name": "Молочные",
  "parent": null
}
```
- Ответ `201`.
- Ошибки: `400` (дубли, parent другой company/branch).

### 3.3 `GET /api/warehouse/category/{category_uuid}/`
- Назначение: детали категории.
- Ответ `200`.

### 3.4 `PATCH|PUT /api/warehouse/category/{category_uuid}/`
- Назначение: обновление категории.
- Ответ `200`.

### 3.5 `DELETE /api/warehouse/category/{category_uuid}/`
- Назначение: удаление категории.
- Ответ `204`.

---

## 4) Группы товаров внутри склада

### 4.1 `GET /api/warehouse/{warehouse_uuid}/groups/`
- Назначение: список групп товаров конкретного склада.
- Query: `page`.
- Ответ `200`.

### 4.2 `POST /api/warehouse/{warehouse_uuid}/groups/`
- Назначение: создать группу в складе.
- Body:
```json
{
  "name": "Напитки",
  "parent": null
}
```
- Ответ `201`.
- Ошибки: `400` (дубль, неверный parent, parent из другого склада).

### 4.3 `GET /api/warehouse/{warehouse_uuid}/groups/{group_uuid}/`
- Назначение: детали группы.
- Ответ `200`.

### 4.4 `PATCH|PUT /api/warehouse/{warehouse_uuid}/groups/{group_uuid}/`
- Назначение: обновление группы.
- Ответ `200`.

### 4.5 `DELETE /api/warehouse/{warehouse_uuid}/groups/{group_uuid}/`
- Назначение: удаление группы.
- Ответ `204`.

---

## 5) Товары

### 5.1 `GET /api/warehouse/{warehouse_uuid}/products/`
- Назначение: список товаров склада.
- Query:
  - `page`
  - `name`, `article`
  - `price_min`, `price_max`
  - `purchase_price_min`, `purchase_price_max`
  - `markup_min`, `markup_max`
  - `brand`, `category`, `product_group`
  - `status`, `stock`
- Ответ `200`.

### 5.2 `POST /api/warehouse/{warehouse_uuid}/products/`
- Назначение: создать товар.
- Body (пример):
```json
{
  "name": "Молоко 1л",
  "article": "MLK-001",
  "barcode": "1234567890123",
  "unit": "шт.",
  "is_weight": false,
  "quantity": "100.000",
  "purchase_price": "45.000",
  "markup_percent": "20.000",
  "price": "54.000",
  "discount_percent": "0.00",
  "brand": null,
  "category": null,
  "product_group": null
}
```
- Ответ `201`: товар с вложенными `characteristics`, `images`, `packages`.
- Ошибки:
  - `400` невалидные значения/отрицательные qty
  - `400` конфликт `barcode/code/plu` (уникальность)
  - `400` объект не из той company/branch

### 5.3 `POST /api/warehouse/{warehouse_uuid}/products/scan/`
- Назначение: найти/создать товар по штрихкоду.
- Body:
```json
{
  "barcode": "1234567890123",
  "name": "Товар по скану",
  "category": null
}
```
- Ответ `200`:
```json
{
  "created": false,
  "scan_qty": "0.000",
  "product": {}
}
```
- Особенности:
  - если найден existing — вернет его
  - если EAN13 весовой — может вернуть вычисленный `scan_qty`
  - если не найден — создаст новый товар (при наличии нужных полей)

### 5.4 `GET /api/warehouse/products/{product_uuid}/`
- Назначение: детали товара.
- Ответ `200`.

### 5.5 `PATCH|PUT /api/warehouse/products/{product_uuid}/`
- Назначение: обновить товар.
- Ответ `200`.

### 5.6 `DELETE /api/warehouse/products/{product_uuid}/`
- Назначение: удалить товар.
- Ответ `204`.

---

## 6) Фото товара

### 6.1 `GET /api/warehouse/products/{product_uuid}/images/`
- Назначение: список фото товара.
- Ответ `200`.

### 6.2 `POST /api/warehouse/products/{product_uuid}/images/`
- Назначение: добавить фото.
- Content-Type: `multipart/form-data`
- Form fields:
  - `image` (файл, обязателен)
  - `alt` (опц.)
  - `is_primary` (опц.)
- Ответ `201`.
- Особенность: сервер конвертирует картинку в WebP.

### 6.3 `GET /api/warehouse/products/{product_uuid}/images/{image_uuid}/`
- Назначение: детали фото.
- Ответ `200`.

### 6.4 `PATCH|PUT /api/warehouse/products/{product_uuid}/images/{image_uuid}/`
- Назначение: обновить метаданные фото.
- Ответ `200`.

### 6.5 `DELETE /api/warehouse/products/{product_uuid}/images/{image_uuid}/`
- Назначение: удалить фото.
- Ответ `204`.

---

## 7) Упаковки товара

### 7.1 `GET /api/warehouse/products/{product_uuid}/packages/`
- Назначение: список упаковок товара.
- Ответ `200`.

### 7.2 `POST /api/warehouse/products/{product_uuid}/packages/`
- Назначение: создать упаковку.
- Body:
```json
{
  "name": "коробка",
  "quantity_in_package": "12.000",
  "unit": "шт."
}
```
- Ответ `201`.
- Ошибки: `400` если `quantity_in_package <= 0`.

### 7.3 `GET /api/warehouse/products/{product_uuid}/packages/{package_uuid}/`
- Назначение: детали упаковки.
- Ответ `200`.

### 7.4 `PATCH|PUT /api/warehouse/products/{product_uuid}/packages/{package_uuid}/`
- Назначение: обновить упаковку.
- Ответ `200`.

### 7.5 `DELETE /api/warehouse/products/{product_uuid}/packages/{package_uuid}/`
- Назначение: удалить упаковку.
- Ответ `204`.

---

## 8) Заявки агента на выдачу товара

### 8.1 `GET /api/warehouse/agent-carts/`
- Назначение: список заявок агента (для owner/admin — по компании).
- Query: `status`, `warehouse`, `agent`, `sale_document`, `submitted_at`, `approved_at`, `page`.
- Ответ `200`.

### 8.2 `POST /api/warehouse/agent-carts/`
- Назначение: создать заявку.
- Body:
```json
{
  "warehouse": "uuid",
  "note": "Прошу выдать товар"
}
```
- Ответ `201`.

### 8.3 `GET /api/warehouse/agent-carts/{pk}/`
- Назначение: детали заявки.
- Ответ `200`.

### 8.4 `PATCH|PUT /api/warehouse/agent-carts/{pk}/`
- Назначение: обновить заявку (обычно только в `draft`).
- Ответ `200`.

### 8.5 `DELETE /api/warehouse/agent-carts/{pk}/`
- Назначение: удалить заявку.
- Ответ `204`.

### 8.6 `POST /api/warehouse/agent-carts/{pk}/submit/`
- Назначение: отправить заявку владельцу.
- Требования:
  - статус `draft`
  - есть хотя бы одна позиция
- Ответ `200`.

### 8.7 `POST /api/warehouse/agent-carts/{pk}/approve/`
- Назначение: одобрить заявку.
- Доступ: owner/admin.
- Действия:
  - уменьшает `StockBalance`
  - увеличивает `AgentStockBalance`
  - статус -> `approved`
- Ответ `200`.

### 8.8 `POST /api/warehouse/agent-carts/{pk}/reject/`
- Назначение: отклонить заявку.
- Доступ: owner/admin.
- Ответ `200`.

### 8.9 `POST /api/warehouse/agent-carts/{pk}/create-sale/`
- Назначение: создать документ SALE по заявке.
- Body:
```json
{
  "counterparty": "uuid",
  "post": false,
  "payment_kind": "cash",
  "prepayment_amount": "0.00",
  "discount_percent": "0.00",
  "discount_amount": "0.00",
  "comment": "Продажа по заявке"
}
```
- Ответ `200`/`201`: созданный SALE документ.

---

## 9) Позиции заявок агента

### 9.1 `GET /api/warehouse/agent-cart-items/`
- Назначение: список позиций.
- Query: `cart`, `page`.
- Ответ `200`.

### 9.2 `POST /api/warehouse/agent-cart-items/`
- Назначение: добавить позицию.
- Body:
```json
{
  "cart": "uuid",
  "product": "uuid",
  "quantity_requested": "5.000"
}
```
- Ответ `201`.

### 9.3 `GET /api/warehouse/agent-cart-items/{pk}/`
- Назначение: детали позиции.
- Ответ `200`.

### 9.4 `PATCH|PUT /api/warehouse/agent-cart-items/{pk}/`
- Назначение: обновить позицию (только draft cart).
- Ответ `200`.

### 9.5 `DELETE /api/warehouse/agent-cart-items/{pk}/`
- Назначение: удалить позицию.
- Ответ `204`.

---

## 10) Остатки агентов

### 10.1 `GET /api/warehouse/agents/me/products/`
- Назначение: остатки текущего агента.
- Query: `order_by=date|-date`, `page`.
- Ответ `200`.

### 10.2 `GET /api/warehouse/owner/agents/products/`
- Назначение: остатки агентов для owner/admin.
- Query: фильтры по агенту/складу/поиску (если заданы во view), пагинация.
- Ответ `200`.

---

## 11) Membership агента в компании

### 11.1 `GET /api/warehouse/agents/companies/search/`
- Назначение: поиск компаний для подачи заявки агентом.
- Query: `search`.
- Ответ `200`: массив компаний.

### 11.2 `GET /api/warehouse/agents/company-requests/`
- Назначение: список заявок/членств.
- Query: `status`, `page`.
- Ответ `200`.

### 11.3 `POST /api/warehouse/agents/company-requests/`
- Назначение: отправить заявку в компанию.
- Body:
```json
{
  "company": "uuid",
  "note": "Хочу работать агентом склада"
}
```
- Ответ `201`.

### 11.4 `POST /api/warehouse/agents/company-requests/{pk}/accept/`
- Назначение: принять заявку.
- Доступ: owner/admin компании.
- Ответ `200`.

### 11.5 `POST /api/warehouse/agents/company-requests/{pk}/reject/`
- Назначение: отклонить заявку.
- Доступ: owner/admin.
- Ответ `200`.

### 11.6 `POST /api/warehouse/agents/company-requests/{pk}/remove/`
- Назначение: отстранить активного агента.
- Доступ: owner/admin.
- Ответ `200`.

### 11.7 `POST /api/warehouse/agents/company-requests/{pk}/common-access/`
- Назначение: обновить общий доступ агента к складу компании.
- Доступ: owner/admin.
- Body:
```json
{
  "common_access_enabled": true,
  "common_warehouse": "uuid"
}
```
- Ответ `200`.

### 11.8 `POST /api/warehouse/agents/company-memberships/`
- Назначение: owner/admin напрямую назначает/активирует агента.
- Body:
```json
{
  "user": "uuid",
  "common_access_enabled": false,
  "common_warehouse": null
}
```
- Ответ `200|201`.

---

## 12) Аналитика

### 12.1 `GET /api/warehouse/agents/me/analytics/`
- Назначение: аналитика текущего агента.
- Query: `period=day|week|month|custom`, `date`, `date_from`, `date_to`.
- Ответ `200`: summary/charts/details.

**Долги по контрагентам агента** (текущее сальдо, не ограничено выбранным периодом графиков — считается по всем проведённым документам в контексте компании/филиала):

- Логика совпадает со **сверкой контрагента** (товарные документы + денежные):  
  `сальдо = (продажи + возврат поставщику) − (покупки + возврат от покупателя) + расход кассы контрагенту − приход от контрагента`.
- Учитываются только контрагенты с `company`, `branch` и **`agent` = этот агент**.

В **`summary`** дополнительно:
| Поле | Смысл |
|------|--------|
| `counterparties_debt_total` | Сумма положительных сальдо: **контрагенты должны компании** |
| `counterparties_payable_total` | Сумма модулей отрицательных сальдо: **компания должна контрагентам** |
| `counterparty_debts_company_name` | Название компании для подписей (`llc` или `name`) |
| `counterparty_debts_branch_name` | Название филиала или `null` |

В **`details.counterparties_debt`** — массив (до 200 записей, сначала крупнейшие долги по знаку сальдо), по каждому контрагенту с ненулевым сальдо:
| Поле | Смысл |
|------|--------|
| `balance` | Сальдо со знаком (строка с 2 знаками) |
| `abs_amount` | Модуль суммы |
| `direction` | `counterparty_owes_company` или `company_owes_counterparty` |
| `debtor`, `creditor` | Кто должник / кредитор: `role` (`company` \| `counterparty`), `name`, у контрагента — `counterparty_id` |
| `summary_ru` | Краткая фраза «кто кому сколько» |
| `breakdown` | Суммы по видам: `sale_and_purchase_return`, `purchase_and_sale_return`, `money_expense`, `money_receipt` и `labels_ru` с пояснениями |

В **`details.counterparties_debt_notes.formula_ru`** — текстовая формула расчёта и знака сальдо.

### 12.2 `GET /api/warehouse/owner/agents/{agent_id}/analytics/`
- Назначение: аналитика конкретного агента для owner/admin.
- Query: как выше.
- Ответ `200`: тот же формат, что и у `agents/me/analytics/`, включая блок долгов по контрагентам этого агента.

### 12.3 `GET /api/warehouse/owner/agents/analytics/`
- Назначение: свод по продажам агентов.
- Query:
  - `period`, `date`, `date_from`, `date_to`
  - `limit` (max 1000), `offset`
  - `order_by` (`sales_amount|sales_count|sales_qty`)
- Ответ `200`.

### 12.4 `GET /api/warehouse/owner/analytics/`
- Назначение: общая аналитика склада по компании.
- Query: `period`, `date`, `date_from`, `date_to`.
- Ответ `200`.

---

## 13) Товарные документы

### 13.1 `GET /api/warehouse/documents/`
- Назначение: список документов.
- Query:
  - `page`
  - `doc_type`, `status`, `payment_kind`
  - `warehouse_from`, `warehouse_to`, `counterparty`
  - `search` (number/comment)
- Ответ `200`.

### 13.2 `POST /api/warehouse/documents/`
- Назначение: создать документ.
- Body (пример SALE):
```json
{
  "doc_type": "SALE",
  "payment_kind": "credit",
  "warehouse_from": "uuid",
  "counterparty": "uuid",
  "cash_register": null,
  "payment_category": null,
  "prepayment_amount": "0.00",
  "discount_percent": "0.00",
  "discount_amount": "0.00",
  "comment": "Продажа",
  "items": [
    {
      "product": "uuid",
      "qty": "2.000",
      "price": "150.00",
      "discount_percent": "0.00",
      "discount_amount": "0.00"
    }
  ]
}
```
- Ответ `201`.
- Ошибки:
  - `400` обязательные поля по типу
  - `400` некорректные скидки/qty
  - `400` товар не из склада документа

### 13.3 `GET /api/warehouse/documents/{pk}/`
- Назначение: детали документа.
- Ответ `200` (включает `items`, `moves`, `receipts`, `expenses`).

### 13.4 `PATCH|PUT /api/warehouse/documents/{pk}/`
- Назначение: обновить документ.
- Важно:
  - если `POSTED` -> ошибка
  - при передаче `items` старые строки перезаписываются.
- Ответ `200`.

### 13.5 `DELETE /api/warehouse/documents/{pk}/`
- Назначение: удалить документ.
- Ответ `204`.

### 13.6 `POST /api/warehouse/documents/{pk}/post/`
- Назначение: провести документ.
- Body (опц.):
```json
{
  "allow_negative": false
}
```
- Эффект:
  - генерит номер при необходимости
  - создает движения/обновляет остатки
  - ставит `POSTED` или `CASH_PENDING`
- Ответ `200`.

### 13.7 `POST /api/warehouse/documents/{pk}/unpost/`
- Назначение: отменить проведение.
- Эффект:
  - удаляет движения
  - откатывает остатки
  - статус `DRAFT`
- Ответ `200`.

### 13.8 `POST /api/warehouse/documents/{pk}/cash/approve/`
- Назначение: подтверждение кассой.
- Body (опц.):
```json
{
  "note": "Подтверждено"
}
```
- Эффект:
  - при необходимости создает `MoneyDocument`
  - статус документа -> `POSTED`
- Ответ `200`.

### 13.9 `POST /api/warehouse/documents/{pk}/cash/reject/`
- Назначение: отклонение кассой.
- Body (опц.): `note`.
- Эффект:
  - откат товарных движений
  - статус `REJECTED`
- Ответ `200`.

---

## 14) Типовые списки документов по типам

Следующие эндпоинты имеют ту же логику, что базовый `documents`, но фиксируют `doc_type`:

- `GET|POST /api/warehouse/documents/sale/` (`SALE`)
- `GET|POST /api/warehouse/documents/purchase/` (`PURCHASE`)
- `GET|POST /api/warehouse/documents/sale-return/` (`SALE_RETURN`)
- `GET|POST /api/warehouse/documents/purchase-return/` (`PURCHASE_RETURN`)
- `GET|POST /api/warehouse/documents/inventory/` (`INVENTORY`)
- `GET|POST /api/warehouse/documents/receipt/` (`RECEIPT`)
- `GET|POST /api/warehouse/documents/write-off/` (`WRITE_OFF`)
- `GET|POST /api/warehouse/documents/transfer/` (`TRANSFER`)

Дополнительно:
- `POST /api/warehouse/transfer/` — создание перемещения (специализированный endpoint).

---

## 15) Документы агента

### 15.1 `GET /api/warehouse/agent/documents/`
- Назначение: список документов текущего агента.
- Ответ `200`.

### 15.2 `POST /api/warehouse/agent/documents/`
- Назначение: создать документ агента.
- Ограничения:
  - нельзя `TRANSFER` и `INVENTORY`
  - контрагент должен принадлежать агенту
- Ответ `201`.

### 15.3 `GET /api/warehouse/agent/documents/{pk}/`
- Назначение: детали документа агента.
- Ответ `200`.

### 15.4 `PATCH|PUT /api/warehouse/agent/documents/{pk}/`
- Назначение: обновить документ агента.
- Ответ `200`.

### 15.5 `DELETE /api/warehouse/agent/documents/{pk}/`
- Назначение: удалить документ агента.
- Ответ `204`.

---

## 16) Кассовые запросы (workflow CASH_PENDING)

### 16.1 `GET /api/warehouse/cash/requests/`
- Назначение: входящие запросы кассы.
- Query:
  - `status` (`PENDING|APPROVED|REJECTED`)
  - `requires_money`
  - `money_doc_type`
  - `document__doc_type`
  - `document__payment_kind`
  - `search`
  - `page`
- Ответ `200`.

### 16.2 `POST /api/warehouse/cash/requests/{pk}/approve/`
- Назначение: подтвердить кассовый запрос.
- Body (опц.): `note`.
- Ответ `200`.

### 16.3 `POST /api/warehouse/cash/requests/{pk}/reject/`
- Назначение: отклонить кассовый запрос.
- Body (опц.): `note`.
- Ответ `200`.

---

## 17) Упрощенный CRUD (для селектов/быстрых форм)

### 17.1 `GET|POST /api/warehouse/crud/products/`
- Назначение: упрощенный список/создание товаров.
- Query: `search`, `page`.
- Ответ `200|201`.

### 17.2 `GET|PATCH|PUT|DELETE /api/warehouse/crud/products/{pk}/`
- Назначение: детальная CRUD-операция по товару.

### 17.3 `GET|POST /api/warehouse/crud/warehouses/`
- Назначение: упрощенный список/создание складов.

### 17.4 `GET|PATCH|PUT|DELETE /api/warehouse/crud/warehouses/{pk}/`
- Назначение: детали/редактирование/удаление склада.

### 17.5 `GET|POST /api/warehouse/crud/counterparties/`
- Назначение: список/создание контрагентов.

### 17.6 `GET|PATCH|PUT|DELETE /api/warehouse/crud/counterparties/{pk}/`
- Назначение: детали/редактирование/удаление контрагента.

---

## 18) Касса (Cash Registers)

### 18.1 `GET /api/warehouse/cash-registers/`
- Назначение: список касс.
- Query: `search`, `page`, `branch`.
- Ответ `200`.

### 18.2 `POST /api/warehouse/cash-registers/`
- Назначение: создать кассу.
- Body:
```json
{
  "name": "Основная касса",
  "location": "Офис"
}
```
- Ответ `201`.

### 18.3 `GET /api/warehouse/cash-registers/{pk}/`
- Назначение: детали кассы.
- Ответ `200`.

### 18.4 `PATCH|PUT /api/warehouse/cash-registers/{pk}/`
- Назначение: обновить кассу.
- Ответ `200`.

### 18.5 `DELETE /api/warehouse/cash-registers/{pk}/`
- Назначение: удалить кассу.
- Ответ `204`.

### 18.6 `GET /api/warehouse/cash-registers/{pk}/operations/`
- Назначение: операции и баланс кассы.
- Ответ `200`:
  - `balance`
  - `receipts_total`
  - `expenses_total`
  - массивы `receipts` и `expenses`.

---

## 19) Категории платежей

### 19.1 `GET /api/warehouse/money/categories/`
- Назначение: список категорий платежей.
- Query: `search`, `page`.
- Ответ `200`.

### 19.2 `POST /api/warehouse/money/categories/`
- Назначение: создать категорию.
- Body:
```json
{
  "title": "Оплата от клиента"
}
```
- Ответ `201`.

### 19.3 `GET /api/warehouse/money/categories/{pk}/`
- Назначение: детали категории.
- Ответ `200`.

### 19.4 `PATCH|PUT /api/warehouse/money/categories/{pk}/`
- Назначение: обновить категорию.
- Ответ `200`.

### 19.5 `DELETE /api/warehouse/money/categories/{pk}/`
- Назначение: удалить категорию.
- Ответ `204`.

---

## 20) Денежные документы

### 20.1 `GET /api/warehouse/money/documents/`
- Назначение: список денежных документов.
- Query:
  - `doc_type`, `status`
  - `cash_register`, `warehouse`, `counterparty`, `payment_category`
  - `search`, `page`
- Ответ `200`.

### 20.2 `POST /api/warehouse/money/documents/`
- Назначение: создать денежный документ.
- Body:
```json
{
  "doc_type": "MONEY_RECEIPT",
  "cash_register": "uuid",
  "counterparty": "uuid",
  "payment_category": "uuid",
  "amount": "1500.00",
  "comment": "Оплата"
}
```
- Опционально: мгновенное проведение через `post=true` (body/query).
- Ответ `201`.

### 20.3 `GET /api/warehouse/money/documents/{pk}/`
- Назначение: детали денежного документа.
- Ответ `200`.

### 20.4 `PATCH|PUT /api/warehouse/money/documents/{pk}/`
- Назначение: обновить денежный документ.
- Ограничение: `POSTED` нельзя менять, пока не `unpost`.
- Ответ `200`.

### 20.5 `DELETE /api/warehouse/money/documents/{pk}/`
- Назначение: удалить денежный документ.
- Ответ `204`.

### 20.6 `POST /api/warehouse/money/documents/{pk}/post/`
- Назначение: провести денежный документ.
- Эффект: статус `POSTED`, номер генерируется при необходимости.
- Ответ `200`.

### 20.7 `POST /api/warehouse/money/documents/{pk}/unpost/`
- Назначение: отменить проведение.
- Эффект: статус `DRAFT`.
- Ответ `200`.

### 20.8 `GET /api/warehouse/money/counterparties/{counterparty_id}/operations/`
- Назначение: операции по одному контрагенту.
- Query:
  - фильтры как у money/documents
  - `include_debts=1` (добавить кредитные товарные операции в unified ответ)
- Ответ `200`.

---

## 21) Акт сверки с контрагентом

### 21.1 `GET /api/warehouse/counterparties/{counterparty_id}/reconciliation/`
- Назначение: скачать акт сверки в PDF.
- Query:
  - `start` (обяз.)
  - `end` (обяз.)
  - `currency` (опц., default `KGS`)
  - `branch` (опц.)
- Ответ `200`: `application/pdf`.
- Ошибки: `400` при неверных датах.

### 21.2 `GET /api/warehouse/counterparties/{counterparty_id}/reconciliation/json/`
- Назначение: получить акт сверки в JSON.
- Query: как выше.
- Ответ `200`: opening/entries/totals/closing/debt.

---

## 22) Стандартные коды ошибок (для всех endpoint-ов)

- `400` — валидация/бизнес-ограничения
- `401` — неавторизован
- `403` — недостаточно прав или доступ к чужой company/branch
- `404` — объект не найден

Типовые сообщения:
- `"Document requires warehouse_from"`
- `"Document requires counterparty"`
- `"Нельзя изменять проведенный документ. Сначала отмените проведение."`
- `"Сумма должна быть больше 0."`

---

## 23) Рекомендации фронту

- Для создания/обновления документов отправляйте полный `items[]` единым запросом.
- Перед `post/unpost` всегда запрашивайте подтверждение пользователя.
- Для фото используйте только `multipart/form-data`.
- Не рассчитывайте итоги документа на фронте как источник истины: итоги и статусы финализирует сервер.
- Для агентских сценариев всегда учитывайте статус membership (`pending|active|rejected|removed`).
