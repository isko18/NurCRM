# Воронка продаж 2.0 — архитектура production-grade pipeline

Документ описывает **апгрейд существующей воронки** консалтинга
(`FunnelConsalting` / `FunnelStageConsalting` / `LeadConsalting`) до надёжного,
автоматизированного, высоко-конверсионного CRM-pipeline.

> Принцип: **расширяем, не ломаем.** Все существующие модели/эндпоинты остаются.
> Новое добавляется аддитивно, со scoping по `company` + `branch` (как везде в `consalting`).
> Инфраструктура уже есть: **Celery** (`core/celery.py`), **Channels/ASGI** (websockets),
> Django-signals. Используем их, ничего нового в стек не тащим.

---

## 0. Анализ слабых мест текущей воронки

| # | Слабое место | Последствие | Решение |
|---|---|---|---|
| 1 | Стадии = свободный текст (`name`+`order`) | Невозможно строить логику переходов, аналитику между компаниями | **`stage_type`** — семантический тип стадии (enum), независимый от названия |
| 2 | Переход в любую стадию (`move-stage` ничего не проверяет, кроме воронки) | Менеджер «перетаскивает» сделку как угодно, статусы рассинхронизированы | **State machine** с матрицей разрешённых переходов |
| 3 | Нет приоритизации лидов | Менеджер работает хаотично, горячие лиды стынут | **Lead scoring A/B/C** |
| 4 | Нет истории действий | Невозможно понять, что происходило со сделкой; нет аудита | **Immutable timeline (`LeadActivity`)** + лог переходов |
| 5 | Нет автоматизации | Сделки «зависают» и теряются молча | **Automation engine** (события + Celery-сканы) |
| 6 | Сделка может «висеть» без следующего шага | Главная причина потери сделок | **Обязательный `next_action`** в активных стадиях |
| 7 | Причина проигрыша — свободный текст или отсутствует | Нет аналитики оттока | **`LossReason`** (справочник, структурированно) |
| 8 | Нет метрик воронки | Нельзя измерить конверсию/узкие места | **AnalyticsService** (конверсия, время в стадии, drop-off) |
| 9 | Нет защиты целостности | WON → назад, пропуск стадий, гонки | **State-machine guard + транзакции + лог** |

---

## 1. Database schema (обновлённая)

Соглашение об именовании: новые модели — суффикс `Consalting`, scoping `company`+`branch`,
наследуют `TimeStampedModel`.

### 1.1. `FunnelStageConsalting` — расширение (аддитивно)

```python
class FunnelStageConsalting(TimeStampedModel):
    class StageType(models.TextChoices):
        NEW_LEAD         = 'new_lead',         'Новый лид'
        FIRST_CONTACT    = 'first_contact',    'Первый контакт'
        QUALIFICATION    = 'qualification',    'Квалификация'
        NURTURE          = 'nurture',          'Прогрев / в работе'
        PROPOSAL_SENT    = 'proposal_sent',    'КП отправлено'
        NEGOTIATION      = 'negotiation',      'Переговоры'
        DECISION_PENDING = 'decision_pending', 'Ожидание решения'
        WON              = 'won',              'Оплачено / выиграно'
        ONBOARDING       = 'onboarding',       'Онбординг'
        COMPLETED        = 'completed',        'Завершено'
        LOST             = 'lost',             'Потеряно'

    # ... company, branch, funnel, name, order, color (как есть) ...

    stage_type   = models.CharField(max_length=20, choices=StageType.choices,
                                    default=StageType.NEW_LEAD, db_index=True)
    # переопределение матрицы переходов на уровне воронки (опционально).
    # пусто -> берётся канонная матрица из state_machine.py
    allowed_next = models.JSONField(default=list, blank=True)      # ["negotiation", "lost"]
    required_fields = models.JSONField(default=list, blank=True)   # ["estimated_value","next_action_date"]
    sla_hours    = models.PositiveIntegerField(null=True, blank=True)  # порог "at risk"
    allow_skip   = models.BooleanField(default=False)             # разрешить пропуск вперёд

    # is_final / is_success — оставляем для совместимости, но они ВЫВОДЯТСЯ из stage_type
    # (won/completed -> final+success, lost -> final). Помечаем deprecated.
```

> **Ключевая идея:** логика переходов и аналитика строятся на `stage_type`, а не на
> `name`/`order`. Компания может переименовать и переставить колонки — поведение не сломается.

### 1.2. `LeadConsalting` — расширение (это «сделка»/карточка)

```python
class LeadConsalting(TimeStampedModel):
    # --- существующее: funnel, stage, client, owner, title, description,
    #     full_name, phone, email, source, estimated_value, probability, status, closed_at ---

    # ----- Scoring -----
    class Grade(models.TextChoices):
        A = 'A', 'Горячий';  B = 'B', 'Тёплый';  C = 'C', 'Холодный'
    class Urgency(models.TextChoices):
        LOW = 'low','Низкая'; MEDIUM='medium','Средняя'; HIGH='high','Высокая'

    score_grade   = models.CharField(max_length=1, choices=Grade.choices, default=Grade.C, db_index=True)
    score_value   = models.PositiveIntegerField(default=0)          # 0..100
    score_updated_at = models.DateTimeField(null=True, blank=True)
    # факторы скоринга:
    budget_confirmed       = models.BooleanField(default=False)
    urgency                = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.LOW)
    decision_maker_engaged = models.BooleanField(default=False)
    avg_response_minutes   = models.PositiveIntegerField(null=True, blank=True)
    # deal_size = estimated_value (уже есть)

    # ----- Next action (обязателен в активных стадиях) -----
    class NextAction(models.TextChoices):
        CALL='call','Звонок'; MESSAGE='message','Сообщение'
        MEETING='meeting','Встреча'; FOLLOW_UP='follow_up','Follow-up'
    next_action_type = models.CharField(max_length=12, choices=NextAction.choices, null=True, blank=True)
    next_action_date = models.DateTimeField(null=True, blank=True, db_index=True)
    next_action_note = models.CharField(max_length=500, blank=True)

    # ----- Риск / тайминги -----
    is_at_risk        = models.BooleanField(default=False, db_index=True)
    risk_reason       = models.CharField(max_length=255, blank=True)
    last_activity_at  = models.DateTimeField(null=True, blank=True, db_index=True)
    stage_entered_at  = models.DateTimeField(null=True, blank=True)   # для "время в стадии"

    # ----- Проигрыш -----
    loss_reason  = models.ForeignKey('LossReasonConsalting', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='leads')
    loss_comment = models.TextField(blank=True)

    # ----- Lifecycle -----
    first_contact_at = models.DateTimeField(null=True, blank=True)
    won_at           = models.DateTimeField(null=True, blank=True)
    lost_at          = models.DateTimeField(null=True, blank=True)
    completed_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['company','funnel','stage']),
            models.Index(fields=['company','score_grade','next_action_date']),  # сортировка workload
            models.Index(fields=['company','is_at_risk']),
            models.Index(fields=['company','owner','next_action_date']),
        ]
```

### 1.3. Новые модели

```python
class LeadActivityConsalting(TimeStampedModel):
    """Неизменяемая лента событий (audit trail). Append-only: без update/delete через API."""
    class Type(models.TextChoices):
        NOTE='note'; CALL='call'; MESSAGE='message'; EMAIL='email'; MEETING='meeting'
        FILE='file'; STAGE_CHANGE='stage_change'; SCORE_CHANGE='score_change'
        TASK='task'; AUTOMATION='automation'; SYSTEM='system'
    id       = uuid; company = FK; branch = FK(null)
    lead     = FK(LeadConsalting, related_name='activities')
    actor    = FK(User, null=True)                 # null = система/автоматизация
    type     = CharField(choices=Type)
    title    = CharField(255)
    body     = TextField(blank)
    payload  = JSONField(default=dict)             # структурные данные (from_stage, to_stage, ...)
    file     = FileField(null, blank)
    # created_at из TimeStampedModel. updated_at НЕ используется (запись неизменяема).

class StageTransitionConsalting(TimeStampedModel):
    """Лог переходов по стадиям — основа аналитики (время в стадии, drop-off)."""
    lead        = FK(LeadConsalting, related_name='transitions')
    company/branch = FK
    from_stage  = FK(FunnelStageConsalting, null=True, related_name='+')
    to_stage    = FK(FunnelStageConsalting, related_name='+')
    from_type   = CharField(20, blank)             # snapshot stage_type (стадию могут удалить)
    to_type     = CharField(20)
    actor       = FK(User, null=True)
    automated   = BooleanField(default=False)
    seconds_in_prev = PositiveBigIntegerField(null=True)  # сколько провёл в прошлой стадии

class LossReasonConsalting(TimeStampedModel):
    """Справочник причин проигрыша (per company)."""
    company = FK; code = SlugField; label = CharField(255); is_active = Bool(default=True)
    # сиды: price_high, no_response, competitor, not_relevant, timing
    class Meta: unique_together = [('company','code')]

class LeadTaskConsalting(TimeStampedModel):
    """Follow-up задачи (ручные и созданные автоматизацией). Питают next_action."""
    class Status(TextChoices): OPEN='open'; DONE='done'; OVERDUE='overdue'; CANCELED='canceled'
    lead       = FK(LeadConsalting, related_name='tasks')
    company/branch = FK
    assignee   = FK(User, null=True)
    type       = CharField(choices=LeadConsalting.NextAction.choices)
    title      = CharField(255); due_date = DateTimeField(db_index=True)
    status     = CharField(choices=Status, default=OPEN, db_index=True)
    created_by = FK(User, null=True); created_by_automation = Bool(default=False)
    completed_at = DateTimeField(null)

class AutomationRuleConsalting(TimeStampedModel):
    """Декларативное правило автоматизации (per company, опц. per funnel)."""
    class Trigger(TextChoices):
        STAGE_CHANGED='stage_changed'; ACTIVITY_ADDED='activity_added'
        NO_ACTIVITY='no_activity'; PROPOSAL_OPENED='proposal_opened'
        TASK_OVERDUE='task_overdue'; LEAD_WON='lead_won'; LEAD_LOST='lead_lost'
        SLA_BREACH='sla_breach'
    company = FK; funnel = FK(null)
    name    = CharField(255); trigger = CharField(choices=Trigger)
    conditions = JSONField(default=dict)   # {"stage_type":"proposal_sent","score_grade":["A","B"]}
    actions    = JSONField(default=list)   # [{"type":"create_task",...},{"type":"notify_manager"}]
    is_active  = Bool(default=True); priority = IntegerField(default=100)

class AutomationLogConsalting(TimeStampedModel):
    """Аудит срабатываний автоматизации (идемпотентность + отладка)."""
    rule = FK(AutomationRuleConsalting, null); lead = FK(LeadConsalting)
    trigger = CharField; matched = Bool; actions_result = JSONField(default=list)
    dedup_key = CharField(255, db_index=True)   # защита от повторного срабатывания
```

---

## 2. State machine — логика переходов

### 2.1. Каноничная матрица (в коде, `funnel/state_machine.py`)

```python
T = FunnelStageConsalting.StageType
TRANSITIONS = {
    T.NEW_LEAD:         {T.FIRST_CONTACT, T.QUALIFICATION, T.LOST},
    T.FIRST_CONTACT:    {T.QUALIFICATION, T.NURTURE, T.LOST},
    T.QUALIFICATION:    {T.NURTURE, T.PROPOSAL_SENT, T.LOST},
    T.NURTURE:          {T.PROPOSAL_SENT, T.QUALIFICATION, T.LOST},   # можно вернуть на квалификацию
    T.PROPOSAL_SENT:    {T.NEGOTIATION, T.DECISION_PENDING, T.LOST},
    T.NEGOTIATION:      {T.DECISION_PENDING, T.PROPOSAL_SENT, T.WON, T.LOST},
    T.DECISION_PENDING: {T.WON, T.NEGOTIATION, T.LOST},
    T.WON:              {T.ONBOARDING},                 # вперёд только в онбординг
    T.ONBOARDING:       {T.COMPLETED},
    T.COMPLETED:        set(),                          # терминальная
    T.LOST:             set(),                          # терминальная (reopen — отдельным правом)
}
```

### 2.2. Правила перехода (guards)

Сервис `FunnelStateMachine.can_transition(lead, target_stage, actor) -> (ok, errors)`:

1. **Разрешённость**: `target.stage_type` ∈ `allowed_next(source)` 
   (берём `source.allowed_next` если задано, иначе каноничную матрицу).
2. **Запрет назад из закрытых**: из `WON/COMPLETED` назад — запрещено (кроме спец-права `can_reopen`).
3. **Запрет пропуска**: если переход не в матрице и `source.allow_skip == False` → ошибка.
4. **Required fields**: все поля из `target.required_fields` (и встроенные правила ниже) заполнены.
5. **Встроенные обязательные правила по типу**:
   - `→ PROPOSAL_SENT`: `estimated_value > 0` + хотя бы одна активность типа `proposal`/файл КП.
   - `→ WON`: подтверждение оплаты (`budget_confirmed=True`) ИЛИ явный флаг в payload.
   - `→ LOST`: `loss_reason` обязателен.
   - активные стадии (`NEW_LEAD..DECISION_PENDING`): `next_action_type` + `next_action_date` заданы.
6. **Целостность воронки**: `target.funnel_id == lead.funnel_id`.

### 2.3. `transition(lead, target, actor, automated=False)` (атомарно)

```
with transaction.atomic():
    ok, errors = can_transition(...)
    if not ok: raise StateTransitionError(errors)
    seconds_in_prev = now - (lead.stage_entered_at or lead.created_at)
    StageTransitionConsalting.objects.create(... seconds_in_prev ...)
    lead.stage = target
    lead.stage_entered_at = now
    _sync_lifecycle(lead, target.stage_type)   # won_at/lost_at/closed_at/status
    lead.save()
    ActivityLogger.log(lead, STAGE_CHANGE, actor, payload={from,to})
    events.emit('stage_changed', lead, actor, automated)   # -> automation engine
```

`_sync_lifecycle` маппит `stage_type` → `status`/таймстемпы:
`won → status=WON, won_at=now`; `lost → status=LOST, lost_at=now`;
`completed → completed_at=now`; активные → `status=IN_WORK`, сброс closed-полей.

---

## 3. Backend service structure

```
apps/consalting/
├── models.py                 # + расширения и новые модели (раздел 1)
├── funnel/
│   ├── __init__.py
│   ├── state_machine.py       # TRANSITIONS, FunnelStateMachine, guards
│   ├── scoring.py             # ScoringService.recalculate(lead)
│   ├── activity.py            # ActivityLogger.log(...)  (единая точка записи в timeline)
│   ├── tasks_service.py       # LeadTaskService: create/complete/overdue-scan
│   ├── analytics.py           # PipelineAnalytics: метрики по воронке/стадиям
│   ├── events.py              # определения событий + dispatcher (signals-обёртка)
│   └── automation/
│       ├── engine.py          # AutomationEngine.run(event)
│       ├── conditions.py      # match(conditions, lead, ctx) -> bool
│       └── actions.py         # ACTION_HANDLERS: create_task, notify_manager, ...
├── tasks.py                   # Celery: scan_no_activity, scan_overdue_tasks, scan_sla
├── signals.py                 # post_save/transition -> events.emit -> AutomationEngine
├── serializers.py / views.py / urls.py / admin.py   # эндпоинты (раздел 5)
```

**Принципы:**
- Вся запись в timeline — только через `ActivityLogger` (гарантия аудита).
- Все переходы — только через `FunnelStateMachine` (никаких прямых `lead.stage = x` в вьюхах).
- Скоринг/риск/lifecycle — в сервисах, не в сериализаторах.

### ScoringService (раздел про lead scoring)

```python
WEIGHTS = {                       # сумма = 100
    'budget_confirmed':        25,
    'urgency_high':            20,   # medium=10
    'decision_maker_engaged':  20,
    'fast_response':           15,   # avg_response_minutes <= 30
    'deal_size':               20,   # нормируется к порогу компании
}
def recalculate(lead):
    v = 0
    if lead.budget_confirmed: v += 25
    v += {LOW:0, MEDIUM:10, HIGH:20}[lead.urgency]
    if lead.decision_maker_engaged: v += 20
    if lead.avg_response_minutes is not None and lead.avg_response_minutes <= 30: v += 15
    v += min(20, deal_size_factor(lead))      # 0..20
    lead.score_value = v
    lead.score_grade = 'A' if v>=70 else 'B' if v>=40 else 'C'
    lead.score_updated_at = now
```

Скоринг влияет на:
- **сортировку UI / workload** — индекс `(company, score_grade, next_action_date)`;
- **уведомления** — правило `score=A` + `is_at_risk` → срочный пуш менеджеру;
- триггерится при изменении факторов (signal) и при добавлении активности.

---

## 4. Automation engine

### 4.1. Два источника триггеров

| Тип | Как ловим | Примеры |
|---|---|---|
| **Событийные** (синхронно) | `events.emit()` из сервисов → `AutomationEngine.run(event)` | stage_changed, activity_added, proposal_opened, lead_won, lead_lost |
| **Временные** (Celery Beat) | периодические сканы | no_activity_24h, task_overdue, sla_breach |

`core/celery.py` уже есть. Добавить `CELERY_BEAT_SCHEDULE` (или `django-celery-beat`):

```python
CELERY_BEAT_SCHEDULE = {
  'consalting-scan-no-activity': {'task':'apps.consalting.tasks.scan_no_activity','schedule': crontab(minute='*/30')},
  'consalting-scan-overdue':     {'task':'apps.consalting.tasks.scan_overdue_tasks','schedule': crontab(minute='*/15')},
  'consalting-scan-sla':         {'task':'apps.consalting.tasks.scan_sla_breach','schedule': crontab(minute='*/30')},
}
```

### 4.2. Движок (декларативные правила)

```python
def run(event):                       # event = {trigger, lead, actor, ctx}
    rules = AutomationRuleConsalting.objects.filter(
        company=event.lead.company, is_active=True, trigger=event.trigger
    ).filter(Q(funnel__isnull=True) | Q(funnel=event.lead.funnel)).order_by('priority')
    for rule in rules:
        if not conditions.match(rule.conditions, event):       # JSON-предикат
            continue
        dedup = f"{rule.id}:{event.lead.id}:{event.trigger}:{day}"
        if AutomationLogConsalting.objects.filter(dedup_key=dedup).exists():
            continue                                           # идемпотентность
        results = [actions.run(a, event) for a in rule.actions]
        AutomationLogConsalting.objects.create(rule=rule, lead=event.lead,
            trigger=event.trigger, matched=True, actions_result=results, dedup_key=dedup)
```

### 4.3. Action handlers

| Action | Делает |
|---|---|
| `create_task` | `LeadTaskService.create(...)` + обновляет `next_action_*` |
| `notify_manager` | Channels `group_send` менеджеру + `LeadActivity(AUTOMATION)` |
| `set_at_risk` | `lead.is_at_risk=True`, `risk_reason=...`, лог |
| `require_field` | помечает сделку как требующую поле (блок перехода вперёд) |
| `start_pipeline` | создаёт онбординг-сущности (WON → ONBOARDING flow) |
| `set_score` / `recalculate_score` | вызывает `ScoringService` |
| `set_loss_required` | при попытке LOST без причины — 400 |

### 4.4. Дефолтные системные правила (сидируются)

- `no_activity` 24h в активной стадии → `set_at_risk` + `notify_manager`.
- `stage→PROPOSAL_SENT` → `create_task(follow_up, +2 дня)`.
- `proposal_opened` → `notify_manager`.
- `lead_won` → `start_pipeline(onboarding)` + `create_task(onboarding_call)`.
- `lead_lost` → `require loss_reason` (enforced и в state machine).
- `task_overdue` → `notify_manager` + `set_at_risk`.

---

## 5. API endpoints (расширение существующих)

Существующие сохраняются. Добавляются:

| Метод | URL | Назначение |
|---|---|---|
| POST | `/leads/<id>/move-stage/` | **через state machine** (валидация + 400 с errors) |
| GET  | `/leads/<id>/allowed-transitions/` | список разрешённых целевых стадий (для UI: какие колонки активны) |
| GET  | `/leads/<id>/timeline/` | лента активностей (audit), пагинация |
| POST | `/leads/<id>/activities/` | добавить note/call/message/meeting/file |
| GET/POST | `/leads/<id>/tasks/` | задачи сделки |
| PATCH | `/lead-tasks/<id>/` | завершить/отменить задачу |
| POST | `/leads/<id>/recalculate-score/` | пересчитать скоринг |
| POST | `/leads/<id>/win/` | закрыть как WON (через SM) |
| POST | `/leads/<id>/lose/` | закрыть как LOST (`loss_reason` обязателен) |
| GET/POST/PATCH | `/loss-reasons/` | справочник причин |
| GET/POST/PATCH | `/automation-rules/` | правила автоматизации |
| GET | `/funnels/<id>/analytics/` | метрики воронки (раздел 7) |
| GET | `/leads/?at_risk=true&score_grade=A&ordering=next_action_date` | рабочий список менеджера |

`move-stage` ответ при запрете:
```json
{ "detail": "Переход запрещён", "errors": [
   "Нельзя пропускать стадию: NEW_LEAD → WON",
   "Заполните next_action_date перед переходом" ] }   // 400
```

---

## 6. Event system

```python
# funnel/events.py — тонкая обёртка над Django signals
funnel_event = django.dispatch.Signal()   # kwargs: trigger, lead, actor, ctx

def emit(trigger, lead, actor=None, **ctx):
    funnel_event.send(sender=lead.__class__, trigger=trigger, lead=lead, actor=actor, ctx=ctx)
```

```python
# signals.py
@receiver(funnel_event)
def _dispatch(sender, trigger, lead, actor, ctx, **kw):
    AutomationEngine.run(Event(trigger, lead, actor, ctx))   # синхронно, быстро
    realtime.push(lead, trigger)                              # Channels group_send
```

- **Синхронно** обрабатываем лёгкие события (создать задачу, выставить флаг, уведомить).
- **Тяжёлое** (внешние интеграции, рассылки) — `transaction.on_commit(lambda: celery_task.delay(...))`.
- **Real-time**: `group_send('manager_<owner_id>', {...})` — у проекта уже есть Channels.

Каноничные события: `lead_created, stage_changed, activity_added, score_changed,
task_created, task_completed, task_overdue, no_activity, sla_breach, proposal_opened,
lead_won, lead_lost, reopened`.

---

## 7. Pipeline analytics (`PipelineAnalytics`)

`GET /funnels/<id>/analytics/?date_from=&date_to=&branch=&owner=`

```json
{
  "totals": {"deals": 320, "pipeline_value": 4250000, "won": 58, "lost": 92,
             "win_rate": 0.387, "avg_cycle_days": 14.2},
  "stages": [
    {"stage_type":"qualification","name":"Квалификация","count":40,
     "value":520000,"avg_hours_in_stage":36.5,
     "conversion_to_next":0.62,"drop_off_rate":0.18}
  ],
  "by_loss_reason": [{"code":"price_high","label":"Дорого","count":31}],
  "by_score": {"A":45,"B":120,"C":155}
}
```

Как считается (на `StageTransitionConsalting` + `LeadConsalting`):
- **count / value** — `GROUP BY stage` + `Sum(estimated_value)`.
- **avg time in stage** — `Avg(seconds_in_prev)` по переходам ИЗ стадии.
- **conversion per stage** — ушедшие вперёд / всего входивших в стадию.
- **drop-off** — доля проигранных/застрявших на стадии.
- **win_rate** — won / (won+lost). **avg_cycle** — `won_at − created_at`.

Тяжёлые отчёты — кэш (Redis, проект уже использует) + пересчёт по событиям/Celery.

---

## 8. Edge cases

| Случай | Обработка |
|---|---|
| Гонка двух менеджеров двигают одну сделку | `select_for_update()` в `transition()`, оптимистичная блокировка по `updated_at` |
| Стадию удалили, а в логе она нужна | в `StageTransition` храним snapshot `from_type/to_type` строкой; FK = SET_NULL |
| Лид без стадии (`unassigned`) | разрешено только пока статус `NEW`; перед PROPOSAL — стадия обязательна |
| Перенос воронки у лида | запрещён, если есть переходы/задачи; либо «архив + клон» |
| WON → backward | запрещено state machine; reopen только спец-правом, пишет `reopened` event |
| LOST без причины | 400 на уровне SM и сериализатора |
| Дубль автоматизации (повторный скан) | `dedup_key` в `AutomationLog` |
| Часовые пояса в `next_action_date`/SLA | всё в UTC (`USE_TZ`), сравнение `timezone.now()` |
| Массовый импорт лидов | bulk-путь без событий + отдельная команда пересчёта скоринга/SLA |
| Удаление менеджера (owner) | `owner=SET_NULL`, задачи переназначаются правилом `reassign_on_owner_delete` |
| Тысячи сделок на менеджера | индексы `(company,owner,next_action_date)`, `(company,is_at_risk)`, пагинация, сканы батчами `iterator()` |
| Активность нельзя редактировать | timeline append-only: нет update/delete эндпоинтов, правка = новая запись `system` |

---

## 9. План внедрения (фазами, без даунтайма)

1. **Миграция-расширение** (nullable-поля + новые таблицы), backfill:
   `stage_type` по текущим стадиям, `stage_entered_at = updated_at`, `last_activity_at`.
2. **Сиды**: дефолтные `LossReason`, системные `AutomationRule`.
3. **State machine** включить в `move-stage` (сначала «мягкий режим»: только лог нарушений, потом enforce).
4. **ScoringService + ActivityLogger** + timeline-эндпоинты.
5. **Celery Beat** сканы (no_activity / overdue / sla) → флаги риска.
6. **Automation engine** (события) + real-time уведомления.
7. **Analytics** эндпоинт + кэш.

Каждая фаза — отдельный PR, обратносовместима. Существующая воронка работает на всех этапах.

---

## 10. Сводка «что добавляем»

- **3 расширенные модели**: `FunnelStageConsalting` (+stage_type/transitions/SLA),
  `LeadConsalting` (+scoring/next_action/risk/loss/lifecycle).
- **6 новых моделей**: `LeadActivity`, `StageTransition`, `LossReason`, `LeadTask`,
  `AutomationRule`, `AutomationLog` (все с суффиксом `Consalting`).
- **Сервисный слой** `funnel/`: state machine, scoring, activity, tasks, analytics, events, automation.
- **Celery** сканы + **Channels** уведомления — на существующей инфраструктуре.
- **~12 новых эндпоинтов** поверх текущих, без удаления старых.

---

## 11. Статус реализации

### ✅ Сделано (Фазы 1–3)
- **Схема**: расширены `FunnelStageConsalting` (`stage_type`, `allowed_next`,
  `required_fields`, `sla_hours`, `allow_skip`; `is_final/is_success` выводятся в `save()`)
  и `LeadConsalting` (скоринг, next_action, риск/тайминги, loss, lifecycle).
  Новые таблицы: `LossReasonConsalting`, `LeadActivityConsalting`,
  `StageTransitionConsalting`, `LeadTaskConsalting`, `AutomationRuleConsalting`,
  `AutomationLogConsalting`. Миграция `0004`.
- **Сервисы** `apps/consalting/funnel/`: `state_machine.py` (матрица + guards + атомарный
  `transition`), `scoring.py` (`ScoringService`), `activity.py` (`ActivityLogger`),
  `events.py` (шина событий).
- **State machine в `move-stage`** — «мягкий режим» по умолчанию
  (`settings.CONSALTING_FUNNEL_STRICT = False` → нарушения логируются, не блокируют;
  `True` → 400). Win/Lose закрытие через SM.
- **Эндпоинты**: `move-stage` (через SM), `allowed-transitions`, `timeline`,
  `activities` (POST), `recalculate-score`, `win`, `lose`, `lead-tasks` CRUD,
  `loss-reasons` CRUD.
- **Сиды**: `python manage.py seed_consalting_funnel --all [--with-funnel]`.
- **Admin**: все новые модели (audit-таблицы — read-only).

### ✅ Сделано (Фазы 4–7)
- **Automation engine** (`funnel/automation/`): `engine.py` (правила по триггеру + дедуп),
  `conditions.py` (JSON-предикат, включая порог `hours`), `actions.py`
  (`create_task`, `notify_manager`, `set_at_risk`, `recalculate_score`, `start_pipeline`).
  Подписчик на `funnel_event` в `signals.py` (регистрируется в `apps.ready()`).
- **Celery Beat сканы** (`tasks.py`): `scan_no_activity`, `scan_overdue_tasks`,
  `scan_sla_breach` + `CELERY_BEAT_SCHEDULE` в `core/settings.py`.
- **Real-time** (`funnel/realtime.py`): `group_send` в `consalting_user_<id>` /
  `consalting_company_<id>` (best-effort; консьюмер/ws-роутинг — на стороне фронта).
- **Analytics**: `funnel/analytics.py` + `GET /funnels/<id>/analytics/`.
- **Тесты**: `tests_funnel.py` (E2E: state machine, scoring, автоматизация, win/lost,
  аналитика) — проверены на sqlite.

### ⏳ Заметки / не критично
- **Backfill** существующих лидов (`stage_entered_at`, `last_activity_at`) не делался:
  SM падает обратно на `created_at`, когда `stage_entered_at` пуст.
- **WS-консьюмер** для воронки не добавлен — `realtime.push` шлёт в группы, подписку
  настраивает ws-слой фронта (по образцу `apps/cafe`).
- **`proposal_opened`** эмитится только при ручном вызове (нужен трекинг открытия КП).
```
