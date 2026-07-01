"""
Ретро-закрытие открытых кассовых смен.

Примеры:
    # закрыть смены удалённых кассиров И смены, открытые дольше суток (по умолчанию оба):
    python manage.py close_stale_shifts

    # только смены удалённых сотрудников:
    python manage.py close_stale_shifts --deleted

    # только зависшие смены старше 12 часов:
    python manage.py close_stale_shifts --stale --max-age-hours 12

    # посмотреть, что будет закрыто, ничего не меняя:
    python manage.py close_stale_shifts --dry-run
"""

from django.core.management.base import BaseCommand

from apps.construction.services_shifts import (
    close_shifts_for_deleted_cashiers,
    close_stale_shifts,
    DEFAULT_MAX_AGE_HOURS,
)


class Command(BaseCommand):
    help = "Закрывает открытые кассовые смены удалённых кассиров и/или смены старше N часов."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deleted", action="store_true",
            help="Закрыть открытые смены удалённых (soft-deleted) кассиров.",
        )
        parser.add_argument(
            "--stale", action="store_true",
            help="Закрыть смены, открытые дольше --max-age-hours.",
        )
        parser.add_argument(
            "--max-age-hours", type=int, default=DEFAULT_MAX_AGE_HOURS,
            help=f"Порог «зависшей» смены в часах (по умолчанию {DEFAULT_MAX_AGE_HOURS}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Только показать, какие смены будут закрыты, без изменений.",
        )

    def handle(self, *args, **options):
        # Если не указан ни один режим — выполняем оба.
        do_deleted = options["deleted"]
        do_stale = options["stale"]
        if not do_deleted and not do_stale:
            do_deleted = do_stale = True

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: изменения не сохраняются."))

        if do_deleted:
            res = close_shifts_for_deleted_cashiers(dry_run=dry_run)
            self._report("Смены удалённых кассиров", res)

        if do_stale:
            hours = options["max_age_hours"]
            res = close_stale_shifts(max_age_hours=hours, dry_run=dry_run)
            self._report(f"Смены, открытые дольше {hours} ч", res)

    def _report(self, title, res):
        closed = res.get("closed") or []
        failed = res.get("failed") or []
        self.stdout.write(self.style.SUCCESS(f"{title}: закрыто {len(closed)}."))
        for pk in closed:
            self.stdout.write(f"  ✔ {pk}")
        if failed:
            self.stdout.write(self.style.ERROR(f"{title}: ошибки {len(failed)}."))
            for pk, err in failed:
                self.stdout.write(self.style.ERROR(f"  x {pk}: {err}"))
