"""Сайд-эффекты завершения лида (переход на системную стадию `completed`).

Создаёт продажу-аналитику consalting (`SaleConsalting`) из лида и фиксирует
параметры абонентки из тарифа. Идемпотентно: повторный вызов не дублирует продажу.

ВНИМАНИЕ: автоматическое начисление ЗАРПЛАТЫ здесь НЕ выполняется — в секторе
консалтинга нет формализованных правил расчёта (процент/база). Когда правила
будут заданы, начисление добавляется здесь же (по owner + participants).
"""
import logging

from django.utils import timezone

logger = logging.getLogger("nurcrm.consalting.completion")


def apply_completion_side_effects(lead, actor=None):
    """Создаёт (идемпотентно) продажу-аналитику из завершённого лида.

    Возвращает SaleConsalting (новую или существующую) либо None при ошибке.
    """
    from ..models import SaleConsalting

    try:
        existing = SaleConsalting.objects.filter(lead=lead).first()
        if existing:
            return existing

        tariff = lead.tariff
        sub_amount = tariff.subscription_amount if tariff else 0
        sub_period = (tariff.subscription_period if tariff else "") or ""
        sub_started = timezone.now() if (sub_amount or 0) > 0 else None

        return SaleConsalting.objects.create(
            company_id=lead.company_id,
            branch_id=lead.branch_id,
            user=lead.owner or actor,
            services=lead.service,
            tariff=tariff,
            client=lead.client,
            lead=lead,
            total=lead.estimated_value or 0,
            description=f"Завершение лида: {lead.title}",
            subscription_amount=sub_amount or 0,
            subscription_period=sub_period if (sub_amount or 0) > 0 else "",
            subscription_started_at=sub_started,
        )
    except Exception as e:  # сайд-эффект не должен ломать переход стадии
        logger.exception("completion side-effects failed for lead %s: %s", lead.id, e)
        return None


def build_subscription_schedule(client, months_ahead=12):
    """Строит график абонентских платежей клиента из его consalting-продаж.

    Возвращает список элементов {period, period_label, amount, status, paid, active}.
    Платежи считаются «planned» (факт оплат абонентки отдельно не трекается).
    """
    from ..models import SaleConsalting

    RU_MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                 "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

    sales = SaleConsalting.objects.filter(
        client=client, subscription_amount__gt=0
    ).order_by("subscription_started_at")

    today = timezone.localdate()
    items = []
    for sale in sales:
        start = (sale.subscription_started_at or sale.created_at).date()
        period = sale.subscription_period or "month"
        amount = str(sale.subscription_amount)

        for i in range(months_ahead):
            if period == "year":
                y, m = start.year + i, start.month
            else:
                total_m = (start.month - 1) + i
                y, m = start.year + total_m // 12, total_m % 12 + 1
            key = f"{y:04d}-{m:02d}"
            active = (y == today.year and m == today.month)
            items.append({
                "period": key,
                "period_label": f"{RU_MONTHS[m - 1]} {y}",
                "amount": amount,
                "status": "planned",
                "paid": False,
                "active": active,
            })
    return items
