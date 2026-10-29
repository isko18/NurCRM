# Инвентаризация товаров (CRM, `Product.quantity`)

Акты инвентаризации в приложении **`main`**: в черновике задаются товары и **учётные** количества (`quantity_fact`), после проведения остаток **`Product.quantity`** выравнивается под факт.

Префикс: **`/api/main/`**.

## Эндпоинты

| Метод | URL | Описание |
|--------|-----|----------|
| `GET` | `/api/main/inventory/sessions/` | Список актов |
| `POST` | `/api/main/inventory/sessions/` | Создать черновик |
| `GET` | `/api/main/inventory/sessions/<uuid>/` | Детали акта со строками |
| `POST` | `/api/main/inventory/sessions/<uuid>/apply/` | Провести: записать остатки и зафиксировать «было» |
| `POST` | `/api/main/inventory/sessions/<uuid>/cancel/` | Отменить черновик |

Доступ: авторизованный пользователь, фильтр **компания/филиал** как у остальных сущностей `main` (`CompanyBranchRestrictedMixin`).

---

## Создание черновика

```http
POST /api/main/inventory/sessions/
Content-Type: application/json
Authorization: Bearer <token>

{
  "note": "Комментарий (необязательно)",
  "items": [
    { "product_id": "<uuid Product>", "quantity_fact": "12.50" },
    { "product_id": "<uuid>", "quantity_fact": "0.00" }
  ]
}
```

- Товары должны быть **доступны** в текущей компании/филиале (как в списке товаров).
- Один и тот же `product_id` в одном запросе **нельзя** указать дважды.

Статус нового акта: **`draft`**.

---

## Проведение

```http
POST /api/main/inventory/sessions/<uuid>/apply/
Content-Type: application/json

{
  "allow_negative": false
}
```

- Для каждой строки: в поле строки пишется **`quantity_before`** (остаток до проведения), у **`Product`** выставляется **`quantity_fact`**.
- По умолчанию **отрицательный** `quantity_fact` запрещён; чтобы разрешить, передайте **`"allow_negative": true`**.
- Если строк не осталось, акт переводится в **`canceled`** (краевой случай).

После проведения статус акта: **`applied`**, проставляется **`applied_at`**.

---

## Отмена черновика

```http
POST /api/main/inventory/sessions/<uuid>/cancel/
```

Только из статуса **`draft`** → **`canceled`**. Остатки товаров не меняются.

---

## Ответ (строки)

У каждой строки после проведения можно смотреть:

- `quantity_before` — было;
- `quantity_fact` — установлено;
- `quantity_delta` — разница (строкой в JSON), если `quantity_before` уже заполнен.

---

## Код

- Вьюхи: `apps/main/inventory_views.py`
- Модели: `apps/main/models.py` → `ProductInventorySession`, `ProductInventoryItem`
- Сериализаторы: `apps/main/serializers.py`
- URL: `apps/main/urls.py` (имена `product-inventory-session-*`)

Миграции: `python manage.py makemigrations main` и `migrate`.
