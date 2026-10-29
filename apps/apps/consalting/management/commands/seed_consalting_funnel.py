"""
Сиды воронки продаж консалтинга (Фаза 2): причины проигрыша, дефолтные
правила автоматизации и (опц.) воронка с каноничными стадиями.

Идемпотентно — можно запускать повторно.

Примеры:
    python manage.py seed_consalting_funnel --all
    python manage.py seed_consalting_funnel --company <uuid> --with-funnel
"""
from django.core.management.base import BaseCommand, CommandError

from apps.users.models import Company
from apps.consalting.models import (
    FunnelConsalting, FunnelStageConsalting,
    LossReasonConsalting, AutomationRuleConsalting,
)

T = FunnelStageConsalting.StageType

DEFAULT_LOSS_REASONS = [
    ("price_high", "Дорого / цена не подошла"),
    ("no_response", "Нет ответа / пропал"),
    ("competitor", "Ушёл к конкуренту"),
    ("not_relevant", "Не релевантно / не наш клиент"),
    ("timing", "Не вовремя / отложил"),
]

# (name, order, stage_type, color)
DEFAULT_STAGES = [
    ("Новый лид", 0, T.NEW_LEAD, "#95a5a6"),
    ("Первый контакт", 1, T.FIRST_CONTACT, "#3498db"),
    ("Квалификация", 2, T.QUALIFICATION, "#2980b9"),
    ("В работе", 3, T.NURTURE, "#f39c12"),
    ("КП отправлено", 4, T.PROPOSAL_SENT, "#e67e22"),
    ("Переговоры", 5, T.NEGOTIATION, "#9b59b6"),
    ("Ожидание решения", 6, T.DECISION_PENDING, "#8e44ad"),
    ("Оплачено", 7, T.WON, "#27ae60"),
    ("Онбординг", 8, T.ONBOARDING, "#16a085"),
    ("Завершено", 9, T.COMPLETED, "#2ecc71"),
    ("Потеряно", 10, T.LOST, "#e74c3c"),
]

DEFAULT_RULES = [
    {
        "name": "Нет активности 24ч → под риском",
        "trigger": AutomationRuleConsalting.Trigger.NO_ACTIVITY,
        "conditions": {"hours": 24},
        "actions": [{"type": "set_at_risk", "reason": "Нет активности более 24 часов"},
                    {"type": "notify_manager"}],
    },
    {
        "name": "КП отправлено → follow-up через 2 дня",
        "trigger": AutomationRuleConsalting.Trigger.STAGE_CHANGED,
        "conditions": {"to_type": T.PROPOSAL_SENT},
        "actions": [{"type": "create_task", "task_type": "follow_up",
                     "title": "Связаться по КП", "due_in_days": 2}],
    },
    {
        "name": "Лид выигран → запустить онбординг",
        "trigger": AutomationRuleConsalting.Trigger.LEAD_WON,
        "conditions": {},
        "actions": [{"type": "create_task", "task_type": "call",
                     "title": "Онбординг-звонок", "due_in_days": 1},
                    {"type": "notify_manager"}],
    },
    {
        "name": "Задача просрочена → под риском",
        "trigger": AutomationRuleConsalting.Trigger.TASK_OVERDUE,
        "conditions": {},
        "actions": [{"type": "set_at_risk", "reason": "Просрочена задача"},
                    {"type": "notify_manager"}],
    },
]


class Command(BaseCommand):
    help = "Сиды воронки продаж консалтинга (причины проигрыша, правила, опц. воронка)."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=str, help="UUID компании")
        parser.add_argument("--all", action="store_true", help="Для всех компаний")
        parser.add_argument("--with-funnel", action="store_true",
                            help="Создать дефолтную воронку с каноничными стадиями")

    def handle(self, *args, **opts):
        if opts["all"]:
            companies = list(Company.objects.all())
        elif opts["company"]:
            companies = list(Company.objects.filter(id=opts["company"]))
            if not companies:
                raise CommandError("Компания не найдена.")
        else:
            raise CommandError("Укажите --company <uuid> или --all.")

        for company in companies:
            self._seed_company(company, with_funnel=opts["with_funnel"])
        self.stdout.write(self.style.SUCCESS(f"Готово для {len(companies)} компан(ий)."))

    def _seed_company(self, company, with_funnel=False):
        for code, label in DEFAULT_LOSS_REASONS:
            LossReasonConsalting.objects.get_or_create(
                company=company, code=code, defaults={"label": label}
            )
        for rule in DEFAULT_RULES:
            AutomationRuleConsalting.objects.get_or_create(
                company=company, name=rule["name"],
                defaults={"trigger": rule["trigger"], "conditions": rule["conditions"],
                          "actions": rule["actions"]},
            )
        if with_funnel:
            funnel, created = FunnelConsalting.objects.get_or_create(
                company=company, branch=None, name="Основная воронка",
                defaults={"description": "Создана автоматически"},
            )
            if created:
                for name, order, stype, color in DEFAULT_STAGES:
                    FunnelStageConsalting.objects.create(
                        company=company, branch=None, funnel=funnel,
                        name=name, order=order, stage_type=stype, color=color,
                    )
        self.stdout.write(f"  · {company}")
