# Агентский функционал в `apps/main`

## Что входит в агентский модуль

В `apps/main` логика по агентам покрывает не одну сущность, а целый цикл работы:

1. выдача товара агенту со склада;
2. приём агентом выданного товара;
3. возвраты от агента;
4. продажи агента через POS;
5. заявки агента на получение товара;
6. остатки и аналитика по агенту;
7. owner-view по товарам и аналитике агентов.

Ниже собрана вся связанная логика из `models.py`, `views.py`, `pos_views.py`, `services_agent_pos.py`, `serializers.py`, `analytics_agent.py` и `urls.py`.

---

## Основные модели

### `ManufactureSubreal`

Передача товара агенту.

Ключевые поля:

- `company`, `branch`
- `user` - кто выдал товар
- `agent` - кому выдали
- `product`
- `qty_transferred` - сколько передано
- `qty_accepted` - сколько агент реально принял
- `qty_returned` - сколько потом вернул
- `is_sawmill` - флаг авто-приёма при создании
- `status`: `open` / `closed`
- `external_ref` - идемпотентный внешний ключ

Важные вычисления:

- `qty_remaining = qty_transferred - qty_accepted`
- базовый `qty_on_agent = qty_accepted - qty_returned`
- реальный остаток на руках считается через `get_qty_on_hand_with_sales()`:
  - `accepted - returned - sold(paid/debt) - pending_returns`

Это главный источник правды по тому, что и в каком количестве находится у агента.

### `Acceptance`

Событие приёма товара по конкретной передаче `ManufactureSubreal`.

Что делает:

- создаётся по `subreal`;
- увеличивает `subreal.qty_accepted`;
- если остаток к приёму стал `0`, передача закрывается через `try_close()`.

### `ReturnFromAgent`

Заявка агента на возврат товара по передаче.

Статусы:

- `pending` - ожидает обработки;
- `accepted` - принят;
- `rejected` - отклонён.

Что важно:

- при создании проверяется реальный остаток у агента с учётом уже проданного и уже зарезервированного под другие pending-возвраты;
- при `accept()`:
  - товар возвращается на основной склад (`Product.quantity += qty`);
  - `subreal.qty_returned += qty`;
  - статус становится `accepted`;
- при `reject()`:
  - товар на склад не возвращается;
  - меняется только статус.

### `AgentSaleAllocation`

Связка продажи агента с конкретной передачей (`ManufactureSubreal`).

Нужна для:

- FIFO-списания с партий агента;
- точного расчёта остатков на руках;
- возврата агентской продажи без возврата товара на основной склад.

### `AgentRequestCart`

Заявка агента на получение товара.

Статусы:

- `draft`
- `submitted`
- `approved`
- `rejected`

Смысл:

- агент набирает товары в черновик;
- отправляет владельцу;
- владелец одобряет или отклоняет;
- при одобрении товар списывается со склада и создаются реальные `ManufactureSubreal`.

### `AgentRequestItem`

Позиция внутри `AgentRequestCart`.

Хранит:

- `quantity_requested`
- `gift_quantity`
- `total_quantity`
- `price_snapshot`
- `subreal` - ссылка на фактическую передачу после approve

Подарки и итоговое количество фиксируются не в момент добавления строки, а при `submit()`.

---

## Базовые бизнес-правила

### Фильтрация компании и филиала

Почти все agent endpoints работают через `CompanyBranchRestrictedMixin`.

Это означает:

- запросы ограничиваются компанией пользователя;
- при активном филиале данные тоже режутся по филиалу;
- объект из другой компании/чужого филиала получить нельзя.

### Права доступа

Общий минимум:

- `IsAuthenticated`

Дополнительно:

- agent-only поведение строится через фильтрацию по `request.user`;
- часть owner/admin действий проверяется через `_is_owner_like(...)`;
- POS-продажи агента используют `MarketCashierOnlyMixin`.

### Формула фактического остатка у агента

Во всех критичных местах система опирается не просто на `qty_accepted - qty_returned`, а на более точную формулу:

`accepted - returned - sold(paid/debt) - pending_reserved`

Где:

- `sold(paid/debt)` берётся из `AgentSaleAllocation`;
- `pending_reserved` - это возвраты агента в статусе `pending`, чтобы агент не смог одновременно и продать, и вернуть один и тот же остаток.

### Webhook на изменение товара

После движений, влияющих на склад, код пытается вызвать `send_product_webhook(..., "product.updated")`.

Это происходит, например, при:

- передаче товара агенту;
- bulk-выдаче агенту;
- approve возврата;
- approve заявки агента.

---

## Поток 1. Выдача товара агенту

### `GET/POST /api/main/subreals/`

Список и создание передач агентам.

`POST`:

- создаёт `ManufactureSubreal`;
- валидирует компанию товара и агента;
- при `qty_transferred > 0` блокирует продукт на складе;
- уменьшает `Product.quantity`;
- если `is_sawmill = true`, автоматически создаёт `Acceptance` на весь остаток.

Фильтры списка:

- `agent`
- `product`
- `status`
- `created_at`

Поиск:

- `product__name`
- имя/фамилия/username агента

Сортировка:

- `created_at`
- `qty_transferred`
- `qty_accepted`
- `status`

### `GET/PATCH/PUT/DELETE /api/main/subreals/<uuid:pk>/`

Работа с конкретной передачей.

Что важно:

- update не должен подменять компанию/филиал;
- `is_sawmill` после создания менять нельзя;
- модель сама запрещает некорректные комбинации количества.

### `POST /api/main/subreals/bulk/`

Массовая выдача товаров одному агенту.

Особенности:

- принимает `agent` и массив `items`;
- дубли по одному продукту схлопываются;
- склад уменьшается по каждой позиции;
- создаётся набор `ManufactureSubreal`;
- для `is_sawmill=true` выполняется авто-приём.

---

## Поток 2. Приём товара агентом

### `GET/POST /api/main/acceptances/`

Список и создание событий приёма.

`POST`:

- принимает `subreal` и `qty`;
- разрешён только если передача ещё `open`;
- нельзя принять больше, чем `subreal.qty_remaining`;
- при создании:
  - увеличивается `qty_accepted`;
  - при полном приёме передача закрывается.

### `GET/DELETE /api/main/acceptances/<uuid:pk>/`

Получение или удаление конкретного события приёма.

---

## Поток 3. Возвраты от агента

### `GET/POST /api/main/returns/`

Общий список и создание возвратов.

`POST`:

- создаёт `ReturnFromAgent`;
- `returned_by = request.user`;
- проверяет, что возврат создаётся только по своей передаче и только в рамках своей компании;
- если выбранная партия уже не подходит, сериализатор может автоматически подобрать другую партию того же товара, где у агента ещё есть остаток.

`GET` дополнительно возвращает `returns_summary`:

- `pending_count`
- `pending_qty`

Фильтры:

- `subreal`
- `returned_by`
- `returned_at`
- `status`

Сортировка:

- `returned_at`
- `qty`
- `id`

### `GET/POST /api/main/agents/me/returns/`

То же самое, но только по возвратам текущего агента.

Особенности:

- `GET` показывает только свои возвраты;
- `POST` создаёт возврат только от лица текущего пользователя;
- в ответе списка тоже есть `returns_summary`.

### `GET/DELETE /api/main/returns/<uuid:pk>/`

Получение или удаление конкретного возврата.

### `POST /api/main/returns/<uuid:pk>/approve/`

Подтверждение возврата.

Поведение:

- endpoint идемпотентный;
- если возврат уже не `pending`, возвращается текущее состояние;
- при успехе:
  - товар возвращается на склад;
  - в передаче увеличивается `qty_returned`;
  - статус возврата меняется на `accepted`.

Важно: в текущей реализации view не содержит явной проверки owner/admin; доступ ограничен аутентификацией и company-scope.

### `POST /api/main/returns/<uuid:pk>/reject/`

Отклонение возврата.

Поведение:

- тоже идемпотентный;
- если возврат уже обработан, просто возвращается текущее состояние;
- при отклонении склад не меняется;
- статус становится `rejected`.

Важно: в текущей реализации view не содержит явной проверки owner/admin; доступ ограничен аутентификацией и company-scope.

---

## Поток 4. Остатки товаров у агента

### `GET/PATCH /api/main/agents/me/products/`

Показывает, что у текущего агента находится "на руках".

Ответ агрегируется по продукту, внутри каждого продукта есть список партий `subreals`.

Внутри расчёта по каждой партии считаются:

- `qty_transferred`
- `qty_accepted`
- `qty_returned`
- `qty_sold`
- `qty_on_hand`

Для продукта в целом:

- `qty_on_hand`
- `last_movement_at`
- список партий

Во внешнем API у вложенных `subreals` сериализуются только:

- `id`
- `created_at`
- `qty_transferred`
- `qty_accepted`
- `qty_returned`

Логика расчёта:

- передача
- все приёмы
- accepted-возвраты
- продажи через `AgentSaleAllocation`

Поиск:

- по названию;
- по `barcode`;
- по `article`;
- по `code`;
- по `plu`, если строка числовая.

`PATCH` - ручной корректор по партиям:

- принимает `subreals: [{id, qty_accepted?, qty_returned?}]`;
- разрешает менять только `qty_accepted` и `qty_returned`;
- нельзя:
  - `qty_accepted > qty_transferred`
  - `qty_returned > qty_accepted`

Код прямо рекомендует использовать события `Acceptance` и `ReturnFromAgent`, а `PATCH` оставляет как ручной override.

### `GET /api/main/owners/agents/products/`

Owner-view по всем агентам.

Возвращает:

- данные агента;
- список товаров у него на руках;
- ту же агрегированную структуру, что и `/agents/me/products/`.

Это owner dashboard-версия агентских остатков по всей компании/филиалу.

Важно: в текущем коде в этом endpoint нет явной проверки `_is_owner_like(...)`, хотя по смыслу он задуман как owner-view.

---

## Поток 5. POS-продажи агента

Этот блок живёт в `pos_views.py` и `services_agent_pos.py`.

### Главные особенности агентской продажи

1. Продажа идёт без смены.
2. Для checkout используется `checkout_agent_cart(...)`.
3. `Sale.shift` не должен использоваться.
4. Можно сохранить `cashbox`, но без `CashShift`.
5. Поштучная продажа из упаковки (`sale_package`) через агентскую корзину запрещена.
6. Для обычного агента остаток берётся с его партий.
7. Если продажу оформляет владелец "за агента", товар списывается с основного склада, а агент используется только как attribution.

### Выбор агента при продаже

Вспомогательная логика:

- `_resolve_acting_agent(...)`
- `_should_use_main_stock_in_agent_sale(...)`

Правила:

- если передан `agent` и текущий пользователь не владелец, будет ошибка: `Только владелец может продавать за агента.`
- выбранный агент кэшируется по ключу `cart_agent:{cart.id}` на 1 час;
- если оператор - владелец, выбранный агент влияет на атрибуцию продажи, но доступность товара проверяется по основному складу;
- это поведение отдельно покрыто тестом `PosOwnerAgentSaleTests`.

### Расчёт остатка для агентской продажи

Для обычного агента:

- используется `_agent_available_qty(...)`;
- формула: `accepted - returned - sold`.

Для owner override:

- используется основной склад `Product.quantity`.

В самом `checkout_agent_cart(...)` для агента остатки проверяются более строго:

- партии лочатся `select_for_update()`;
- продажи списываются FIFO по `ManufactureSubreal`;
- pending-возвраты уменьшают доступное количество;
- создаются записи `AgentSaleAllocation`.

### `POST /api/main/agents/me/cart/start/`

Создаёт или возвращает активную корзину агента.

Дополнительно:

- если у пользователя несколько активных корзин, лишние закрываются;
- можно передать скидки на заказ;
- owner может сразу передать `agent`.

### `GET /api/main/agents/me/carts/<uuid:pk>/`

Получение текущей агентской корзины.

Используется тот же `CartDetailAPIView`.

### `POST /api/main/agents/me/carts/<uuid:pk>/scan/`

Добавление товара по штрихкоду.

Что делает:

- ищет товар по `barcode`;
- проверяет доступный остаток;
- если остатка не хватает:
  - для owner override: ошибка по основному складу;
  - для агента: ошибка по его остаткам.

### `POST /api/main/agents/me/carts/<uuid:pk>/add-item/`

Добавление товара по `product_id`.

Особенности:

- можно передать `quantity`;
- можно передать `unit_price`;
- можно передать `discount_total`;
- нельзя использовать `sale_package_id`;
- цена не должна уходить ниже закупочной;
- количество проверяется против доступного остатка.

### `POST /api/main/agents/me/carts/<uuid:pk>/custom-item/`

Добавляет произвольную позицию без `Product`.

Используется для услуг или ручных строк чека.

### `POST /api/main/agents/me/carts/<uuid:pk>/checkout/`

Оформление продажи.

Входные поля через `AgentCheckoutSerializer`:

- `print_receipt`
- `client_id` или алиас `client`
- `payment_method`
- `cash_received`
- `cashbox_id`

Правила:

- для `cash` обязательно `cash_received`;
- `cash_received` не может быть отрицательной;
- если `payment_method = cash`, сумма наличных должна быть не меньше суммы продажи;
- checkout работает без смены;
- при необходимости в ответ добавляется `receipt_text`.

### `PATCH/DELETE /api/main/agents/me/carts/<uuid:cart_id>/items/<uuid:item_id>/`

Редактирование или удаление позиции в активной агентской корзине.

`PATCH` поддерживает:

- `quantity`
- `unit_price`
- `discount_total`

Если `quantity = 0`, строка удаляется.

### `GET /api/main/agents/me/sales/`

История продаж агента.

Показывает продажи, где:

- есть `AgentSaleAllocation` с `agent=request.user`, или
- `Sale.user = request.user`.

Поддерживаются фильтры:

- `start`
- `end`
- `paid`
- `status`
- `user`
- `search`
- `ordering`

### `GET /api/main/agents/me/sales/<uuid:pk>/`

Детали своей продажи.

Чужие продажи не видны.

### `POST /api/main/agents/me/sales/<uuid:pk>/return/`

Возврат агентской продажи.

Ключевая логика:

- если у продажи есть `agent_allocations`, удаляются `AgentSaleAllocation`, и товар снова считается "у агента";
- если это обычная продажа, где агент просто был кассиром, товар возвращается на основной склад;
- возвращать можно только продажи в статусах `paid` или `debt`;
- после возврата статус продажи становится `canceled`.

---

## Поток 6. Заявки агента на получение товара

Это отдельный workflow, который не равен POS-корзине.

### `GET/POST /api/main/agent-carts/`

Список и создание заявок агента.

Поведение:

- обычный агент видит только свои корзины;
- owner/admin видит все корзины компании;
- при создании `agent = request.user`;
- можно фильтровать по:
  - `status`
  - `client`

Сортировка:

- `created_at`
- `updated_at`
- `status`

### `GET/PATCH/DELETE /api/main/agent-carts/<uuid:pk>/`

Работа с одной заявкой.

Правила:

- agent может редактировать только `draft`;
- менять разрешено только `client` и `note`;
- удалять можно только `draft`;
- обычный агент не может удалять чужую корзину.

### `POST /api/main/agent-carts/<uuid:pk>/submit/`

Отправка заявки владельцу.

Что происходит:

- можно отправить только `draft`;
- пустую заявку отправить нельзя;
- пересчитываются подарки;
- фиксируются:
  - `gift_quantity`
  - `total_quantity`
  - `price_snapshot`
- статус становится `submitted`.

### `POST /api/main/agent-carts/<uuid:pk>/approve/`

Одобрение заявки владельцем/админом.

Что происходит:

- доступ только owner/admin;
- можно одобрить только `submitted`;
- товар проверяется и списывается со склада;
- по каждой строке создаётся `ManufactureSubreal`;
- передача создаётся с `is_sawmill=True`, поэтому сразу авто-принимается;
- ссылка на созданную передачу кладётся в `AgentRequestItem.subreal`;
- статус заявки меняется на `approved`.

Иными словами, `approve` конвертирует request-cart в реальные выдачи агенту.

### `POST /api/main/agent-carts/<uuid:pk>/reject/`

Отклонение заявки владельцем/админом.

Поведение:

- доступ только owner/admin;
- можно отклонить только `submitted`;
- склад не меняется;
- статус становится `rejected`.

### `GET/POST /api/main/agent-cart-items/`

Список и создание строк в заявке агента.

`POST`:

- принимает `cart`, `product`, `quantity_requested`;
- агент может добавлять строки только в свои корзины;
- товар должен принадлежать той же компании/филиалу;
- есть мягкая проверка по текущему складу `Product.quantity`.

По коду сейчас допускаются операции не только с `draft`, но и с `submitted` на уровне сериализатора, однако модель жёстко разрешает ручные изменения строк только пока корзина `draft`, а после `submitted` пропускает только служебные backend-апдейты через `update_fields`.

### `PATCH/DELETE /api/main/agent-cart-items/<uuid:pk>/`

Редактирование или удаление строки заявки.

Реальные ограничения:

- удалять можно только из `draft`;
- агент работает только со своими корзинами;
- после `submitted` ручное изменение пользовательских полей запрещено.

---

## Поток 7. Аналитика по агентам

Логика собирается в `analytics_agent.py`.

### Что считает аналитика агента

`build_agent_analytics_payload(...)` собирает:

- данные агента;
- период;
- summary:
  - `transfers_count`
  - `acceptances_count`
  - `items_transferred`
  - `defective_items` (брак товаров: принятые возвраты агента за период)
  - `sales_count`
  - `sales_amount`
  - `items_on_hand_qty`
  - `items_on_hand_amount`
- charts:
  - `sales_by_date`
  - `sales_by_product_amount`
  - `sales_distribution_by_product`
  - `on_hand_by_product_qty`
  - `on_hand_by_product_amount`
  - `transfers_by_date`
  - `top_products_by_transfers`
- `transfers_history`

Кэширование:

- остатки на руках - через `cached_result(..., key_prefix="agent_on_hand")`
- аналитика агента - через `cached_result(..., key_prefix="analytics_agent")`

### `GET /api/main/agents/me/analytics/`

Аналитика текущего агента.

Квери-параметры:

- `period=day|week|month|custom`
- `date`
- `date_from`
- `date_to`

Если профиль пользователя не привязан к компании, вернётся `400`.

### `GET /api/main/owners/agents/<uuid:agent_id>/analytics/`

Owner/admin аналитика по конкретному агенту.

Особенности:

- доступ только owner/admin;
- агент должен принадлежать компании пользователя;
- период задаётся теми же query-параметрами.

### `GET /api/main/owners/analytics/`

Общая owner analytics по компании.

Это уже не аналитика одного агента, но endpoint находится рядом с агентским блоком и используется как общий уровень над аналитикой агентов.

---

## Сводный список agent-related endpoints

### Выдача и движение товара

- `GET/POST /api/main/subreals/`
- `GET/PATCH/PUT/DELETE /api/main/subreals/<uuid:pk>/`
- `POST /api/main/subreals/bulk/`
- `GET/POST /api/main/acceptances/`
- `GET/DELETE /api/main/acceptances/<uuid:pk>/`
- `GET/POST /api/main/returns/`
- `GET/DELETE /api/main/returns/<uuid:pk>/`
- `POST /api/main/returns/<uuid:pk>/approve/`
- `POST /api/main/returns/<uuid:pk>/reject/`
- `GET/POST /api/main/agents/me/returns/`
- `GET/PATCH /api/main/agents/me/products/`
- `GET /api/main/owners/agents/products/`

### POS-продажи агента

- `POST /api/main/agents/me/cart/start/`
- `GET /api/main/agents/me/carts/<uuid:pk>/`
- `POST /api/main/agents/me/carts/<uuid:pk>/scan/`
- `POST /api/main/agents/me/carts/<uuid:pk>/add-item/`
- `POST /api/main/agents/me/carts/<uuid:pk>/custom-item/`
- `POST /api/main/agents/me/carts/<uuid:pk>/checkout/`
- `PATCH/DELETE /api/main/agents/me/carts/<uuid:cart_id>/items/<uuid:item_id>/`
- `GET /api/main/agents/me/sales/`
- `GET /api/main/agents/me/sales/<uuid:pk>/`
- `POST /api/main/agents/me/sales/<uuid:pk>/return/`

### Заявки агента на получение товара

- `GET/POST /api/main/agent-carts/`
- `GET/PATCH/DELETE /api/main/agent-carts/<uuid:pk>/`
- `POST /api/main/agent-carts/<uuid:pk>/submit/`
- `POST /api/main/agent-carts/<uuid:pk>/approve/`
- `POST /api/main/agent-carts/<uuid:pk>/reject/`
- `GET/POST /api/main/agent-cart-items/`
- `PATCH/DELETE /api/main/agent-cart-items/<uuid:pk>/`

### Аналитика

- `GET /api/main/agents/me/analytics/`
- `GET /api/main/owners/agents/<uuid:agent_id>/analytics/`
- `GET /api/main/owners/analytics/`

---

## Самые важные скрытые нюансы

1. Остатки у агента считаются не по одному полю, а через передачи, продажи и pending-возвраты.
2. Агентская продажа работает без смены.
3. Owner может продавать "за агента", но при этом остаток берётся с основного склада, а не с партий агента.
4. Возврат агентской продажи не возвращает товар на основной склад, если продажа была именно агентской: удаляются `AgentSaleAllocation`, и товар снова оказывается "у агента".
5. Approve agent request cart фактически превращает заявку в реальные передачи товара агенту.
6. `is_sawmill=True` означает авто-приём передачи.
7. Pending-возвраты резервируют остаток и уменьшают доступное количество для новых продаж и новых возвратов.

---

## Связанные файлы

- `apps/main/models.py`
- `apps/main/views.py`
- `apps/main/pos_views.py`
- `apps/main/services_agent_pos.py`
- `apps/main/serializers.py`
- `apps/main/pos_serializers.py`
- `apps/main/analytics_agent.py`
- `apps/main/urls.py`
- `apps/main/tests.py`
