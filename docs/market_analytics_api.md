# Market Analytics API - Документация

## Базовый URL

```
GET /api/v1/main/analytics/market/
```

## Общие параметры

| Параметр | Тип | Описание | Обязательный |
|----------|-----|----------|--------------|
| `tab` | string | Раздел аналитики | Да |
| `branch` | uuid | ID филиала для фильтрации | Нет |
| `period_start` | date | Начало периода (YYYY-MM-DD) | Нет |
| `period_end` | date | Конец периода (YYYY-MM-DD) | Нет |
| `limit` | integer | Лимит записей (без лимита = все) | Нет |
| `include_global` | boolean | Включить глобальные записи | Нет |

### Доступные разделы (tab)
- `sales` - Продажи
- `stock` - Склад  
- `cashboxes` - Кассы
- `shifts` - Смены
- `products` - Товары (расширенная аналитика)
- `users` - Сотрудники (расширенная аналитика)
- `finance` - Финансы (расширенная аналитика)

---

## 1. Продажи (tab=sales)

### Запрос
```http
GET /api/v1/main/analytics/market/?tab=sales&period_start=2024-01-01&period_end=2024-12-31
```

### Ответ
```json
{
  "tab": "sales",
  "cards": {
    "revenue": "1500000.00",
    "transactions": 1250,
    "avg_check": "1200.00",
    "clients": 450,
    "cogs": "900000.00",
    "gross_profit": "600000.00",
    "margin_percent": 40.0
  },
  "charts": {
    "sales_dynamics": [
      {"date": "2024-01-01", "value": "15000.00"}
    ],
    "payment_methods": [
      {"method": "cash", "count": 500, "total": "600000.00"},
      {"method": "card", "count": 600, "total": "720000.00"}
    ]
  }
}
```

---

## 2. Товары (tab=products) ✨

### Запрос
```http
# Все товары
GET /api/v1/main/analytics/market/?tab=products

# С лимитом
GET /api/v1/main/analytics/market/?tab=products&limit=50
```

### Ответ
```json
{
  "cards": {
    "stock_value": "2500000.00",
    "low_stock_count": 71
  },
  "tables": {
    "top_by_revenue": [{
      "product_id": "uuid-123",
      "name": "Товар А",
      "code": "0001",
      "article": "ART-001",
      "barcode": "1234567890",
      "category": "Электроника",
      "brand": "Sony",
      "current_stock": "150.000",
      "price": "5000.00",
      "purchase_price": "3500.00",
      "revenue": "500000.00",
      "qty_sold": "100.000",
      "transactions": 85
    }],
    "top_by_quantity": [{
      "product_id": "uuid-456",
      "name": "Товар Б",
      "qty_sold": "250.000"
    }],
    "categories": [{
      "category_id": "uuid-789",
      "category": "Электроника",
      "revenue": "800000.00",
      "qty_sold": "450.000",
      "products_count": 45,
      "transactions": 320
    }],
    "brands": [{
      "brand_id": "uuid-012",
      "brand": "Sony",
      "revenue": "600000.00",
      "products_count": 15
    }],
    "low_stock_products": [{
      "id": "uuid-345",
      "name": "Товар В",
      "code": "0003",
      "quantity": "2.000",
      "price": "800.00",
      "purchase_price": "500.00",
      "status": "low"
    }]
  }
}
```

### Особенности
- `low_stock_products` - **ВСЕ** товары с остатком ≤5
- Без лимита возвращается **полный список** (например, все 71 товар)
- Каждый товар содержит полную детализацию

---

## 3. Склад (tab=stock)

### Запрос
```http
GET /api/v1/main/analytics/market/?tab=stock
```

### Ответ
```json
{
  "cards": {
    "total_products": 450,
    "categories_count": 25,
    "inventory_value": "2500000.00",
    "low_stock_count": 71,
    "turnover_days": 45.2
  },
  "charts": {
    "category_distribution": [
      {"name": "Электроника", "percent": 35.5, "count": 160}
    ],
    "movement": [
      {"date": "2024-01-01", "units": "125.000"}
    ]
  }
}
```

---

## 4. Кассы (tab=cashboxes)

### Запрос
```http
GET /api/v1/main/analytics/market/?tab=cashboxes
```

### Ответ
```json
{
  "cards": {
    "total_cashboxes": 5,
    "active_cashboxes": 3,
    "total_income": "1500000.00",
    "total_expense": "350000.00"
  },
  "tables": {
    "cashboxes": [{
      "name": "Касса 1",
      "sales_count": 450,
      "sales_total": "540000.00",
      "balance": "490000.00"
    }]
  }
}
```

---

## 5. Смены (tab=shifts)

### Запрос
```http
GET /api/v1/main/analytics/market/?tab=shifts
```

### Ответ
```json
{
  "cards": {
    "total_shifts": 125,
    "open_shifts": 3,
    "total_revenue": "1500000.00",
    "avg_revenue_per_shift": "12295.08"
  },
  "tables": {
    "active_shifts": [{
      "cashier": "Иванов",
      "cashbox": "Касса 1",
      "opened_at": "2024-01-15T09:00:00",
      "sales": "125000.00"
    }],
    "best_cashiers": [{
      "place": 1,
      "cashier": "Петрова",
      "shifts": 25,
      "sales": "350000.00"
    }]
  }
}
```

---

## 6. Сотрудники (tab=users) ✨

### Запрос
```http
# Все сотрудники
GET /api/v1/main/analytics/market/?tab=users

# С лимитом
GET /api/v1/main/analytics/market/?tab=users&limit=20
```

### Ответ
```json
{
  "cards": {
    "total": 125,
    "closed": 120,
    "open": 5,
    "discrepancies_count": 12
  },
  "tables": {
    "users_performance": [{
      "user_id": "uuid-user-1",
      "user": "Иванов Иван",
      "email": "ivan@example.com",
      "phone": "+7 700 123 4567",
      "revenue": "450000.00",
      "transactions": 320,
      "avg_check": "1406.25"
    }],
    "shift_discrepancies": [{
      "shift_id": "uuid-shift-1",
      "cashier": "Петрова",
      "cashbox": "Касса 1",
      "opened_at": "2024-01-15T09:00:00",
      "closed_at": "2024-01-15T18:00:00",
      "expected_cash": "125000.00",
      "closing_cash": "124950.00",
      "diff": "-50.00",
      "type": "shortage"
    }]
  }
}
```

### Особенности
- `users_performance` - **ВСЕ** сотрудники с продажами
- `shift_discrepancies` - **ВСЕ** расхождения в кассах
- Полная детализация по каждой смене

---

## 7. Финансы (tab=finance) ✨

### Запрос
```http
# Все транзакции
GET /api/v1/main/analytics/market/?tab=finance

# С лимитом
GET /api/v1/main/analytics/market/?tab=finance&limit=100
```

### Ответ
```json
{
  "cards": {
    "income_total": "1500000.00",
    "expense_total": "850000.00",
    "net_flow": "650000.00",
    "income_count": 245,
    "expense_count": 189
  },
  "tables": {
    "expense_breakdown": [{
      "name": "Аренда",
      "total": "240000.00",
      "count": 12
    }],
    "income_breakdown": [{
      "name": "Продажи",
      "total": "1200000.00",
      "count": 200
    }],
    "expense_items": [{
      "id": "uuid-exp-1",
      "name": "Аренда офиса",
      "amount": "50000.00",
      "cashbox": "Касса 1",
      "created_at": "2024-01-01T10:00:00",
      "created_by": "Администратор",
      "description": "Оплата за январь"
    }],
    "income_items": [{
      "id": "uuid-inc-1",
      "name": "Продажа товаров",
      "amount": "125000.00",
      "created_at": "2024-01-01T18:00:00"
    }]
  }
}
```

### Особенности
- `expense_items` / `income_items` - **ВСЕ** транзакции
- Детализация по каждой операции
- Связь с кассой и сменой

---

## Примеры использования

### 1. Все товары с низким остатком
```bash
curl -X GET "https://api.example.com/api/v1/main/analytics/market/?tab=products" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Результат:** Массив `low_stock_products` содержит все 71 товар

### 2. Топ-20 сотрудников
```bash
curl -X GET "https://api.example.com/api/v1/main/analytics/market/?tab=users&limit=20" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Все расходы за месяц
```bash
curl -X GET "https://api.example.com/api/v1/main/analytics/market/?tab=finance&period_start=2024-01-01&period_end=2024-01-31" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 4. Продажи по способам оплаты
```bash
curl -X GET "https://api.example.com/api/v1/main/analytics/market/?tab=sales" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Результат:** `charts.payment_methods` содержит разбивку по cash/card/transfer

---

## Пагинация

### Без лимита (по умолчанию)
```http
GET /api/v1/main/analytics/market/?tab=products
```
Возвращает **ВСЕ** записи (71, 200, 1000+ товаров)

### С лимитом
```http
GET /api/v1/main/analytics/market/?tab=products&limit=50
```
Возвращает первые 50 записей

---

## Кэширование

- Все запросы кэшируются на **5 минут**
- Кэш ключ: `company_id` + `branch_id` + `tab` + query hash
- Для сброса кэша: изменить любой параметр запроса

---

## Права доступа

- Требуется аутентификация (`IsAuthenticated`)
- Пользователь должен быть привязан к компании
- **Owner/Admin:** видят все филиалы
- **Обычные пользователи:** видят только свой филиал

---

## Коды ошибок

| Код | Описание |
|-----|----------|
| 400 | Неизвестный tab или неверные параметры |
| 401 | Не авторизован |
| 403 | У пользователя нет компании |
| 500 | Внутренняя ошибка сервера |

---

## Changelog

### v2.0 (2024-02-10) ✨ НОВЫЕ ВОЗМОЖНОСТИ

#### Products
- ✅ `low_stock_products` - ВСЕ товары с низким остатком (не топ-10)
- ✅ Детализация: код, артикул, штрихкод, категория, бренд
- ✅ Параметр `limit` для пагинации

#### Sales
- ✅ `payment_methods` - разбивка по способам оплаты

#### Users
- ✅ ВСЕ сотрудники с контактами (email, phone)
- ✅ ВСЕ расхождения в кассах с полными деталями
- ✅ `discrepancies_count` - общее количество расхождений

#### Finance
- ✅ `expense_items` / `income_items` - ВСЕ транзакции
- ✅ `income_count` / `expense_count` - счётчики
- ✅ Связь с кассой, сменой, создателем

### v1.0
- Базовая аналитика: sales, stock, cashboxes, shifts

---

## Техническая информация

### Файл
`apps/main/analytics_market.py`

### Модели
- `Sale`, `SaleItem` - продажи
- `Product`, `ProductCategory`, `ProductBrand` - товары
- `CashFlow` - финансы
- `CashShift` - смены
- `User`, `Company`, `Branch` - пользователи

### Константы
- `Z_MONEY = Decimal("0.00")` - нулевая сумма
- `Z_QTY = Decimal("0.000")` - нулевое количество
