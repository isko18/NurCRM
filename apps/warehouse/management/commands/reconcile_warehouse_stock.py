"""
Разовая сверка складских остатков (§5.7 stock-single-source-of-truth.md).

Режимы:

  1. Отчёт (по умолчанию, ничего не пишет):
       python manage.py reconcile_warehouse_stock [--report] [--csv out.csv] [--all]
                                                  [--company <uuid>] [--warehouse <uuid>]
     CSV: company, warehouse, product, card_qty, balance_qty, moves_sum, agent_qty,
          card_updated_at, last_move_at (+ id и колонка issues).
     По умолчанию — только строки с проблемами:
       no_balance        — регистра нет, остаток только в карточке;
       card_ne_balance   — карточка ≠ регистр (их решает владелец, шаг 4);
       balance_ne_moves  — регистр ≠ Σ движений;
       negative_balance  — отрицательный регистр.
     --all — все товары.

  2. Начальные остатки (шаги 2–3):
       python manage.py reconcile_warehouse_stock --init-opening [--apply]
     - товарам без StockBalance: StockBalance = карточка;
     - на разницу регистр − Σ движений: движение StockMove(source_kind="opening", document=NULL).
     Карточку и существующий регистр не меняет. Без --apply — только план.

  3. Решения владельцев (шаг 4):
       python manage.py reconcile_warehouse_stock --decisions decisions.csv [--apply]
     CSV с заголовком: product_id,qty[,comment]
       product_id — UUID товара (WarehouseProduct.id);
       qty        — верный фактический остаток на складе товара (>= 0, «.» или «,»).
     Применяется только проведёнными документами INVENTORY (один документ на склад),
     товары, у которых регистр уже равен qty, пропускаются. Без --apply — только план.

Каждая пара (склад, товар) в --init-opening и каждый документ в --decisions
пишутся в своей транзакции; повторный запуск ничего не дублирует.
"""
import csv
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.warehouse import models as m
from apps.warehouse import stock as stock_service
from apps.warehouse.models import q_qty


REPORT_COLUMNS = [
    "company", "warehouse", "product", "card_qty", "balance_qty", "moves_sum", "agent_qty",
    "card_updated_at", "last_move_at", "issues", "company_id", "warehouse_id", "product_id",
]

DECISIONS_COMMENT = "Сверка остатков: решение владельца"


class Command(BaseCommand):
    help = "Сверка складских остатков: отчёт, начальные остатки (opening), решения владельцев через INVENTORY."

    def add_arguments(self, parser):
        parser.add_argument("--report", action="store_true", help="Отчёт (режим по умолчанию).")
        parser.add_argument("--csv", dest="csv_path", default=None, help="Путь для CSV отчёта (иначе stdout).")
        parser.add_argument("--all", action="store_true", help="В отчёт — все товары, а не только проблемные.")
        parser.add_argument("--company", default=None, help="UUID компании (ограничить сверку).")
        parser.add_argument("--warehouse", default=None, help="UUID склада (ограничить сверку).")
        parser.add_argument("--init-opening", action="store_true", help="Шаги 2–3: регистр из карточки + opening-движения.")
        parser.add_argument("--decisions", default=None, help="Шаг 4: CSV product_id,qty[,comment].")
        parser.add_argument("--apply", action="store_true", help="Записать изменения (без него — только план).")

    # ------------------------------------------------------------------ report
    def _report(self, opts):
        rows = stock_service.stock_rows(company_id=opts["company"], warehouse_id=opts["warehouse"])
        out_file = None
        if opts["csv_path"]:
            out_file = open(opts["csv_path"], "w", newline="", encoding="utf-8")
            stream = out_file
        else:
            stream = self.stdout
        writer = csv.writer(stream)
        writer.writerow(REPORT_COLUMNS)
        counts = {"total": 0, "no_balance": 0, "card_ne_balance": 0, "balance_ne_moves": 0, "negative_balance": 0}
        written = 0
        try:
            for r in rows:
                counts["total"] += 1
                for issue in r["issues"]:
                    counts[issue] = counts.get(issue, 0) + 1
                if not r["issues"] and not opts["all"]:
                    continue
                writer.writerow([
                    r["company"], r["warehouse"], r["product"], r["card_qty"],
                    "" if r["balance_qty"] is None else r["balance_qty"],
                    r["moves_sum"], r["agent_qty"],
                    r["card_updated_at"].isoformat() if r["card_updated_at"] else "",
                    r["last_move_at"].isoformat() if r["last_move_at"] else "",
                    ";".join(r["issues"]), r["company_id"], r["warehouse_id"], r["product_id"],
                ])
                written += 1
        finally:
            if out_file is not None:
                out_file.close()

        foreign = list(stock_service.foreign_balance_rows(company_id=opts["company"]))
        summary = (
            f"Товаров со складом: {counts['total']}; строк в отчёте: {written}\n"
            f"  без StockBalance (остаток только в карточке): {counts['no_balance']}\n"
            f"  карточка ≠ регистр: {counts['card_ne_balance']}\n"
            f"  регистр ≠ Σ движений: {counts['balance_ne_moves']}\n"
            f"  отрицательный регистр: {counts['negative_balance']}\n"
            f"  регистр «чужого» склада ≠ Σ движений: {len(foreign)}\n"
        )
        # при CSV в stdout сводку пишем в stderr, чтобы не портить файл
        (self.stderr if not opts["csv_path"] else self.stdout).write(summary)

    # ------------------------------------------------------------ init opening
    def _init_opening(self, opts):
        apply = opts["apply"]
        plan_balances = 0
        plan_moves = 0
        done_balances = 0
        done_moves = 0
        for r in stock_service.stock_rows(company_id=opts["company"], warehouse_id=opts["warehouse"]):
            bal = r["balance_qty"]
            target = r["card_qty"] if bal is None else bal
            delta = q_qty(target - r["moves_sum"])
            need_balance = bal is None
            if not need_balance and delta == 0:
                continue
            plan_balances += int(need_balance)
            plan_moves += int(delta != 0)
            if not apply:
                if delta != 0 or need_balance:
                    self.stdout.write(
                        f"[план] {r['company']} / {r['warehouse']} / {r['product']} ({r['product_id']}): "
                        f"{'создать регистр = ' + str(target) + '; ' if need_balance else ''}"
                        f"opening {delta:+}"
                    )
                continue
            product = m.WarehouseProduct.objects.select_related("warehouse").get(pk=r["product_id"])
            res = stock_service.init_opening_for_pair(warehouse=product.warehouse, product=product)
            done_balances += int(res["created_balance"])
            done_moves += int(res["opening_delta"] != 0)

        # Регистр по «чужому» складу товара (в норме таких нет) — тоже выравниваем по Σ движений.
        for r in stock_service.foreign_balance_rows(company_id=opts["company"]):
            if opts["warehouse"] and str(r["warehouse_id"]) != str(opts["warehouse"]):
                continue
            delta = q_qty(Decimal(r["qty"] or 0) - Decimal(r["moves_sum"] or 0))
            plan_moves += 1
            if not apply:
                self.stdout.write(
                    f"[план] регистр склада {r['warehouse_id']} / товар {r['product_id']}: opening {delta:+}"
                )
                continue
            wh = m.Warehouse.objects.get(pk=r["warehouse_id"])
            product = m.WarehouseProduct.objects.get(pk=r["product_id"])
            res = stock_service.init_opening_for_pair(warehouse=wh, product=product)
            done_moves += int(res["opening_delta"] != 0)

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"Начальные остатки: создано регистров {done_balances}, opening-движений {done_moves}."
            ))
        else:
            self.stdout.write(
                f"План: создать регистров {plan_balances}, opening-движений {plan_moves}. "
                "Для записи добавьте --apply."
            )

    # --------------------------------------------------------------- decisions
    def _read_decisions(self, path):
        try:
            fh = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Не удалось открыть {path}: {exc}")
        decisions = OrderedDict()
        with fh:
            reader = csv.DictReader(fh)
            fields = {(f or "").strip() for f in (reader.fieldnames or [])}
            if not {"product_id", "qty"} <= fields:
                raise CommandError("CSV решений: нужен заголовок product_id,qty[,comment].")
            for n, row in enumerate(reader, start=2):
                row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                if not row.get("product_id"):
                    continue
                try:
                    qty = q_qty(Decimal(row["qty"].replace(",", ".")))
                except (InvalidOperation, ValueError):
                    raise CommandError(f"Строка {n}: qty «{row['qty']}» — не число.")
                if qty < 0:
                    raise CommandError(f"Строка {n}: qty не может быть отрицательным.")
                decisions[row["product_id"]] = (qty, row.get("comment") or "")
        return decisions

    def _decisions(self, opts):
        from apps.warehouse import services

        decisions = self._read_decisions(opts["decisions"])
        products = {
            str(p.pk): p
            for p in m.WarehouseProduct.objects.select_related("warehouse", "company").filter(pk__in=list(decisions))
        }
        missing = [pid for pid in decisions if pid not in products]
        if missing:
            raise CommandError(f"Товары не найдены: {', '.join(missing[:20])}")

        by_wh = OrderedDict()
        for pid, (qty, comment) in decisions.items():
            p = products[pid]
            if p.warehouse_id is None:
                raise CommandError(f"Товар {pid} не привязан к складу.")
            if opts["company"] and str(p.company_id) != str(opts["company"]):
                raise CommandError(f"Товар {pid} другой компании, чем --company.")
            if not p.is_weight and qty % 1 != 0:
                raise CommandError(f"Товар {pid} штучный: qty должен быть целым ({qty}).")
            cur = stock_service.get_on_hand(warehouse=p.warehouse, product=p)
            if cur == qty:
                self.stdout.write(f"[пропуск] {p.name} ({pid}): регистр уже {cur}")
                continue
            by_wh.setdefault(p.warehouse_id, []).append((p, qty, cur, comment))

        if not by_wh:
            self.stdout.write("Нечего применять: все остатки уже совпадают с решениями.")
            return

        for wh_id, lines in by_wh.items():
            wh = lines[0][0].warehouse
            for p, qty, cur, comment in lines:
                self.stdout.write(
                    f"{'[применить]' if opts['apply'] else '[план]'} {wh.company} / {wh.name} / {p.name} ({p.pk}): "
                    f"{cur} → {qty} ({q_qty(qty - cur):+}){' — ' + comment if comment else ''}"
                )
            if not opts["apply"]:
                continue
            with transaction.atomic():
                doc = m.Document.objects.create(
                    doc_type=m.Document.DocType.INVENTORY,
                    warehouse_from=wh,
                    comment=DECISIONS_COMMENT,
                )
                for p, qty, _cur, _comment in lines:
                    m.DocumentItem.objects.create(document=doc, product=p, qty=qty, price=Decimal("0.00"))
                services.post_document(doc, allow_negative=False, allow_duplicate=True)
                doc.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(f"  проведён {doc.number} ({len(lines)} строк)"))

        if not opts["apply"]:
            self.stdout.write("Для записи добавьте --apply.")

    # ------------------------------------------------------------------ handle
    def handle(self, *args, **opts):
        if opts["apply"] and not (opts["init_opening"] or opts["decisions"]):
            raise CommandError("--apply используется вместе с --init-opening или --decisions.")
        if not (opts["init_opening"] or opts["decisions"]):
            self._report(opts)
            return
        if opts["init_opening"]:
            self._init_opening(opts)
        if opts["decisions"]:
            self._decisions(opts)
