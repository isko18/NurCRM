# Продажи в `apps/main`: полное описание (модели, логика, API)

Документ описывает **кассовые продажи маркета** в приложении `main`: сущности `Cart` / `Sale`, сервис `checkout_cart`, оплату, долг, возврат и связь с POS API.  
Практические эндпоинты и примеры тел запросов см. также в **`README_MARKET_POS.md`**.

---

## 0. Смена кассира (обязательно до POS)

Продажи `main` POS привязаны к модели **`construction.CashShift`** (кассовая смена). Без **открытой** смены (`status=open`) на выбранной **кассе** (`Cashbox`) для текущего пользователя нельзя начать продажу (`pos/sales/start/` вернёт ошибку).

### Открытие

- **`POST /api/construction/shifts/open/`**
- Тело: `{ "cashbox": "<uuid>", "opening_cash": "0.00" }` — см. подробности, филиал `?branch=`, поле `cashier`, идемпотентность и закрытие в **`README_MARKET_POS.md`**, раздел **«1) Смена»**.

Кратко: касса должна принадлежать той же **компании** и тому же **филиалу**, что и контекст запроса; кассир по умолчанию — **текущий пользователь**.

### Закрытие

- **`POST /api/construction/shifts/<shift_id>/close/`** с `{ "closing_cash": "<сумма>" }`.

### Связь с `Sale`

У записи **`Sale`** заполняются **`shift`** и **`cashbox`** (при наличии смены касса подтягивается из смены — см. `Sale.save()` в `models.py`).

---

## 1. Назначение

- **Продажа** фиксируется моделью **`Sale`** с позициями **`SaleItem`**.
- До оформления используется **`Cart`** с **`CartItem`** (черновик корзины кассира).
- Перенос корзины в продажу и списание остатков выполняет **`checkout_cart()`** в `apps/main/services/__init__.py`.
- Финальный статус и способ оплаты задаются методом **`Sale.mark_paid()`** после создания `Sale` (в POS — в `SaleCheckoutAPIView`).

Отдельно существуют **продажи агентов** (`checkout_agent_cart` в `services_agent_pos.py`, эндпоинты `/api/main/agents/me/...`) — та же модель `Sale`, но другой сценарий остатков (аллокации агента). Ниже акцент на **маркет POS**.

---

## 2. Модели данных

### 2.1 `Cart` (`apps/main/models.py`)

| Поле | Смысл |
|------|--------|
| `company`, `branch`, `user` | Контекст компании/филиала/кассира |
| `shift` | Открытая смена `construction.CashShift` (обязательна для типичного POS-флоу) |
| `status` | `active` → в работе; после `checkout_cart` — `checked_out` |
| `subtotal`, `discount_total`, `tax_total`, `total` | Итоги после `recalc()` |
| `order_discount_total`, `order_discount_percent` | Скидка на весь чек (либо сумма, либо %) |

Ограничения уникальности: одна активная корзина на пару `(shift, user)` при непустой смене (см. `Meta.constraints` в модели).

### 2.2 `CartItem`

| Поле | Смысл |
|------|--------|
| `product` | Может быть `null` для кастомных строк |
| `quantity`, `unit_price`, `line_discount` | Количество; базовая цена; скидка на строку (рублями) |
| `unique_together (cart, product)` | Одна строка на товар в корзине; повторное добавление увеличивает `quantity` |

Эффективная цена за единицу в корзине: `unit_price - line_discount / quantity`.  
Валидация: при отсутствии строковой скидки цена не ниже `product.purchase_price`.

### 2.3 `Sale`

| Поле | Смысл |
|------|--------|
| `company`, `branch`, `user` | Компания, филиал, кассир (часто из смены) |
| `shift`, `cashbox` | Смена и касса: при наличии `shift` касса подтягивается из смены (`save()` модели) |
| `client` | Покупатель (`main.Client`), опционально |
| `status` | `new` → после проведения оплаты: `paid` или `debt`; `canceled` — возврат |
| `subtotal`, `discount_total`, `tax_total`, `total` | Копия итогов из корзины на момент checkout |
| `payment_method` | `cash`, `transfer`, `debt`, банки (`mbank`, `optima`, …) |
| `cash_received`, `paid_at` | Для наличных — получено и сдача через свойство `change`; для долга — см. `mark_paid` |

**`Sale.clean()` / `Sale.save()`:**

- Если задан **`shift`**: жёстко синхронизируются `company`, `branch`, `cashbox` со сменой; `user` должен совпадать с кассиром смены при проверках.
- Если **`shift` нет**: обязательна **`cashbox`**, согласованная с компанией/филиалом.

### 2.4 `SaleItem`

| Поле | Смысл |
|------|--------|
| `name_snapshot`, `barcode_snapshot` | Зафиксированные на момент продажи название и штрихкод |
| `unit_price`, `quantity` | Цена за единицу (уже с учётом доли строковой скидки из корзины при checkout) |
| `purchase_price_snapshot` | Себестоимость на момент продажи (для маржи); заполняется в `SaleItem.save()` при **не** bulk-вставке |

Свойства `line_total`, `line_cogs` используются в аналитике (см. `analytics_market.py`).

---

## 3. Поток «корзина → продажа» (`checkout_cart`)

Файл: `apps/main/services/__init__.py`, функция **`checkout_cart(cart)`** (атомарная транзакция).

1. **`cart.recalc()`** — пересчёт сумм корзины.
2. Проверка: корзина не пуста; у корзины есть **`shift`** (иначе `ValueError`).
3. **`select_for_update`** по товарам — проверка **`Product.quantity`** на каждую позицию с товаром (`NotEnoughStock` при нехватке).
4. Создание **`Sale`** со статусом **`NEW`**, суммами из корзины, привязкой к `shift`, `cashbox`, кассиру из смены.
5. Формирование списка **`SaleItem`**: для каждой позиции корзины эффективная цена  
   `effective_unit = unit_price - line_discount / qty`.
6. **`SaleItem.objects.bulk_create(...)`** — вставка без вызова `save()` по каждой строке (особенность: логика снапшота себестоимости в `SaleItem.save()` при bulk_create может не отработать; детали зависят от версии кода и миграций).
7. Повторное списание **`Product.quantity`** и `bulk_update`.
8. По `transaction.on_commit` — вебхуки `product.updated` для изменённых товаров.
9. Удаление строк корзины, статус корзины **`CHECKED_OUT`**.

После возврата из `checkout_cart` POS вызывает **`sale.mark_paid(...)`** — см. §4.

---

## 4. Оплата: `Sale.mark_paid()`

Файл: `apps/main/models.py`, метод **`mark_paid(payment_method=..., cash_received=...)`**.

- Если **`payment_method == DEBT`**:
  - `status = debt`
  - `paid_at = None`, `cash_received = 0`
  - Продажа **не считается оплаченной** для кассы/смены до отдельной оплаты долга.
- Иначе:
  - `status = paid`, `paid_at = now`
  - Для **`cash`** — сохраняется `cash_received`, иначе для безнала `cash_received` обнуляется.

Свойство **`change`**: только для наличных, разница «получено − итог», неотрицательная.

---

## 5. Долг и возврат (API)

| Действие | Метод | Эндпоинт |
|----------|--------|----------|
| Оплатить продажу в долгу | `POST` | `/api/main/pos/sales/<sale_id>/pay-debt/` |
| Возврат (отмена) продажи | `POST` | `/api/main/pos/sales/<sale_id>/return/` |

**Оплата долга** (`SalePayDebtAPIView`): только если `sale.status == debt`; вызывается `mark_paid` с новым способом оплаты.

**Возврат** (`SaleReturnAPIView`): для статусов `paid` или `debt`; если есть **`AgentSaleAllocation`** — удаляются аллокации агента, иначе **`Product.quantity`** увеличивается на количества из строк; затем `sale.status = canceled`. Инвалидируется кэш аналитики маркета.

---

## 6. HTTP API (маркет POS) — карта

Базовый префикс: `/api/main/` (см. `apps/main/urls.py`).

| Назначение | Метод | Путь |
|------------|--------|------|
| Старт / получить активную корзину | `POST` | `pos/sales/start/` |
| Корзина (деталь, PATCH скидки на чек) | `GET/PATCH` | `pos/carts/<uuid>/` |
| Скан штрихкода | `POST` | `pos/sales/<cart_id>/scan/` |
| Добавить товар по id | `POST` | `pos/sales/<cart_id>/add-item/` |
| Кастомная позиция | `POST` | `pos/carts/<cart_id>/custom-item/` |
| Позиция корзины | `PATCH/DELETE` | `pos/carts/<cart_id>/items/<item_id>/` |
| Checkout | `POST` | `pos/sales/<cart_id>/checkout/` |
| Список / деталь продажи | `GET` | `pos/sales/`, `pos/sales/<sale_id>/` |
| Данные чека для печати | `GET` | `pos/sales/<sale_id>/receipt/` |
| JSON чек / накладная | `GET` | `sales/json/<sale_id>/receipt/`, `.../invoice/` |

Подробные тела запросов и коды ответов — в **`README_MARKET_POS.md`**.

---

## 7. Аналитика

- **`GET /api/main/analytics/market/`** — вкладки `sales`, `stock`, `cashboxes`, `shifts` (реализация: `analytics_market.py`).
- Учёт выручки и маржи опирается на **`Sale`** со статусом, попадающим в фильтры (обычно оплаченные продажи; см. код фильтрации по `status` и датам).

---

## 8. Карта исходных файлов

| Файл | Роль |
|------|------|
| `apps/main/models.py` | `Cart`, `CartItem`, `Sale`, `SaleItem` |
| `apps/main/services/__init__.py` | `checkout_cart`, `NotEnoughStock` |
| `apps/main/pos_views.py` | POS: start, scan, add-item, checkout, pay-debt, return, списки |
| `apps/main/pos_serializers.py` | Сериализаторы корзины и продажи |
| `apps/main/services_agent_pos.py` | Отдельный сценарий checkout для агентов |
| `apps/main/analytics_market.py` | Аналитика маркета |
| `apps/main/document.py`, `printers.py` | Чеки/накладные |
| `apps/construction/` | Модели и API **смен** (`CashShift`), без открытой смены POS обычно не работает |

---

## 9. Схема последовательности (кратко)

```
Открыть смену (construction API)
    → POST pos/sales/start/  (Cart active + shift)
    → scan / add-item / custom-item / PATCH cart
    → POST pos/sales/<cart_id>/checkout/
          → checkout_cart()  → Sale (NEW) + списание остатков + Cart checked_out
          → mark_paid()      → Sale (paid | debt)
    → (если debt) позже POST .../pay-debt/ → mark_paid → paid
    → (при необходимости) POST .../return/ → canceled + возврат остатков
```

---

## 10. Зависимости от других приложений

- **`construction`**: `CashShift`, `Cashbox` — привязка продажи к смене и кассе.
- **`users`**: компания, филиал, права; `MarketCashierOnlyMixin` на POS-вьюхах.

Если поведение на проде отличается от описания, смотрите актуальные версии перечисленных файлов — этот документ отражает структуру кодовой базы на момент добавления.
