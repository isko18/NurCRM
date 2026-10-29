from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.construction.models import CashFlow, CashShift
from apps.main.models import SaleItem, SaleReturn
from apps.main.pos_utils import money, qty3


def _line(item, qty):
    base_qty = Decimal(str(item.quantity or 0))
    disc = Decimal(str(item.line_discount or 0))
    part_disc = disc * (qty / base_qty) if base_qty > 0 else Decimal("0")
    return {
        "sale_item": str(item.id),
        "product": str(item.product_id) if item.product_id else None,
        "name": item.name_snapshot,
        "qty": str(qty3(qty)),
        "amount": str(money((item.unit_price or Decimal("0")) * qty - part_disc)),
    }


class Command(BaseCommand):
    help = "Заполняет состав (returned_items) и смену у возвратов, записанных до 28.09.2026."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = SaleReturn.objects.filter(Q(returned_items__isnull=True) | Q(shift__isnull=True)).select_related("sale")
        total = qs.count()
        filled_items = filled_shift = 0
        for r in qs.iterator():
            updates = {}
            if r.returned_items is None:
                updates["returned_items"] = self._items(r)
                filled_items += 1
            if r.shift_id is None:
                shift_id = self._shift_id(r)
                if shift_id:
                    updates["shift_id"] = shift_id
                    filled_shift += 1
            if updates and not opts["dry_run"]:
                SaleReturn.objects.filter(pk=r.pk).update(**updates)
        self.stdout.write(self.style.SUCCESS(
            f"Возвратов: {total}, состав заполнен: {filled_items}, смена найдена: {filled_shift}"
            + (" (dry-run)" if opts["dry_run"] else "")
        ))

    def _items(self, r):
        payload = r.items_payload
        if not payload:
            # полный возврат: строки чека остаются
            return [_line(it, Decimal(str(it.quantity or 0))) for it in SaleItem.objects.filter(sale_id=r.sale_id)]
        lines = []
        for row in payload:
            try:
                item_id, qty = row[0], Decimal(str(row[1]))
            except (IndexError, TypeError, ValueError, ArithmeticError):
                continue
            item = SaleItem.objects.filter(pk=item_id).first()
            if item is not None:
                lines.append(_line(item, qty))
            else:
                # строка чека удалена (вернули её целиком) — известно только количество
                lines.append({"sale_item": str(item_id), "product": None, "name": None, "qty": str(qty3(qty)), "amount": None})
        if len(lines) == 1 and lines[0]["amount"] is None:
            lines[0]["amount"] = str(r.returned_amount)
        return lines

    def _shift_id(self, r):
        t = r.created_at
        flow = (
            CashFlow.objects.filter(
                source_kind=CashFlow.SourceKind.POS_SALE_RETURN,
                source_id=str(r.sale_id),
                shift__isnull=False,
                created_at__gte=t - timedelta(minutes=2),
                created_at__lte=t + timedelta(minutes=2),
            )
            .values_list("shift_id", flat=True)
            .first()
        )
        if flow:
            return flow
        if not r.user_id:
            return None
        return (
            CashShift.objects.filter(cashier_id=r.user_id, company_id=r.company_id, opened_at__lte=t)
            .filter(Q(closed_at__isnull=True) | Q(closed_at__gte=t))
            .order_by("-opened_at")
            .values_list("id", flat=True)
            .first()
        )
