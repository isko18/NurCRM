"""
Воркер технического бота (ТЗ ч.7, п. 3.3–3.6): группировка отчётов в проблемы,
критичность по таблице правил, оповещения команде, сводка, проверка новой версии,
очистка старых данных.
"""
import logging
from datetime import timedelta
from typing import List, Optional, Tuple

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Min, Q, Sum
from django.utils import timezone

from apps.support.bot import (
    esc,
    format_new_critical_alert,
    format_regression_alert,
    format_surge_alert,
    format_version_worse_alert,
    send_team_alert,
    _category_label,
)
from apps.support.models import SupportErrorReport, SupportIssue, SupportReportAttachment
from apps.support.rules import evaluate_report_severity, sort_versions, version_gte, version_key

logger = logging.getLogger("support.tasks")

SURGE_SHOPS = 5
SURGE_CASES = 20
ALERT_INTERVAL = timedelta(hours=1)
REPORTS_RETENTION = timedelta(days=90)
ATTACHMENTS_RETENTION = timedelta(days=30)
SEVERITY_RANK = {"warning": 1, "error": 2, "critical": 3}


def _process_one(rep: SupportErrorReport) -> List[Tuple[str, str]]:
    """Группирует один отчёт; возвращает список (kind, text) оповещений к отправке после commit."""
    alerts: List[Tuple[str, str]] = []
    now = timezone.now()

    with transaction.atomic():
        issue, created = SupportIssue.objects.get_or_create(
            fingerprint=rep.fingerprint,
            defaults={
                "title": (rep.message or "")[:500] or rep.category,
                "category": rep.category,
                "severity": SupportIssue.Severity.WARNING if rep.level == "warning" else SupportIssue.Severity.ERROR,
                "first_seen": rep.first_at,
                "last_seen": rep.last_at,
                "occurrences": 0,
                "companies_count": 0,
                "versions": [],
                "status": SupportIssue.Status.NEW,
            },
        )
        # Блокируем строку проблемы: параллельные воркеры и команды бота не теряют обновления
        issue = SupportIssue.objects.select_for_update().get(pk=issue.pk)

        # Отчёт уже учтён (повторная обработка) — ничего не делаем
        locked_rep = SupportErrorReport.objects.select_for_update().filter(pk=rep.pk).first()
        if locked_rep is None or locked_rep.processed_at is not None:
            return []

        issue.occurrences = (issue.occurrences or 0) + max(1, rep.count or 1)
        if rep.last_at and rep.last_at > issue.last_seen:
            issue.last_seen = rep.last_at
        if rep.first_at and rep.first_at < issue.first_seen:
            issue.first_seen = rep.first_at
        if rep.version and rep.version not in (issue.versions or []):
            issue.versions = sort_versions(list(issue.versions or []) + [rep.version])

        # Привязываем отчёт до подсчёта магазинов
        SupportErrorReport.objects.filter(pk=rep.pk).update(issue=issue, processed_at=now)
        rep.issue = issue

        issue.companies_count = (
            SupportErrorReport.objects.filter(issue=issue, company__isnull=False)
            .values("company_id").distinct().count()
        )

        if rep.company_id and not issue.sample_company_name:
            issue.sample_company_name = rep.company.name if rep.company else ""
            issue.sample_login = rep.login or ""
            issue.sample_app = rep.app or ""
            issue.sample_version = rep.version or ""
        if not issue.sample_stack and rep.stack:
            issue.sample_stack = rep.stack[:4000]
        if not issue.sample_app:
            issue.sample_app = rep.app or ""
            issue.sample_login = issue.sample_login or rep.login or ""
            issue.sample_version = rep.version or ""

        # Важность по таблице правил; критичность «залипает»
        severity, rule_code = evaluate_report_severity(rep)
        if SEVERITY_RANK.get(severity, 2) > SEVERITY_RANK.get(issue.severity, 2):
            issue.severity = severity

        # Заглушка истекла
        muted_active = False
        if issue.status == SupportIssue.Status.MUTED:
            if issue.muted_until and issue.muted_until > now:
                muted_active = True
            else:
                issue.status = SupportIssue.Status.NEW
                issue.muted_until = None

        # Возврат исправленной ошибки: версия отчёта >= версии исправления
        regression = False
        if issue.status == SupportIssue.Status.FIXED:
            if not issue.fixed_version or (rep.version and version_gte(rep.version, issue.fixed_version)):
                regression = True
                issue.status = SupportIssue.Status.NEW

        if regression:
            alerts.append(("regression", format_regression_alert(issue, rep.version)))
            issue.critical_alert_at = now  # не слать сразу следом «всплеск»
        elif not muted_active and issue.severity == SupportIssue.Severity.CRITICAL:
            if not issue.alert_sent:
                # Новая критичная проблема — ровно один раз (флаг ставится под блокировкой)
                issue.alert_sent = True
                issue.critical_alert_at = now
                alerts.append(("new_critical", format_new_critical_alert(issue, rep)))
            else:
                last_alert = max(
                    [d for d in (issue.critical_alert_at, issue.last_surge_alert_at) if d],
                    default=None,
                )
                if last_alert is None or now - last_alert >= ALERT_INTERVAL:
                    hour_qs = SupportErrorReport.objects.filter(issue=issue, created_at__gte=now - ALERT_INTERVAL)
                    hour_cases = hour_qs.aggregate(s=Sum("count"))["s"] or 0
                    hour_shops = (
                        hour_qs.filter(company__isnull=False).values("company_id").distinct().count()
                    )
                    if hour_shops >= SURGE_SHOPS or hour_cases >= SURGE_CASES:
                        issue.last_surge_alert_at = now
                        alerts.append(("surge", format_surge_alert(issue, hour_shops, hour_cases)))

        issue.save()

    return alerts


@shared_task(name="apps.support.tasks.process_incoming_reports", ignore_result=True)
def process_incoming_reports(report_ids: list):
    """Фоновая группировка поступивших отчётов и оповещения команде."""
    reports = list(
        SupportErrorReport.objects.filter(id__in=report_ids, processed_at__isnull=True)
        .select_related("company")
        .order_by("created_at", "first_at")
    )
    for rep in reports:
        try:
            alerts = _process_one(rep)
        except Exception:
            logger.exception("Error processing support report %s", rep.id)
            continue
        for _kind, text in alerts:
            send_team_alert(text)


@shared_task(name="apps.support.tasks.process_pending_reports", ignore_result=True)
def process_pending_reports(batch_size: int = 500):
    """Подбирает отчёты, которые не дошли до воркера (сбой очереди при приёме)."""
    cutoff = timezone.now() - timedelta(minutes=2)
    ids = list(
        SupportErrorReport.objects.filter(processed_at__isnull=True, created_at__lt=cutoff)
        .order_by("created_at")
        .values_list("id", flat=True)[:batch_size]
    )
    if ids:
        process_incoming_reports([str(i) for i in ids])
    return {"processed": len(ids)}


# ---------------------------------------------------------------- новая версия хуже прошлой

VERSION_WINDOW = timedelta(hours=2)
VERSION_MIN_SHOPS = 5


def _version_share(app: str, version: str, since, until) -> Tuple[int, int]:
    """(магазинов с критичными, магазинов всего) среди приславших отчёты для версии в окне."""
    qs = SupportErrorReport.objects.filter(
        app=app, version=version, created_at__gte=since, created_at__lt=until, company__isnull=False
    )
    total = qs.values("company_id").distinct().count()
    crit = (
        qs.filter(issue__severity=SupportIssue.Severity.CRITICAL)
        .values("company_id").distinct().count()
    )
    return crit, total


@shared_task(name="apps.support.tasks.check_new_version_regressions", ignore_result=True)
def check_new_version_regressions():
    """
    За первые 2 часа после выпуска (первый отчёт версии) доля магазинов с критичными
    ошибками вдвое выше, чем у предыдущей версии за последние сутки, — одно сообщение на версию.
    """
    now = timezone.now()
    recent = (
        SupportErrorReport.objects.filter(created_at__gte=now - timedelta(days=30))
        .order_by()
        .values("app", "version")
        .annotate(first=Min("created_at"))
    )
    by_app: dict = {}
    for row in recent:
        by_app.setdefault(row["app"], []).append((row["version"], row["first"]))

    sent = 0
    for app, items in by_app.items():
        items.sort(key=lambda x: version_key(x[0]))
        for idx, (version, first_at) in enumerate(items):
            if idx == 0 or now - first_at > VERSION_WINDOW + timedelta(minutes=30):
                continue
            prev_version = items[idx - 1][0]
            if version_key(prev_version) >= version_key(version):
                continue
            new_crit, new_total = _version_share(app, version, first_at, first_at + VERSION_WINDOW)
            if new_total < VERSION_MIN_SHOPS or new_crit == 0:
                continue
            prev_crit, prev_total = _version_share(app, prev_version, now - timedelta(days=1), now)
            new_share = new_crit / new_total
            prev_share = (prev_crit / prev_total) if prev_total else 0.0
            worse = (new_share >= 2 * prev_share) if prev_share > 0 else (new_crit >= 3)
            if not worse:
                continue
            if not cache.add(f"support:version_worse:{app}:{version}", 1, 30 * 24 * 3600):
                continue
            send_team_alert(format_version_worse_alert(
                app, version, prev_version, new_share, prev_share, new_crit, new_total,
            ))
            sent += 1
    return {"alerts": sent}


# ---------------------------------------------------------------- сводка 09:00

@shared_task(name="apps.support.tasks.send_daily_support_digest", ignore_result=True)
def send_daily_support_digest():
    """
    10 главных проблем за сутки (по числу магазинов), новые за сутки, сколько закрыто;
    доля магазинов без падений по версиям.
    """
    now = timezone.now()
    since = now - timedelta(days=1)
    day_qs = SupportErrorReport.objects.filter(created_at__gte=since)

    top = list(
        day_qs.filter(issue__isnull=False)
        .order_by()
        .values("issue_id", "issue__title", "issue__category", "issue__severity", "issue__status")
        .annotate(shops=Count("company", distinct=True), cases=Sum("count"))
        .order_by("-shops", "-cases")[:10]
    )
    new_count = SupportIssue.objects.filter(first_seen__gte=since).count()
    new_critical = SupportIssue.objects.filter(
        first_seen__gte=since, severity=SupportIssue.Severity.CRITICAL
    ).count()
    fixed_count = SupportIssue.objects.filter(fixed_at__gte=since, status=SupportIssue.Status.FIXED).count()

    lines = [
        "☀️ <b>Сводка за сутки</b>",
        f"Новых проблем: {new_count} (критичных {new_critical}) · закрыто: {fixed_count}",
    ]
    if top:
        lines.append("\n<b>Топ-10 по магазинам:</b>")
        for i, r in enumerate(top, 1):
            icon = "🔴" if r["issue__severity"] == "critical" else ("🟡" if r["issue__severity"] == "warning" else "🟠")
            lines.append(
                f"{i}. {icon} /issue_{r['issue_id']} {esc(_category_label(r['issue__category']))}: "
                f"{esc((r['issue__title'] or '')[:60])} — магазинов {r['shops']}, случаев {r['cases']}"
            )
    else:
        lines.append("\nЗа сутки ошибок не было.")

    # Доля магазинов без падений по версиям (среди магазинов, приславших отчёты за сутки)
    ver_rows = list(
        day_qs.filter(company__isnull=False)
        .order_by()
        .values("app", "version")
        .annotate(
            total=Count("company", distinct=True),
            crashed=Count("company", distinct=True, filter=Q(category="crash")),
        )
    )
    if ver_rows:
        ver_rows.sort(key=lambda r: (r["app"], version_key(r["version"])), reverse=True)
        lines.append("\n<b>Без падений по версиям</b> (из магазинов с отчётами):")
        for r in ver_rows[:12]:
            share = 1 - (r["crashed"] / r["total"]) if r["total"] else 1
            lines.append(
                f"• {esc(r['app'])} {esc(r['version'])}: {share:.0%} "
                f"({r['total'] - r['crashed']} из {r['total']})"
            )

    send_team_alert("\n".join(lines))
    return {"digest_sent": True}


# ---------------------------------------------------------------- хранение

@shared_task(name="apps.support.tasks.cleanup_support_data", ignore_result=True)
def cleanup_support_data(chunk: int = 5000):
    """Отчёты — 90 дней, вложения (zip) — 30 дней. Проблемы хранятся бессрочно."""
    now = timezone.now()
    deleted_reports = 0
    while True:
        ids = list(
            SupportErrorReport.objects.filter(created_at__lt=now - REPORTS_RETENTION)
            .values_list("id", flat=True)[:chunk]
        )
        if not ids:
            break
        deleted_reports += SupportErrorReport.objects.filter(id__in=ids).delete()[0]

    deleted_files = 0
    for att in SupportReportAttachment.objects.filter(created_at__lt=now - ATTACHMENTS_RETENTION).iterator():
        try:
            if att.file:
                att.file.delete(save=False)
        except Exception:
            logger.warning("Failed to delete support attachment file %s", att.pk, exc_info=True)
        att.delete()
        deleted_files += 1

    return {"reports": deleted_reports, "attachments": deleted_files}
