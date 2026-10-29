# Склад — Агент: общий доступ к складам (несколько или все)

**Что изменилось:** при включённом общем доступе (`common_access_enabled=true`) владелец может дать агенту доступ:
- к **нескольким складам** — поле `common_warehouses` (массив uuid);
- либо ко **всем складам компании и всем их товарам** — флаг `common_all_warehouses=true` (перечислять склады не нужно).

Старое поле `common_warehouse` (один склад) продолжает работать.

**Статус:** бэкенд готов. **Перед проверкой на стенде применить миграцию** (`makemigrations warehouse && migrate`).

---

## 1. Эндпоинты (без изменений в путях)

| Метод и путь | Назначение |
|---|---|
| `POST /warehouse/agents/company-memberships/` | Владелец напрямую назначает агента (без заявки) |
| `PATCH /warehouse/agents/company-requests/{id}/common-access/` | Владелец меняет складской доступ активного агента |

Оба доступны только владельцу/админу.

## 2. Тело запроса

### Вариант А — конкретные склады

```json
{
  "common_access_enabled": true,
  "common_warehouses": ["8f5d5f2c-…", "1a2b3c4d-…", "9e8d7c6b-…"],
  "can_sell_wholesale": true
}
```

### Вариант Б — все склады и все товары

```json
{
  "common_access_enabled": true,
  "common_all_warehouses": true
}
```

Для `company-memberships/` дополнительно обязателен `user` (uuid пользователя).

Поля:

| Поле | Тип | Правило |
|---|---|---|
| `common_access_enabled` | bool | Включает общий доступ к складам |
| `common_all_warehouses` | bool | **Доступ ко всем складам компании и всем их товарам.** При `true` список складов не нужен и очищается |
| `common_warehouses` | `[uuid, …]` | Конкретные склады. **Непустой, если доступ включён и `common_all_warehouses` не true** (иначе 400). Все — той же компании (иначе 400) |
| `common_warehouse` | uuid \| null | Legacy: один склад. Эквивалентно `common_warehouses: [uuid]` |
| `assigned_warehouse` | uuid \| null | Как раньше; на набор общего доступа не влияет |

Правила разрешения набора:

- `common_all_warehouses=true` → все склады компании, `common_warehouses` игнорируется.
- Иначе передан `common_warehouses` — берётся он.
- Иначе передан `common_warehouse` — берётся как список из одного.
- Иначе (ничего не передано) — остаётся текущее состояние агента.
- `common_access_enabled=false` → сбрасывается и список, и флаг `common_all_warehouses`.

> Прежнее ограничение «общий доступ только к назначенному складу» **снято** — владелец выбирает любой набор складов компании либо сразу все.

## 3. Ответ

`common_all_warehouses` — флаг «все склады». `common_warehouses` — полный набор конкретных складов (пуст, если включён режим «все»). `common_warehouse` — первый из набора (для обратной совместимости).

```json
{
  "id": "798fb275-…",
  "company": "fb64c942-…",
  "user": "88e1ffe2-…",
  "user_display": "Мирлан Мейманбеков",
  "status": "active",
  "assigned_warehouse": null,
  "common_access_enabled": true,
  "common_all_warehouses": false,
  "common_warehouse": "8f5d5f2c-…",
  "common_warehouses": ["8f5d5f2c-…", "1a2b3c4d-…"],
  "can_sell_wholesale": true,
  "can_sell_without_approval": false,
  "created_at": "2026-07-15T13:21:40+06:00",
  "updated_at": "2026-07-17T00:55:10+06:00"
}
```

При режиме «все склады» ответ: `common_all_warehouses: true`, `common_warehouses: []`, `common_warehouse: null`.

## 4. На что это влияет

- **Остатки агента** `GET /warehouse/agents/my/products/` при общем доступе показывают товары со всех складов набора; при `common_all_warehouses=true` — со всех складов компании. `warehouse_id` в каждой строке указывает склад позиции. Пагинация и `?search=` — как прежде.
- Продажа с общего остатка разрешена, если склад входит в набор агента (или включён режим «все склады»).

## 5. Рекомендации фронту

- В форме доступа агента: чекбокс «Все склады» (→ `common_all_warehouses`) + мультиселект складов (→ `common_warehouses`). При включённом чекбоксе мультиселект прячьте/делайте неактивным.
- Логика отправки:
  - выбран «Все склады» → `{ common_access_enabled: true, common_all_warehouses: true }`;
  - выбраны конкретные → `{ common_access_enabled: true, common_all_warehouses: false, common_warehouses: [...] }`;
  - доступ выключен → `{ common_access_enabled: false }`.
- `common_warehouse` отдельно передавать не нужно — проставится сам.
- Старые агенты с одним складом продолжают работать: их склад читается через fallback и приходит и в `common_warehouse`, и в `common_warehouses`.

## 6. Ошибки

| Ситуация | Ответ |
|---|---|
| Доступ включён, `common_all_warehouses` не true, пустой `common_warehouses` | 400 `{"common_warehouses": ["Укажите хотя бы один склад или включите common_all_warehouses."]}` |
| Склад из набора — другой компании | 400 `{"common_warehouses": ["Склад <id> принадлежит другой компании."]}` |
| Запрос не от владельца/админа | 403 |
