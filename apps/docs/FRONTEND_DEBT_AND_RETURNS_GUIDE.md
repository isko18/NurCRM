# Инструкция для Frontend: Продажи в долг, Возвраты и Сделки клиентов (NurCRM)

**Версия API:** 2.0 (актуализировано: Август 2026)  
**Область:** POS Касса, Агентские продажи, CRM карточка клиента

---

## 📌 Краткий обзор ключевых правил

1. **Бэкенд сам создаёт сделку при продаже в долг.**  
   Фронтенду **НЕ НУЖНО** делать второй запрос `POST /api/main/clients/{client_id}/deals/` после успешного чекаута на кассе.
2. **Бэкенд сам обнуляет и отменяет долг при возврате товара.**  
   При вызове возврата чека (`/return/`) связанная сделка в CRM автоматически уменьшается или переходит в статус **«Отменён»** (`kind: "canceled"`).
3. **Блок `debt_adjustment` в ответе на возврат.**  
   Ответ эндпоинта возврата содержит исчерпывающие данные о том, на сколько уменьшился долг и нужно ли выдать клиенту наличные деньги.

---

## 1. Продажа в долг на кассе (POS Checkout)

### Эндпоинты:
- **Обычная касса:** `POST /api/main/pos/sales/{cart_id}/checkout/`
- **Агентская касса:** `POST /api/main/agents/me/sales/{cart_id}/checkout/`

### Запрос (Request Body):
```json
{
  "payment_method": "debt",
  "client_id": "31f0a7b9-2f97-43bf-910c-bc5d0329ec0f",
  "print_receipt": true
}
```

> ⚠️ **ВАЖНО:**  
> При `payment_method = "debt"` (или при передаче `"method": "debt"` в массиве `payments`) параметр **`client_id` обязателен**. При его отсутствии API вернет ошибку `400 Bad Request`.

### Ответ (Response 201 Created):
```json
{
  "sale_id": "51407296-42d2-46e8-bcb8-bc9640574c50",
  "status": "debt",
  "total": "40.00",
  "subtotal": "40.00",
  "client": "31f0a7b9-2f97-43bf-910c-bc5d0329ec0f",
  "client_name": "Бека",
  "payment_method": "debt"
}
```

### 🔴 ЧТО ДОЛЖЕН И НЕ ДОЛЖЕН ДЕЛАТЬ ФРОНТЕНД:
- ❌ **НЕ ВЫЗЫВАТЬ:** `POST /api/main/clients/{client_id}/deals/` после чекаута!
- ✅ **Бэкенд автоматически** создаёт `ClientDeal` с типом `kind: "debt"` и связывает её с продажей `sale_id`.

---

## 2. Возврат товара, купленного в долг (Sale Return)

### Эндпоинты:
- **Маркет (POS):** `POST /api/main/pos/sales/{sale_id}/return/`
- **Агенты:** `POST /api/main/agents/me/sales/{sale_id}/return/`

### Тело запроса (для полного возврата — передать пустой объект или `items: null`):
```json
{}
```

### Ответ (Response 200 OK):
```json
{
  "id": "51407296-42d2-46e8-bcb8-bc9640574c50",
  "status": "canceled",
  "total": "0.00",
  "debt_adjustment": {
    "deal_id": "7d9606ad-8e63-4274-a26b-54f80b538d2f",
    "legacy_debt_id": null,
    "reduced_by": "40.00",
    "remaining_debt_before": "40.00",
    "remaining_debt": "0.00",
    "deal_status": "canceled",
    "installments_updated": 1,
    "cash_refund_due": "0.00",
    "reason": "sale_return"
  }
}
```

### 💡 Поля объекта `debt_adjustment`:

| Поле | Тип | Описание |
|---|---|---|
| `deal_id` | `string` | ID сделки `ClientDeal`, которая была откорректирована. |
| `reduced_by` | `string` | Сумма, на которую уменьшился долг клиента. |
| `remaining_debt_before` | `string` | Остаток долга ДО возврата. |
| `remaining_debt` | `string` | Остаток долга ПОСЛЕ возврата. |
| `deal_status` | `string` | `"canceled"` — сделка отменена (полный возврат); `"closed"` — долг погашен полностью; `"open"` — остался долг после частичного возврата. |
| `cash_refund_due` | `string` | **Сумма к выдаче клиенту из кассы наличными.** Появляется, если возврат превышает остаток долга. |

### 🔴 ЛОГИКА ОТОБРАЖЕНИЯ НА ФРОНТЕНДЕ ПРИ ВОЗВРАТЕ:
1. Выполнить `POST .../return/`.
2. Если `debt_adjustment != null`:
   - Показать уведомление: *«Долг клиента уменьшен на {reduced_by} сом. Текущий остаток долга: {remaining_debt} сом.»*
   - Если `cash_refund_due > 0`: показать плашку *«Выдать клиенту наличными: {cash_refund_due} сом»*.
3. Обновить список сделок и KPI клиента (`/api/main/clients/{client_id}/kpis/`).

---

## 3. Отображение сделок в карточке клиента (CRM Client Detail)

### Эндпоинт получения сделок:
- `GET /api/main/clients/{client_id}/deals/`
- `GET /api/main/deals/?client={client_id}`

### Структура объекта сделки (`ClientDeal`):
```json
{
  "id": "7d9606ad-8e63-4274-a26b-54f80b538d2f",
  "client": "31f0a7b9-2f97-43bf-910c-bc5d0329ec0f",
  "client_full_name": "Бека",
  "title": "Продажа в долг №51407296-42d2-46e8-bcb8-bc9640574c50",
  "kind": "canceled",
  "kind_display": "Отменён",
  "amount": "0.00",
  "prepayment": "0.00",
  "debt_amount": "0.00",
  "remaining_debt": "0.00",
  "sale": "51407296-42d2-46e8-bcb8-bc9640574c50",
  "created_at": "2026-08-14T12:55:53.394321Z"
}
```

### 🎨 Значения поля `kind` и `kind_display` для отображения бейджей/тегов:

| `kind` | `kind_display` | Цвет бейджа в UI | Описание |
|---|---|---|---|
| `"debt"` | `"Долг"` | 🔴 Красный | Активный долг / рассрочка. |
| `"canceled"` | `"Отменён"` | ⚪️ Серый / Перечёркнутый | Сделка отменена из-за возврата товара. |
| `"sale"` | `"Продажа"` | 🟢 Зелёный | Оплаченная продажа. |
| `"amount"` | `"Сумма договора"` | 🔵 Синий | Договорная сделка. |
| `"prepayment"` | `"Предоплата"` | 🩵 Бирюзовый | Внесённый аванс. |
