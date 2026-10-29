"""
Правила критичности (ТЗ ч.7, п. 3.4) и сравнение версий.

DEFAULT_RULES / seed_default_rules() можно вызвать из миграции (RunPython):

    from apps.support.rules import seed_default_rules
    def forwards(apps, schema_editor):
        seed_default_rules(apps.get_model("support", "SupportAlertRule"))
"""
import logging
import re
from datetime import timedelta
from typing import Iterable, List, Optional, Tuple

from django.core.cache import cache
from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

logger = logging.getLogger("support.rules")

LEVEL_RANK = {"warning": 1, "error": 2, "critical": 3}

DEFAULT_RULES: List[dict] = [
    {
        "code": "payment-error",
        "name": "Не проходит оплата (payment, error и выше)",
        "category": "payment", "min_level": "error", "message_pattern": "",
        "scope": "any", "threshold": 1, "window_minutes": 60, "position": 10,
    },
    {
        "code": "shift-error",
        "name": "Не открывается / не закрывается смена",
        "category": "shift", "min_level": "error", "message_pattern": "",
        "scope": "any", "threshold": 1, "window_minutes": 60, "position": 20,
    },
    {
        "code": "crash",
        "name": "Программа упала или не запускается",
        "category": "crash", "min_level": "error", "message_pattern": "",
        "scope": "any", "threshold": 1, "window_minutes": 60, "position": 30,
    },
    {
        "code": "offline-30min",
        "name": "Офлайн-продажи не досылаются дольше 30 минут",
        "category": "offline", "min_level": "error", "message_pattern": "",
        "scope": "duration", "threshold": 30, "window_minutes": 24 * 60, "position": 40,
    },
    {
        "code": "print-3x10min",
        "name": "Не печатается чек 3 раза за 10 минут",
        "category": "print", "min_level": "error", "message_pattern": "",
        "scope": "cases", "threshold": 3, "window_minutes": 10, "position": 50,
    },
    {
        "code": "drawer-3x10min",
        "name": "Не открывается денежный ящик 3 раза за 10 минут",
        "category": "drawer", "min_level": "error", "message_pattern": "",
        "scope": "cases", "threshold": 3, "window_minutes": 10, "position": 60,
    },
    {
        "code": "login-all-employees",
        "name": "Ошибка входа у всех сотрудников компании",
        "category": "login", "min_level": "error", "message_pattern": "",
        "scope": "company_logins", "threshold": 3, "window_minutes": 60, "position": 70,
    },
    {
        "code": "update-error",
        "name": "Обновление не ставится",
        "category": "update", "min_level": "error", "message_pattern": "",
        "scope": "any", "threshold": 1, "window_minutes": 60, "position": 80,
    },
    {
        "code": "local-db-corrupt",
        "name": "Повреждение локальной базы",
        "category": "", "min_level": "warning",
        "message_pattern": r"database disk image is malformed|sqlite error 11\b",
        "scope": "any", "threshold": 1, "window_minutes": 60, "position": 90,
    },
    {
        "code": "five-shops-hour",
        "name": "Любая ошибка у 5 и больше магазинов за час",
        "category": "", "min_level": "error", "message_pattern": "",
        "scope": "shops", "threshold": 5, "window_minutes": 60, "position": 100,
    },
]

RULES_CACHE_KEY = "support:alert_rules:v1"
RULES_CACHE_TTL = 60


def seed_default_rules(model=None) -> int:
    """Создаёт отсутствующие правила по умолчанию (по code). Возвращает число созданных."""
    if model is None:
        from apps.support.models import SupportAlertRule as model  # noqa: N813
    created = 0
    for rule in DEFAULT_RULES:
        data = dict(rule)
        code = data.pop("code")
        data.setdefault("severity", "critical")
        data.setdefault("enabled", True)
        _, was_created = model.objects.get_or_create(code=code, defaults=data)
        created += int(was_created)
    return created


def ensure_default_rules() -> None:
    """Ленивая инициализация: если таблица правил пуста — заполнить по умолчанию."""
    from apps.support.models import SupportAlertRule

    if cache.get("support:alert_rules:seeded"):
        return
    try:
        if not SupportAlertRule.objects.exists():
            seed_default_rules(SupportAlertRule)
            invalidate_rules_cache()
        cache.set("support:alert_rules:seeded", 1, 3600)
    except Exception:
        logger.exception("Failed to seed default support alert rules")


def invalidate_rules_cache() -> None:
    cache.delete(RULES_CACHE_KEY)


def load_rules() -> List[dict]:
    rules = cache.get(RULES_CACHE_KEY)
    if rules is not None:
        return rules
    from apps.support.models import SupportAlertRule

    ensure_default_rules()
    rules = list(
        SupportAlertRule.objects.filter(enabled=True)
        .order_by("position", "id")
        .values(
            "code", "category", "min_level", "message_pattern", "scope",
            "threshold", "window_minutes", "severity",
        )
    )
    cache.set(RULES_CACHE_KEY, rules, RULES_CACHE_TTL)
    return rules


def _pattern_matches(pattern: str, message: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, message or "", re.IGNORECASE) is not None
    except re.error:
        logger.warning("Invalid support rule regex: %r", pattern)
        return False


def _company_employee_count(company_id) -> int:
    if not company_id:
        return 0
    try:
        from apps.users.models import User

        qs = User.objects.filter(company_id=company_id)
        try:
            qs = qs.filter(is_active=True)
        except Exception:
            pass
        n = qs.count()
        # владелец может не быть «сотрудником» через FK company
        from apps.users.models import Company

        if Company.objects.filter(pk=company_id, owner__company_id__isnull=True).exists():
            n += 1
        return n
    except Exception:
        return 0


def _threshold_reached(rule: dict, report) -> bool:
    from apps.support.models import SupportErrorReport

    scope = rule["scope"]
    if scope == "any":
        return True

    now = timezone.now()
    since = now - timedelta(minutes=max(1, int(rule["window_minutes"] or 60)))
    threshold = max(1, int(rule["threshold"] or 1))
    base = SupportErrorReport.objects.filter(fingerprint=report.fingerprint, last_at__gte=since)

    if scope == "cases":
        if report.company_id:
            qs = base.filter(company_id=report.company_id)
        elif report.device_id:
            qs = base.filter(device_id=report.device_id)
        else:
            qs = base.filter(pk=report.pk)
        total = qs.aggregate(s=Sum("count"))["s"] or 0
        return total >= threshold

    if scope == "shops":
        shops = (
            base.filter(company__isnull=False).values("company_id").distinct().count()
        )
        return shops >= threshold

    if scope == "duration":
        if report.company_id:
            qs = base.filter(company_id=report.company_id)
        elif report.device_id:
            qs = base.filter(device_id=report.device_id)
        else:
            qs = base.filter(pk=report.pk)
        agg = qs.aggregate(first=Min("first_at"), last=Max("last_at"))
        if not agg["first"] or not agg["last"]:
            return False
        return (agg["last"] - agg["first"]).total_seconds() >= threshold * 60

    if scope == "company_logins":
        if not report.company_id:
            return False
        logins = (
            base.filter(company_id=report.company_id)
            .exclude(login="")
            .values("login")
            .distinct()
            .count()
        )
        employees = _company_employee_count(report.company_id)
        need = min(threshold, employees) if employees else threshold
        return logins >= max(1, need)

    return False


def evaluate_report_severity(report) -> Tuple[str, Optional[str]]:
    """
    Возвращает (severity, rule_code). Если ни одно правило не сработало —
    warning для уровня warning, иначе error.
    """
    level_rank = LEVEL_RANK.get(report.level, 2)
    for rule in load_rules():
        if rule["category"] and rule["category"] != report.category:
            continue
        if level_rank < LEVEL_RANK.get(rule["min_level"], 2):
            continue
        if not _pattern_matches(rule["message_pattern"], report.message):
            continue
        try:
            if _threshold_reached(rule, report):
                return rule["severity"], rule["code"]
        except Exception:
            logger.exception("Support rule %s evaluation failed", rule.get("code"))
    return ("warning" if report.level == "warning" else "error"), None


# ---------------------------------------------------------------- версии

def version_key(v: str) -> tuple:
    """Ключ сортировки версий: '1.17.100' > '1.17.38'."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (-1,)


def version_gte(a: str, b: str) -> bool:
    """a >= b с корректным сравнением версий (packaging, иначе по числам)."""
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(a) >= Version(b)
        except InvalidVersion:
            pass
    except ImportError:
        pass
    return version_key(a) >= version_key(b)


def is_valid_version(v: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+){0,3}(?:[-+.]?[0-9A-Za-z.]+)?", (v or "").strip()))


def sort_versions(versions: Iterable[str]) -> List[str]:
    return sorted({v for v in versions if v}, key=version_key)
