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
    """Атомарное исполнение сайд-эффектов при завершении лида (выигрыш/WON):
    1. Поиск/создание клиента при его отсутствии.
    2. Создание продажи SaleConsalting (идемпотентно).
    3. Авто-начисление зарплаты по ставке услуги (ServiceSalaryRateConsalting).
    4. Рассылка реалтайм уведомлений владельцу и обновления доски.
    """
    from django.db import transaction
    from apps.main.models import Client
    from ..models import SaleConsalting, ServiceSalaryRateConsalting, SalaryAccrualConsalting
    from . import realtime

    try:
        with transaction.atomic():
            # 1. Поиск или создание клиента
            client = lead.client
            if not client and (lead.full_name or lead.phone or lead.email):
                client = Client.objects.filter(
                    company_id=lead.company_id,
                    phone=lead.phone
                ).first() if lead.phone else None

                if not client:
                    client = Client.objects.create(
                        company_id=lead.company_id,
                        branch_id=lead.branch_id,
                        full_name=lead.full_name or lead.title or "Клиент из лида",
                        phone=lead.phone or "",
                        email=lead.email or "",
                        salesperson=lead.owner or actor,
                    )
                lead.client = client
                lead.save(update_fields=["client", "updated_at"])

            # 2. Создание продажи SaleConsalting (идемпотентно)
            existing_sale = SaleConsalting.objects.filter(lead=lead).first()
            if not existing_sale:
                tariff = lead.tariff
                sub_amount = tariff.subscription_amount if tariff else 0
                sub_period = (tariff.subscription_period if tariff else "") or ""
                sub_started = timezone.now() if (sub_amount or 0) > 0 else None

                sale = SaleConsalting.objects.create(
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

                from .cash_confirmation import needs_confirmation
                from ..models import CashRequestConsalting, CashOperationConsalting

                payment_mode = getattr(lead, "payment_mode", None) or "cash"
                author = lead.owner or actor
                if needs_confirmation(lead.company, payment_mode, author):
                    sale.status = SaleConsalting.Status.PENDING_CONFIRMATION
                    sale.save(update_fields=["status"])
                    CashRequestConsalting.objects.create(
                        company=lead.company,
                        sale=sale,
                        user=author,
                        client=lead.client,
                        kind=CashRequestConsalting.Kind.SALE,
                        direction="income",
                        amount=sale.total,
                        payment_method=payment_mode,
                        status=CashRequestConsalting.Status.PENDING,
                    )
                else:
                    sale.status = SaleConsalting.Status.COMPLETED
                    sale.save(update_fields=["status"])
                    CashOperationConsalting.objects.create(
                        company=lead.company,
                        user=author,
                        kind=CashOperationConsalting.Kind.SALE,
                        direction=CashOperationConsalting.Direction.INCOME,
                        amount=sale.total,
                        payment_method=payment_mode,
                        comment=f"Продажа из лида: {lead.title}",
                    )
            else:
                sale = existing_sale

            # 3. Авто-создание абонентской подписки и графика платежей (§5.3)
            create_sale_side_effects(sale)

            # 4. Авто-начисление зарплаты продавцу по ставке услуги
            seller = lead.owner or actor
            accrue_salary_for_sale(sale, seller=seller)

            # 5. Реалтайм-уведомления
            realtime.lead_moved(lead)
            if lead.owner_id:
                realtime.notify_user(
                    lead.owner_id,
                    "lead.won",
                    realtime.serialize_lead(lead)
                )

            return sale
    except Exception as e:
        logger.exception("completion side-effects failed for lead %s: %s", lead.id, e)
        return None


def generate_schedule(sub, horizon_months=12):
    """Плановые платежи вперёд на горизонт (§5.3). Продлевается ежедневной задачей."""
    from dateutil.relativedelta import relativedelta
    from ..models import SubscriptionPaymentConsalting

    step = relativedelta(months=1) if sub.period == "month" else relativedelta(years=1)
    count = horizon_months if sub.period == "month" else 3  # для года — 3 периода
    due = sub.start_date
    rows = []
    for _ in range(count):
        month_str = due.strftime("%Y-%m")
        if not SubscriptionPaymentConsalting.objects.filter(subscription=sub, period_month=month_str).exists():
            rows.append(SubscriptionPaymentConsalting(
                subscription=sub,
                due_date=due,
                period_month=month_str,
                amount=sub.amount,
                status=SubscriptionPaymentConsalting.Status.PLANNED,
            ))
        due += step
    if rows:
        SubscriptionPaymentConsalting.objects.bulk_create(rows, ignore_conflicts=True)


def create_sale_side_effects(sale, *, subscription_enabled=True,
                             subscription_start=None, subscription_amount=None,
                             subscription_period=None):
    """Единая точка вызова при создании продажи / согласовании оплаты (§5.3)."""
    from django.db import transaction
    from ..models import SubscriptionConsalting

    with transaction.atomic():
        tariff = sale.tariff
        amount = subscription_amount if subscription_amount is not None else (
            tariff.subscription_amount if tariff else 0
        )
        service = getattr(sale, "services", None) or getattr(sale, "service", None)
        if subscription_enabled and amount and float(amount) > 0 and sale.client and service:
            period = subscription_period or (tariff.subscription_period if tariff else "month")
            start = subscription_start or timezone.localdate()
            if isinstance(start, str):
                from datetime import datetime
                try:
                    start = datetime.strptime(start, "%Y-%m-%d").date()
                except ValueError:
                    start = timezone.localdate()

            sub, created = SubscriptionConsalting.objects.get_or_create(
                sale=sale, service=service,
                defaults=dict(
                    company=sale.company,
                    client=sale.client,
                    tariff=tariff,
                    lead=sale.lead,
                    amount=amount,
                    period=period,
                    start_date=start,
                    created_by=sale.user,
                ),
            )
            if created:
                generate_schedule(sub, horizon_months=12)
            return sub
        return None


def resolve_rate(user, service):
    """
    Приоритет ставок:
    1. сотрудник + услуга (SalarySchemeServiceOverrideConsalting)
    2. сотрудник (SalarySchemeConsalting)
    3. услуга (ServiceSalaryRateConsalting)
    4. компания (SalaryDefaultsConsalting)
    """
    from ..models import (
        SalarySchemeConsalting, SalarySchemeServiceOverrideConsalting,
        ServiceSalaryRateConsalting, SalaryDefaultsConsalting
    )

    scheme = SalarySchemeConsalting.objects.filter(user=user, company_id=user.company_id).first()
    if scheme:
        if service:
            override = SalarySchemeServiceOverrideConsalting.objects.filter(scheme=scheme, service=service).first()
            if override and (override.percent > 0 or override.fixed_amount > 0):
                return override.percent, override.fixed_amount
        if scheme.percent_enabled or scheme.fixed_enabled:
            pct = scheme.percent if scheme.percent_enabled else Decimal("0")
            fix = scheme.fixed_amount if scheme.fixed_enabled else Decimal("0")
            return pct, fix

    if service:
        rate = ServiceSalaryRateConsalting.objects.filter(company_id=user.company_id, service=service).first()
        if rate and (rate.percent > 0 or rate.fixed_amount > 0):
            return rate.percent, rate.fixed_amount

    defaults = SalaryDefaultsConsalting.objects.filter(company_id=user.company_id).first()
    if defaults:
        return defaults.percent, defaults.fixed_amount

    return Decimal("0"), Decimal("0")


def accrue_salary_for_sale(sale, seller=None):
    """Автоматическое начисление зарплаты продавцу при создании/закрытии продажи."""
    from ..models import SalaryAccrualConsalting
    from . import realtime

    if not sale:
        return None

    seller = seller or sale.user
    if not seller:
        return None

    try:
        percent, fixed = resolve_rate(seller, sale.services)
        base_amt = sale.total or Decimal("0.00")
        last_accrual = None

        if percent and percent > 0:
            accrual_amt = (base_amt * percent / Decimal("100")).quantize(Decimal("0.01"))
            if accrual_amt > 0:
                accrual, created = SalaryAccrualConsalting.objects.get_or_create(
                    sale=sale,
                    kind=SalaryAccrualConsalting.Kind.PERCENT,
                    defaults={
                        "company_id": sale.company_id,
                        "user": seller,
                        "service": sale.services,
                        "lead": sale.lead,
                        "base_amount": base_amt,
                        "percent": percent,
                        "amount": accrual_amt,
                        "status": SalaryAccrualConsalting.Status.ACCRUED,
                    }
                )
                last_accrual = accrual
                if created:
                    realtime.notify_user(
                        seller.id,
                        "consulting.salary.accrued",
                        {
                            "title": f"Начислена зарплата (%): {accrual_amt}",
                            "message": f"Продажа: {sale.description or (sale.services.name if sale.services else 'Услуга')}",
                            "amount": str(accrual_amt),
                            "sale_id": str(sale.id),
                        }
                    )

        if fixed and fixed > 0:
            accrual, created = SalaryAccrualConsalting.objects.get_or_create(
                sale=sale,
                kind=SalaryAccrualConsalting.Kind.FIXED,
                defaults={
                    "company_id": sale.company_id,
                    "user": seller,
                    "service": sale.services,
                    "lead": sale.lead,
                    "base_amount": base_amt,
                    "percent": Decimal("0"),
                    "amount": fixed,
                    "status": SalaryAccrualConsalting.Status.ACCRUED,
                }
            )
            last_accrual = accrual
            if created:
                realtime.notify_user(
                    seller.id,
                    "consulting.salary.accrued",
                    {
                        "title": f"Начислено фикс за сделку: {fixed}",
                        "message": f"Продажа: {sale.description or (sale.services.name if sale.services else 'Услуга')}",
                        "amount": str(fixed),
                        "sale_id": str(sale.id),
                    }
                )

        return last_accrual
    except Exception as e:
        logger.warning("accrue_salary_for_sale failed for sale %s: %s", sale.id, e)
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
