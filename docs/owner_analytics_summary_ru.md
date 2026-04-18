# Сводные показатели (`summary`) — аналитика владельца

Источник расчётов в коде: `apps/main/analytics_owner_production.py` → `build_owner_analytics_payload()`.

Ответ эндпоинта `GET /api/main/owners/analytics/` содержит объект **`summary`** (и `period`, `charts`). Ниже — все ключи `summary`, типы и смысл.

## Период и филиал

- Показатели с пометкой **«за период»** считаются по `period`, `date_from`, `date_to` из запроса (см. [owner_analytics_api.md](./owner_analytics_api.md)).
- **Склад** (`stock_*`), **сырьё** (`raw_material_value`) и **число пользователей** (`users_count`) **не зависят от даты**: это снимок на «сейчас» по компании и выбранному филиалу (логика `branch` как в списках товаров / сырья).

## Таблица полей `summary`

| Ключ | Тип в JSON | Описание |
|------|------------|----------|
| `users_count` | integer | Пользователи компании (`User`, `company_id`). |
| `transfers_count` | integer | Число перемещений (`ManufactureSubreal`) за период, с учётом филиала. |
| `acceptances_count` | integer | Число приёмок (`Acceptance`) за период. |
| `items_transferred` | number | Сумма `qty_transferred` по тем же перемещениям за период. |
| `defective_items` | number | Сумма `qty` по `ReturnFromAgent` со статусом `ACCEPTED` и `returned_at` в периоде. |
| `sales_count` | integer | Число оплаченных продаж (`Sale`, статус `PAID`) за период. |
| `sales_amount` | string (decimal) | Сумма полей `total` по этим продажам. |
| `discounts_total` | string (decimal) | Сумма `discount_total` по оплаченным продажам за период. |
| `revenue` | string (decimal) | Выручка по строкам чека: `Σ(quantity × unit_price − line_discount)` по `SaleItem` оплаченных продаж за период. |
| `cost_of_goods_sold` | string (decimal) | Себестоимость: `Σ(quantity × закупка_строки)`, закупка = `purchase_price_snapshot`, иначе текущая `product.purchase_price`. |
| `gross_profit` | string (decimal) | `revenue − cost_of_goods_sold`. |
| `gross_margin_percent` | string (decimal) | `gross_profit / revenue × 100` при `revenue > 0`, иначе `0`. |
| `stock_value` | string (decimal) | Закупочная оценка склада: `Σ(quantity × purchase_price)` по **`Product`**, компания + филиал; **без** позиций с `kind = service`. |
| `stock_purchase_value` | string (decimal) | То же значение, что `stock_value` (дубль для карточки UI). |
| `stock_retail_value` | string (decimal) | `Σ(quantity × price)` по тем же товарам. |
| `raw_material_value` | string (decimal) | `Σ(quantity × price)` по **`ItemMake`**, компания + филиал. |
| `accounts_receivable` | string (decimal) | Дебиторская: `accounts_receivable_client_deals + accounts_receivable_pos_sales`. |
| `accounts_receivable_client_deals` | string (decimal) | Остаток по сделкам `ClientDeal` с `kind = debt`: `Σ((amount − prepayment) − оплачено_по_графику)`. |
| `accounts_receivable_pos_sales` | string (decimal) | Сумма `total` по продажам в статусе «в долг» (`Sale`, `DEBT`). |
| `accounts_payable` | string (decimal) | Кредиторская: складские контрагенты/документы + при необходимости building debt ledger (см. код вьюхи). |
| `total_debt` | string (decimal) | **Только** остаток по рассрочке CRM (`accounts_receivable_client_deals`); для обратной совместимости. Не включает POS-долг. |

## Связь с детализацией карточек

Эндпоинт списков для модалок: [analytics_cards_details_api.md](./analytics_cards_details_api.md) (`GET /api/main/analytics/cards/details/?card=…`).  
Склад в детализации использует те же правила компании/филиала и **исключает услуги** (`kind != service`), как и `summary`.

## Пример фрагмента `summary`

Строковые поля — десятичные суммы с точкой (`"1234.56"`).

```json
{
  "users_count": 5,
  "transfers_count": 10,
  "acceptances_count": 8,
  "items_transferred": 100,
  "defective_items": 2,
  "sales_count": 20,
  "sales_amount": "50000.00",
  "discounts_total": "100.00",
  "revenue": "49900.00",
  "cost_of_goods_sold": "30000.00",
  "gross_profit": "19900.00",
  "gross_margin_percent": "39.88",
  "stock_value": "120000.00",
  "stock_purchase_value": "120000.00",
  "stock_retail_value": "180000.00",
  "raw_material_value": "40000.00",
  "accounts_receivable": "15000.00",
  "accounts_receivable_client_deals": "12000.00",
  "accounts_receivable_pos_sales": "3000.00",
  "accounts_payable": "5000.00",
  "total_debt": "12000.00"
}
```
