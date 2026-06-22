"""Сайд-эффекты завершения лида (переход на системную стадию `completed`).

Создаёт продажу-аналитику consalting (`SaleConsalting`) из лида и фиксирует
параметры абонентки из тарифа. Идемпотентно: повторный вызов не дублирует продажу.

ВНИМАНИЕ: автоматическое начисление ЗАРПЛАТЫ здесь НЕ выполняется — в секторе
консалтинга нет формализованных правил расчёта (процент/база). Когда правила
будут заданы, начисление добавляется здесь же (по owner + participants).
"""
import logging
from datetime import date
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger("nurcrm.consalting.completion")

# сколько неоплаченных периодов держим «вперёд» (скользящее окно)
SUBSCRIPTION_WINDOW = 12


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


RU_MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def _add_months(d: date, n: int) -> date:
    """Прибавляет n месяцев к дате, выравнивая на 1-е число периода."""
    total = (d.year * 12 + (d.month - 1)) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, 1)


def ensure_subscription_deal(sale, window: int = SUBSCRIPTION_WINDOW):
    """Гарантирует наличие сделки-«подложки» (ClientDeal/DEBT) с помесячными взносами
    для абонентской продажи и поддерживает скользящее окно: всегда ~window периодов
    вперёд от текущего месяца. Создаётся лениво (при запросе расписания).

    Возвращает ClientDeal или None (если у продажи нет клиента/абонентки).
    """
    from apps.main.models import ClientDeal, DealInstallment
    from ..models import SaleConsalting

    amount = sale.subscription_amount or Decimal("0")
    if not sale.client_id or amount <= 0:
        return None

    step = 12 if (sale.subscription_period == "year") else 1
    start_dt = sale.subscription_started_at or sale.created_at
    start = timezone.localtime(start_dt).date().replace(day=1)

    deal = sale.subscription_deal
    if deal is None:
        service_name = sale.services.name if sale.services_id else "услуга"
        deal = ClientDeal.objects.create(
            company_id=sale.company_id,
            branch_id=sale.branch_id,
            client_id=sale.client_id,
            title=f"Абонентская плата: {service_name}"[:255],
            kind=ClientDeal.Kind.DEBT,
            amount=amount,
            prepayment=Decimal("0"),
            debt_days=30,            # формальный срок (требуется моделью для DEBT)
            auto_schedule=False,     # график ведём вручную (помесячно), не лумпом
            note="Помесячная абонентская плата (consalting).",
        )
        # привязываем без повторного full_clean пересчёта продажи
        SaleConsalting.objects.filter(pk=sale.pk).update(subscription_deal=deal)
        sale.subscription_deal = deal

    # сколько периодов нужно: от старта до (max(старт, текущий месяц) + window шагов)
    today = timezone.localdate().replace(day=1)
    horizon = _add_months(max(start, today), window * step)

    periods = []
    d = start
    while d <= horizon:
        periods.append(d)
        d = _add_months(d, step)

    existing = list(deal.installments.order_by("number"))
    if len(existing) < len(periods):
        new = [
            DealInstallment(
                company_id=deal.company_id,
                branch_id=deal.branch_id,
                deal=deal,
                number=idx + 1,
                due_date=periods[idx],
                amount=amount,
                balance_after=Decimal("0.00"),
            )
            for idx in range(len(existing), len(periods))
        ]
        DealInstallment.objects.bulk_create(new)

    return deal


def build_subscription_schedule(client, window: int = SUBSCRIPTION_WINDOW):
    """Строит график абонентских платежей клиента из его consalting-продаж.

    Каждый период подкреплён реальной сделкой/взносом, поэтому несёт `deal` и
    `installment_id` для оплаты через /api/main/clients/{cid}/deals/{did}/pay/,
    а `paid`/`status` отражают фактическую оплату взноса.
    """
    from ..models import SaleConsalting

    sales = (
        SaleConsalting.objects
        .filter(client=client, subscription_amount__gt=0)
        .select_related("services")
        .order_by("subscription_started_at")
    )

    today = timezone.localdate()
    items = []
    for sale in sales:
        deal = ensure_subscription_deal(sale, window=window)
        if deal is None:
            continue
        for inst in deal.installments.order_by("number"):
            d = inst.due_date
            paid = (inst.paid_amount or Decimal("0")) >= inst.amount
            items.append({
                "period": f"{d.year:04d}-{d.month:02d}",
                "period_label": f"{RU_MONTHS[d.month - 1]} {d.year}",
                "amount": str(inst.amount),
                "status": "paid" if paid else "planned",
                "paid": paid,
                "active": (d.year == today.year and d.month == today.month),
                "deal": str(deal.id),
                "installment_id": str(inst.id),
            })
    return items
