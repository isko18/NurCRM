from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.main.models import Product
from apps.main.services.webhooks import send_product_webhook


class Command(BaseCommand):
    help = "Resend existing products to external webhook (SITE_WEBHOOK_URL)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            default="",
            help="Filter by company UUID (optional).",
        )
        parser.add_argument(
            "--branch",
            default="",
            help="Filter by branch UUID (optional).",
        )
        parser.add_argument(
            "--product-ids",
            default="",
            help="Comma-separated product UUIDs to send (optional).",
        )
        parser.add_argument(
            "--codes",
            default="",
            help="Comma-separated product codes to send (optional).",
        )
        parser.add_argument(
            "--event",
            default="product.updated",
            help="Webhook event name to send (default: product.updated).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Sleep seconds between requests (default: 0).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max number of products to send (0 = all).",
        )
        parser.add_argument(
            "--updated-since",
            default="",
            help="Only products updated since ISO datetime/date (e.g. 2026-01-01 or 2026-01-01T00:00:00).",
        )

    def handle(self, *args, **options):
        company_raw = (options.get("company") or "").strip()
        branch_raw = (options.get("branch") or "").strip()
        product_ids_raw = (options.get("product_ids") or "").strip()
        codes_raw = (options.get("codes") or "").strip()

        event = str(options["event"] or "product.updated")
        sleep_s = float(options["sleep"] or 0.0)
        limit = int(options["limit"] or 0)
        updated_since_raw = (options.get("updated_since") or "").strip()

        qs = Product.objects.all()

        if company_raw:
            qs = qs.filter(company_id=company_raw)
        if branch_raw:
            qs = qs.filter(branch_id=branch_raw)

        if product_ids_raw:
            ids = [x.strip() for x in product_ids_raw.split(",") if x.strip()]
            qs = qs.filter(id__in=ids)

        if codes_raw:
            codes = [x.strip() for x in codes_raw.split(",") if x.strip()]
            qs = qs.filter(code__in=codes)

        if updated_since_raw:
            try:
                if len(updated_since_raw) == 10:
                    dt = timezone.datetime.fromisoformat(updated_since_raw)
                    dt = timezone.make_aware(dt)
                else:
                    dt = timezone.datetime.fromisoformat(updated_since_raw)
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt)
                qs = qs.filter(updated_at__gte=dt)
            except Exception:
                raise SystemExit("Invalid --updated-since value. Use YYYY-MM-DD or ISO datetime.")

        # Best-effort: prefetch relations used by ProductSerializer to avoid N+1.
        qs = (
            qs.select_related(
                "company",
                "branch",
                "brand",
                "category",
                "client",
                "created_by",
                "characteristics",
            )
            .prefetch_related(
                "images",
                "packages",
                "item_make",
            )
            .order_by("created_at")
        )

        if limit > 0:
            qs = qs[:limit]

        total = 0
        started = time.time()

        for product in qs.iterator(chunk_size=200):
            send_product_webhook(product, event)
            total += 1

            if sleep_s > 0:
                time.sleep(sleep_s)

            if total % 50 == 0:
                elapsed = max(time.time() - started, 0.001)
                self.stdout.write(f"Sent {total} products ({total/elapsed:.2f}/s)")

        elapsed = max(time.time() - started, 0.001)
        self.stdout.write(self.style.SUCCESS(f"Done. Sent {total} products in {elapsed:.1f}s"))
