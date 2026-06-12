# Модуль Building (Строительство / ЖК)

Модуль управления строительной компанией: жилые комплексы, продажи квартир и
договоры, закупки, склад, подрядчики и поставщики, процесс работ, собственная
касса, зарплата (payroll), долги и бартерные взаимозачёты.

Модуль самодостаточен и **не зависит** от модуля `construction` — у него своя
касса, свои заявки на кассу и свой воркфлоу.

- `models.py` — модели данных (≈40 моделей).
- `serializers.py` — сериализаторы DRF.
- `views.py` — представления (generic API views).
- `services.py` — бизнес-логика воркфлоу (закупка→касса→передача→склад, ERP, премии с продаж).
- `urls.py` — маршруты, подключаются под общим префиксом `/api/building/`.

---

## 1. Базовые принципы

### Мультитенантность (компания)
Все данные изолированы по компании пользователя. За это отвечает
`CompanyQuerysetMixin`: если у модели есть поле `company`, queryset фильтруется
по `request.user.company_id`. `is_staff` / `is_superuser` видят всё.

Для моделей, привязанных к ЖК косвенно (через `residential_complex`), фильтрация
идёт по `residential_complex__company_id`.

### Доступ по ЖК (назначения сотрудников)
Сотрудник может быть назначен на конкретные ЖК через `ResidentialComplexMember`.
Логика `_allowed_residential_complex_ids(user)`:

- `owner` / `admin` / `superuser` → `None` (доступ ко всем ЖК компании);
- сотрудник с активными назначениями → список ID назначенных ЖК;
- сотрудник без назначений → `[]` (не видит ничего по ЖК).

«Owner-like» (`_is_owner_like`) — это `superuser`, либо `role in (owner, admin)`,
либо пользователь, владеющий компанией (`owned_company_id`).

### Права (feature-флаги на пользователе)
Помимо owner-like, доступ к разделам даёт набор булевых флагов на модели User:

| Флаг | Раздел |
|------|--------|
| `can_view_building_procurement` | Закупки / склад-приёмка |
| `can_view_building_stock`       | Склад (приёмка, списания, передачи) |
| `can_view_building_cash_register` | Касса, заявки на кассу |
| `can_view_building_clients`     | Клиенты |
| `can_view_building_treaty`      | Договоры, квартиры, продажи, группы договоров |
| `can_view_building_work_process`| Процесс работ (work entries, AVR) |
| `can_view_building_salary`      | Зарплата / payroll |
| `can_view_building_employess` / `can_view_employees` | Сотрудники, назначения на ЖК |
| `can_view_building_procurement` **или** `can_view_building_work_process` | Долги (ledger) |

Owner-like обходит проверку любого флага.

### Общие соглашения
- Все ID — `UUID`.
- Все списки фильтруются `DjangoFilterBackend` + `SearchFilter` (см. поля у каждого view).
- Файлы загружаются как `multipart/form-data` (поля `file`, опционально `title`).
- Базовый префикс всех путей ниже: `/api/building/`.

---

## 2. Бизнес-процессы (воркфлоу)

### 2.1. Закупка → касса → передача → склад
Реализован в `services.py`. Статусы `BuildingProcurementRequest.Status`:

```
draft → submitted_to_cash → cash_approved → transfer_created → transferred
                          ↘ cash_rejected            ↘ (reject) partially_transferred
```

1. **Создание закупки** (`draft`) и добавление позиций (`BuildingProcurementItem`).
   Позиции можно менять только в статусе `draft`.
2. **Отправка в кассу** — `submit_procurement_to_cash`. Требует право закупок,
   непустой список позиций. Пересчитывает `total_amount`, ставит `submitted_to_cash`.
3. **Решение кассы** — `approve_procurement_cash` / `reject_procurement_cash`
   (требуют право кассы). Пишут `BuildingProcurementCashDecision` и историю.
4. **Создание передачи на склад** — `create_transfer_from_procurement`
   (после `cash_approved`). Создаёт/находит склад ЖК, копирует позиции в
   `BuildingTransferRequest` (`pending_receipt`), статус закупки → `transfer_created`.
5. **Приёмка складом** — `accept_transfer` (право склада). Приходует позиции в
   `BuildingWarehouseStockItem` (через `BuildingWarehouseStockMove`, тип `incoming`),
   передача → `accepted`, закупка → `transferred`.
   Если закупка в режиме `debt`/`mixed`/`barter` и указан поставщик — создаётся
   запись долга `BuildingDebtLedgerEntry` (PAYABLE, «мы должны поставщику»).
6. **Отказ склада** — `reject_transfer` (с причиной): передача → `rejected`,
   закупка → `partially_transferred`.

Каждый шаг логируется в `BuildingWorkflowEvent` (`services.log_event`).

### 2.2. Движение по складу
`BuildingWarehouseMovement` (тип `MovementType`):
- `write_off` — списание;
- `transfer_to_contractor` — передача подрядчику;
- `transfer_to_work_entry` — передача в процесс работ.

Каждое движение порождает `BuildingWarehouseStockMove` и корректирует остатки
`BuildingWarehouseStockItem`.

### 2.3. Продажи и договоры
`BuildingTreaty` — договор (продажа/бронь/закупка/строит. отдел/прочее).
- `operation_type`: sale / booking / other.
- `payment_type`: full / installment; `payment_mode`: cash / installment / barter / mixed.
- `status`: draft / active / signed / cancelled.
- Рассрочка: `BuildingTreatyInstallment` (planned/paid) + платежи
  `BuildingTreatyInstallmentPayment`.
- При продаже (`sale`, статус active/signed) автоматически создаётся премия
  ответственному менеджеру — `services.create_sale_commission_adjustment`
  (по настройке `BuildingEmployeeCompensation.sale_commission_*`, идемпотентно).
- Интеграция с ERP — `request_treaty_create_in_erp` (через env
  `BUILDING_ERP_TREATY_ENDPOINT`, `BUILDING_ERP_TOKEN`; статусы `ErpSyncStatus`).
- Договоры можно раскладывать по папкам — `BuildingTreatyGroup` (дерево, MPTT).

### 2.4. Касса Building и заявки на кассу
Своя касса (`BuildingCashbox`) и движения (`BuildingCashFlow`: income/expense,
статусы pending/approved/rejected).

`BuildingCashRegisterRequest` — заявка на кассу от бизнес-модуля (продажа,
рассрочка, оплата подрядчику/закупки, аванс). Жизненный цикл:
`pending → approved` (создаётся `BuildingCashFlow`) `/ rejected / cancelled`.

### 2.5. Зарплата (payroll)
- `BuildingEmployeeCompensation` — настройка оплаты сотрудника (оклад/ставка,
  комиссия с продаж).
- `BuildingPayrollPeriod` (draft → approved → paid) и строки `BuildingPayrollLine`.
- `BuildingPayrollAdjustment` — бонусы/удержания/авансы.
- `BuildingPayrollPayment` — выплаты (pending → posted/void), проводятся через кассу.
- Авансы (`AdvanceRequest...`) — отдельный поток одобрения/отказа через кассу.

### 2.6. Долги и бартер
- `BuildingDebtLedgerEntry` — реестр долгов: направление payable/receivable,
  типы charge/payment/barter/adjustment/writeoff, контрагент client/supplier/contractor.
- Сводки: общая (`/debts/summary/`) и по контрагенту.
- Бартер: `BuildingBarterItem` (привязка к источнику) и
  `BuildingSupplierBarterSettlement` (зачёт с поставщиком/подрядчиком:
  draft → confirmed / cancelled).

---

## 3. Справочник API

Ниже — все маршруты модуля (префикс `/api/building/`). Для всех требуется
аутентификация (`IsAuthenticated`).

### Касса Building
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `cashboxes/` | Список/создание касс |
| GET/PATCH/PUT/DELETE | `cashboxes/<uuid>/` | Касса |
| GET/POST | `cash/flows/` | Движения по кассе |
| GET/PATCH/PUT/DELETE | `cash/flows/<uuid>/` | Движение |
| POST | `cash/flows/bulk/status/` | Массовая смена статуса движений |

### Заявки на кассу
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `cash-register/requests/` | Список/создание заявок |
| GET | `cash-register/requests/<uuid>/` | Заявка |
| POST | `cash-register/requests/<uuid>/approve/` | Одобрить (создаёт CashFlow) |
| POST | `cash-register/requests/<uuid>/reject/` | Отклонить (с причиной) |
| POST | `cash-register/requests/<uuid>/files/` | Прикрепить файл к заявке |
| POST | `cash-register/cashflows/<uuid>/files/` | Прикрепить файл к движению |

### Жилые комплексы (ЖК) и связанное
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `objects/` | Список/создание ЖК |
| GET/PATCH/PUT/DELETE | `objects/<uuid>/` | ЖК |
| GET | `objects/<uuid>/floors/` | Статистика по этажам (всего/свободно/бронь/продано) |
| GET | `objects/<uuid>/blocks/stats/` | Статистика по блокам |
| GET/POST | `objects/<uuid>/members/` | Назначения сотрудников на ЖК |
| DELETE | `objects/<uuid>/members/<uuid:user_id>/` | Снять назначение |
| GET/POST | `drawings/` | Чертежи ЖК |
| GET/PATCH/PUT/DELETE | `drawings/<uuid>/` | Чертёж |
| GET/POST | `warehouses/` | Склады ЖК |
| GET/PATCH/PUT/DELETE | `warehouses/<uuid>/` | Склад |
| GET/POST | `apartments/` | Квартиры (требует право `treaty`) |
| GET/PATCH/PUT/DELETE | `apartments/<uuid>/` | Квартира |

### Товары
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `products/` | Справочник товаров |
| GET/PATCH/PUT/DELETE | `products/<uuid>/` | Товар |

### Закупки
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `procurements/` | Список/создание закупок |
| GET/PATCH/PUT/DELETE | `procurements/<uuid>/` | Закупка |
| POST | `procurements/<uuid>/files/` | Файл к закупке (может автосоздать договор) |
| GET/POST | `procurement-items/` | Позиции закупки (только в `draft`) |
| GET/PATCH/PUT/DELETE | `procurement-items/<uuid>/` | Позиция |
| POST | `procurements/<uuid>/submit-to-cash/` | Отправить закупку в кассу |
| GET | `cash/procurements/pending/` | Закупки, ожидающие решения кассы |
| POST | `cash/procurements/<uuid>/approve/` | Одобрить кассой |
| POST | `cash/procurements/<uuid>/reject/` | Отклонить кассой (с причиной) |
| POST | `procurements/<uuid>/transfers/create/` | Создать передачу на склад |

### Передачи на склад
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `warehouse-transfers/` | Список передач (`?incoming=1` — только ожидающие) |
| GET | `warehouse-transfers/<uuid>/` | Передача |
| POST | `warehouse-transfers/<uuid>/accept/` | Принять (приход на склад) |
| POST | `warehouse-transfers/<uuid>/reject/` | Отклонить (с причиной) |
| POST | `warehouse-transfers/<uuid>/files/` | Прикрепить файл |

### Подрядчики
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `contractors/` | Список/создание |
| GET/PATCH/PUT/DELETE | `contractors/<uuid>/` | Подрядчик |
| POST | `contractors/<uuid>/files/` | Файл |
| GET | `contractors/<uuid>/work-history/` | История работ подрядчика |

### Поставщики
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `suppliers/` | Список/создание |
| GET/PATCH/PUT/DELETE | `suppliers/<uuid>/` | Поставщик |
| POST | `suppliers/<uuid>/files/` | Файл |
| GET | `suppliers/<uuid>/purchase-history/` | История закупок у поставщика |

### Бартерные зачёты
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `barter-settlements/` | Список/создание зачётов |
| GET/PATCH/PUT/DELETE | `barter-settlements/<uuid>/` | Зачёт (удаление только в `draft`) |
| POST | `barter-settlements/<uuid>/confirm/` | Подтвердить |
| POST | `barter-settlements/<uuid>/cancel/` | Отменить |

### Склад: заявки, акты, движения, остатки
| Метод | Путь | Описание |
|-------|------|----------|
| POST | `work-entries/<uuid>/warehouse-requests/` | Заявка на материалы из процесса работ |
| GET | `work-entries/warehouse-requests/` | Список заявок на склад |
| GET | `work-entries/warehouse-requests/<uuid>/` | Заявка |
| POST | `work-entries/<uuid>/reconciliation-act/` | Акт сверки по процессу работ |
| POST | `warehouse-movements/write-off/` | Списание со склада |
| POST | `warehouse-movements/transfer-to-contractor/` | Передача подрядчику |
| POST | `warehouse-movements/transfer-to-work-entry/` | Передача в процесс работ |
| POST | `warehouse-movements/<uuid>/files/` | Файл к движению |
| GET | `warehouse-stock/items/` | Остатки на складах |
| GET | `warehouse-stock/moves/` | История движений склада |
| GET | `workflow-events/` | Журнал событий воркфлоу |

### Документы закупки (purchase documents)
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `documents/purchase/` | Список/создание |
| GET/PATCH/PUT/DELETE | `documents/purchase/<uuid>/` | Документ |
| POST | `documents/purchase/<uuid>/cash/approve/` | Одобрить кассой |
| POST | `documents/purchase/<uuid>/cash/reject/` | Отклонить кассой |

### Процесс работ (work entries)
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `work-entries/` | Список/создание записей |
| GET/PATCH/PUT/DELETE | `work-entries/<uuid>/` | Запись (правка — автор/owner) |
| POST | `work-entries/<uuid>/photos/` | Добавить фото |
| POST | `work-entries/<uuid>/files/` | Добавить файл |
| GET | `work-entries/<uuid>/warehouse-receipts/` | Приходы материалов по записи |
| GET/POST | `work-entries/<uuid>/acceptance/` | Акт приёмки работ (AVR) |
| POST | `work-entry-acceptance/<uuid>/files/` | Файл к акту приёмки |

### Клиенты
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `clients/` | Список/создание |
| GET/PATCH/PUT/DELETE | `clients/<uuid>/` | Клиент |
| POST | `clients/<uuid>/files/` | Файл |

### Договоры (treaties)
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `treaties/` | Список/создание |
| GET/PATCH/PUT/DELETE | `treaties/<uuid>/` | Договор (правка — автор/owner) |
| POST | `treaties/<uuid>/files/` | Файл |
| POST | `treaties/<uuid>/erp/create/` | Отправить договор в ERP |
| POST | `treaties/move/` | Переместить договор(ы) в группу |
| POST | `treaty-installments/<uuid>/payments/` | Платёж по рассрочке |
| GET/POST | `treaty-groups/` | Группы (папки) договоров |
| GET/PATCH/PUT/DELETE | `treaty-groups/<uuid>/` | Группа |

### Долги (debts ledger)
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `debts/ledger/` | Реестр долгов |
| POST | `debts/ledger/<uuid>/files/` | Файл к записи долга |
| GET | `debts/summary/` | Общая сводка по долгам |
| GET | `debts/summary/<str:counterparty_type>/<uuid:counterparty_id>/` | Сводка по контрагенту |

### Бартер (универсальный)
| Метод | Путь | Описание |
|-------|------|----------|
| POST | `barter/<str:source_type>/<uuid:source_id>/items/` | Upsert позиций бартера для источника |
| GET/PATCH/PUT/DELETE | `barter/items/<uuid>/` | Позиция бартера |
| POST | `barter/<str:source_type>/<uuid:source_id>/files/` | Файл бартера |

### Задачи / напоминания
| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `tasks/` | Список/создание задач |
| GET/PATCH/PUT/DELETE | `tasks/<uuid>/` | Задача (доступ — автор/исполнитель/owner) |
| POST | `tasks/<uuid>/files/` | Файл |
| POST | `tasks/<uuid>/checklist-items/` | Пункт чек-листа |
| GET/PATCH/PUT/DELETE | `task-checklist-items/<uuid>/` | Пункт чек-листа |

### Зарплата / payroll
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `salary/employees/` | Сотрудники (для ЗП) |
| GET/PUT/PATCH | `salary/employees/<uuid:user_id>/compensation/` | Upsert настройки оплаты |
| GET/POST | `salary/payrolls/` | Периоды начислений |
| GET/PATCH/PUT/DELETE | `salary/payrolls/<uuid>/` | Период (правка только в `draft`) |
| POST | `salary/payrolls/<uuid>/approve/` | Утвердить период (→ approved) |
| GET/POST | `salary/payrolls/<uuid:payroll_id>/lines/` | Строки начислений периода |
| GET/PATCH/PUT/DELETE | `salary/payroll-lines/<uuid>/` | Строка |
| POST | `salary/payroll-lines/<uuid>/adjustments/` | Бонус/удержание/аванс |
| GET/DELETE | `salary/payroll-adjustments/<uuid>/` | Начисление |
| GET/POST | `salary/payroll-lines/<uuid>/payments/` | Выплаты по строке |
| POST | `salary/payments/<uuid>/approve/` | Провести выплату через кассу |
| GET | `salary/my/lines/` | Свои строки начислений (для сотрудника) |
| GET | `salary/advance-requests/` | Заявки на аванс |
| POST | `salary/advance-requests/<uuid>/approve/` | Одобрить аванс |
| POST | `salary/advance-requests/<uuid>/reject/` | Отклонить аванс |

---

## 4. Справочник статусов (enum)

| Модель | Поле | Значения |
|--------|------|----------|
| `BuildingCashFlow` | type | income, expense |
| `BuildingCashFlow` | status | pending, approved, rejected |
| `BuildingCashRegisterRequest` | request_type | apartment_sale, installment_initial_payment, installment_payment, contractor_payment, procurement_payment, advance, other |
| `BuildingCashRegisterRequest` | status | pending, approved, rejected, cancelled |
| `ResidentialComplexApartment` | status | available, reserved, sold |
| `BuildingContractor` | contractor_type | subcontractor, general_contractor, other |
| `BuildingSupplier` | supplier_type | materials_supplier, equipment_supplier, other |
| `BuildingSupplierBarterSettlement` | status | draft, confirmed, cancelled |
| `BuildingProcurementRequest` | payment_mode | cash, debt, barter, mixed |
| `BuildingProcurementRequest` | status | draft, submitted_to_cash, cash_approved, cash_rejected, transfer_created, transferred, partially_transferred |
| `BuildingTransferRequest` | status | pending_receipt, accepted, rejected |
| `BuildingWarehouseRequest` | status | pending, approved, rejected, partially_approved, completed |
| `BuildingReconciliationAct` | status | draft, approved, rejected |
| `BuildingWarehouseMovement` | movement_type | write_off, transfer_to_contractor, transfer_to_work_entry |
| `BuildingWarehouseStockMove` | move_type | incoming, write_off, transfer_to_contractor, transfer_to_work_entry |
| `BuildingTreaty` | operation_type | sale, booking, other |
| `BuildingTreaty` | treaty_type | construction_department, sale, booking, procurement, other |
| `BuildingTreaty` | payment_type | full, installment |
| `BuildingTreaty` | payment_mode | cash, installment, barter, mixed |
| `BuildingTreaty` | status | draft, active, signed, cancelled |
| `BuildingTreaty` | erp_sync_status | not_requested, requested, synced, failed, not_configured |
| `BuildingTreatyInstallment` | status | planned, paid |
| `BuildingWorkEntry` | category | note, treaty, defect, report, other |
| `BuildingWorkEntry` | work_status | planned, in_progress, paused, completed, cancelled |
| `BuildingWorkEntry` | payment_mode | cash, debt, barter, mixed |
| `BuildingTask` | status | open, done, cancelled |
| `BuildingEmployeeCompensation` | salary_type | monthly, monthly_pct, daily, hourly |
| `BuildingEmployeeCompensation` | sale_commission_type | none, fixed, percent |
| `BuildingPayrollPeriod` | status | draft, approved, paid |
| `BuildingPayrollAdjustment` | type | bonus, deduction, advance |
| `BuildingPayrollAdjustment` | status | pending, completed, rejected |
| `BuildingPayrollPayment` | status | pending, posted, void |
| `BuildingDebtLedgerEntry` | direction | payable, receivable |
| `BuildingDebtLedgerEntry` | entry_type | charge, payment, barter, adjustment, writeoff |
| `BuildingDebtLedgerEntry` | status | draft, approved, cancelled |
| `BuildingDebtLedgerEntry` | counterparty_type | client, supplier, contractor |
| `BuildingWorkEntryAcceptance` | status | draft, signed |

---

## 5. Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `BUILDING_ERP_TREATY_ENDPOINT` | URL ERP для создания договора. Если не задан — синк помечается `not_configured`. |
| `BUILDING_ERP_TOKEN` | Bearer-токен для запроса в ERP (опционально). |
