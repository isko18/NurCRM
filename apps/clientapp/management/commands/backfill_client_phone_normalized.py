from django.core.management.base import BaseCommand

from apps.main.models import Client
from apps.main.phone_utils import normalize_phone_e164


class Command(BaseCommand):
    help = "Заполняет Client.phone_normalized (E.164) для существующих клиентов. Запустить один раз после миграции."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=2000)

    def handle(self, *args, **opts):
        batch = opts["batch"]
        updated = 0
        last_pk = None
        while True:
            qs = Client.objects.order_by("pk").only("pk", "phone", "phone_normalized")
            if last_pk is not None:
                qs = qs.filter(pk__gt=last_pk)
            rows = list(qs[:batch])
            if not rows:
                break
            changed = []
            for c in rows:
                norm = normalize_phone_e164(c.phone)
                if norm != (c.phone_normalized or ""):
                    c.phone_normalized = norm
                    changed.append(c)
            if changed:
                Client.objects.bulk_update(changed, ["phone_normalized"], batch_size=500)
                updated += len(changed)
            last_pk = rows[-1].pk
        self.stdout.write(self.style.SUCCESS(f"Обновлено клиентов: {updated}"))
