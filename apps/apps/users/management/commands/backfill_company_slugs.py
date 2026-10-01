import uuid
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.users.models import Company  # поправь импорт под себя


def make_unique_slug(base: str) -> str:
    base = (base or "").strip()
    base = slugify(base)[:60] or "company"
    candidate = base
    i = 2
    while Company.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{i}"
        i += 1
        if len(candidate) > 80:
            candidate = f"{base[:50]}-{uuid.uuid4().hex[:8]}"
    return candidate


class Command(BaseCommand):
    help = "Fill slug for companies where slug is empty/null"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        limit = opts["limit"]

        qs = Company.objects.filter(slug__isnull=True) | Company.objects.filter(slug="")
        qs = qs.order_by("created_at")

        if limit:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(self.style.WARNING(f"Found {total} companies without slug"))

        updated = 0
        for c in qs.select_for_update():
            slug = make_unique_slug(c.name)
            if not dry:
                Company.objects.filter(pk=c.pk).update(slug=slug)
            updated += 1

        if dry:
            self.stdout.write(self.style.SUCCESS(f"DRY RUN: would update {updated} companies"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated {updated} companies"))
