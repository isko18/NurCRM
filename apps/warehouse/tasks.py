"""Фоновые задачи склада."""
import logging
from html import escape

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

# Сколько примеров нарушений писать в лог/уведомление.
_SAMPLE_LIMIT = 20


@shared_task(name="apps.warehouse.tasks.check_stock_consistency")
def check_stock_consistency():
    """
    Ежедневный контроль остатков (§5.8 stock-single-source-of-truth):
    - карточка ≠ регистр для своего склада;
    - регистр ≠ Σ движений;
    - отрицательный регистр при ALLOW_NEGATIVE_STOCK=False.
    Ничего не исправляет: пишет в лог и (если настроен) кратко в Telegram-группу команды.
    Товары без регистра (остаток только в карточке) считаются отдельно — это
    данные до сверки, они инициализируются при первой операции или командой
    reconcile_warehouse_stock --init-opening.
    """
    from apps.warehouse import stock as stock_service

    allow_negative = bool(getattr(settings, "ALLOW_NEGATIVE_STOCK", False))
    counts = {"card_ne_balance": 0, "balance_ne_moves": 0, "negative_balance": 0, "no_balance": 0}
    samples = []
    for r in stock_service.stock_rows():
        issues = [i for i in r["issues"] if not (i == "negative_balance" and allow_negative)]
        if not issues:
            continue
        for i in issues:
            counts[i] = counts.get(i, 0) + 1
        real = [i for i in issues if i != "no_balance"]
        if real and len(samples) < _SAMPLE_LIMIT:
            samples.append((r, real))
    foreign = sum(1 for _ in stock_service.foreign_balance_rows())
    counts["foreign_balance_ne_moves"] = foreign

    violations = counts["card_ne_balance"] + counts["balance_ne_moves"] + counts["negative_balance"] + foreign
    if violations:
        logger.warning("check_stock_consistency: нарушения остатков %s", counts)
        for r, issues in samples:
            logger.warning(
                "check_stock_consistency: %s | %s | %s (%s): card=%s balance=%s moves=%s issues=%s",
                r["company"], r["warehouse"], r["product"], r["product_id"],
                r["card_qty"], r["balance_qty"], r["moves_sum"], ",".join(issues),
            )
        try:
            from apps.support.bot import send_team_alert

            lines = [
                "<b>Склад: нарушения остатков</b>",
                f"карточка ≠ регистр: {counts['card_ne_balance']}",
                f"регистр ≠ Σ движений: {counts['balance_ne_moves'] + foreign}",
                f"отрицательный регистр: {counts['negative_balance']}",
            ]
            for r, issues in samples[:5]:
                lines.append(
                    f"• {escape(str(r['company']))} / {escape(str(r['product']))}: "
                    f"карточка {r['card_qty']}, регистр {r['balance_qty']}, движения {r['moves_sum']}"
                )
            lines.append("Подробно: manage.py reconcile_warehouse_stock --report")
            send_team_alert("\n".join(lines))
        except Exception:  # noqa: BLE001 — уведомление не должно ронять задачу
            logger.exception("check_stock_consistency: не удалось отправить уведомление")
    else:
        logger.info("check_stock_consistency: нарушений нет %s", counts)
    return counts
