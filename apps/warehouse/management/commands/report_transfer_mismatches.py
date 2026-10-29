"""
QA B01: отчёт по перемещениям, зачисленным не на тот товар.

Раньше при проведении TRANSFER товар на складе-получателе искался в том числе по коду
(коды начинались с 0001 в каждом складе) и по артикулу+названию, поэтому приход уходил
на чужую карточку. Для каждого проведённого TRANSFER сравниваем товар-источник и товар,
на который фактически зачислен приход (движение на складе-получателе): название,
единица, штрихкод. В отчёт попадают пары, где совпал только код или только название.

Ничего не исправляет (решение D-B01 — за владельцами). Опции:
  --company <uuid>   одна компания
  --all              печатать и совпавшие пары
  --csv <путь>       сохранить отчёт в CSV

  python manage.py report_transfer_mismatches --company <uuid> --csv /tmp/b01.csv
"""
from __future__ import annotations

import csv

from django.core.management.base import BaseCommand

from apps.warehouse import models as m


def _norm(v):
    return (v or "").strip().lower()


class Command(BaseCommand):
    help = "Отчёт: перемещения, зачисленные на товар с другим названием/штрихкодом (dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None)
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--csv", default=None)

    def handle(self, *args, **opts):
        docs = m.Document.objects.filter(
            doc_type=m.Document.DocType.TRANSFER, status=m.Document.Status.POSTED
        ).select_related("warehouse_from", "warehouse_to", "company").order_by("date")
        if opts["company"]:
            docs = docs.filter(company_id=opts["company"])

        rows = []
        checked = 0
        for doc in docs.iterator(chunk_size=200):
            moves_in = {
                mv.product_id: mv
                for mv in doc.moves.filter(warehouse_id=doc.warehouse_to_id, qty_delta__gt=0).select_related("product")
            }
            in_list = list(moves_in.values())
            for item in doc.items.select_related("product"):
                src = item.product
                if src is None:
                    continue
                checked += 1
                dest = item.target_product
                if dest is None:
                    # Старые документы: ищем приход той же величины на складе-получателе.
                    cand = [mv for mv in in_list if mv.qty_delta == item.qty]
                    dest = cand[0].product if len(cand) == 1 else None
                    if dest is None and len(in_list) == 1:
                        dest = in_list[0].product
                if dest is None:
                    continue
                same_name = _norm(src.name) == _norm(dest.name)
                same_unit = _norm(src.unit) == _norm(dest.unit)
                same_barcode = bool(_norm(src.barcode)) and _norm(src.barcode) == _norm(dest.barcode)
                same_code = bool(_norm(src.code)) and _norm(src.code) == _norm(dest.code)
                ok = same_barcode or (same_name and same_unit and (not src.barcode or not dest.barcode or same_barcode))
                reason = []
                if same_code and not same_name:
                    reason.append("совпал только код")
                if same_name and not same_barcode and src.barcode and dest.barcode:
                    reason.append("совпало только название (штрихкоды разные)")
                if not same_name and not same_barcode and not same_code:
                    reason.append("ничего не совпало")
                if ok and not reason and not opts["all"]:
                    continue
                rows.append({
                    "company": getattr(doc.company, "name", ""),
                    "document": doc.number or str(doc.pk),
                    "date": doc.date.date().isoformat() if doc.date else "",
                    "warehouse_from": doc.warehouse_from.name if doc.warehouse_from else "",
                    "warehouse_to": doc.warehouse_to.name if doc.warehouse_to else "",
                    "qty": str(item.qty),
                    "source_id": str(src.pk),
                    "source_name": src.name,
                    "source_code": src.code or "",
                    "source_barcode": src.barcode or "",
                    "target_id": str(dest.pk),
                    "target_name": dest.name,
                    "target_code": dest.code or "",
                    "target_barcode": dest.barcode or "",
                    "problem": "; ".join(reason) or ("ok" if ok else "проверить"),
                })

        for r in rows:
            self.stdout.write(
                f"{r['company']} | {r['document']} {r['date']} | {r['warehouse_from']} → {r['warehouse_to']} | "
                f"{r['qty']} | «{r['source_name']}» ({r['source_code']}/{r['source_barcode']}) → "
                f"«{r['target_name']}» ({r['target_code']}/{r['target_barcode']}) | {r['problem']}"
            )
        if opts["csv"] and rows:
            with open(opts["csv"], "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(f"CSV: {opts['csv']}")
        self.stdout.write(f"Проверено строк перемещений: {checked}; в отчёте: {len(rows)}.")
