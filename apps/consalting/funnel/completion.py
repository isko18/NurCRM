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
    if sub.period == "month":
        count = horizon_months
    else:
        count = horizon_months if not getattr(sub, "autorenew", True) else 3
    last_p = sub.payments.order_by("-due_date").first()
    due = (last_p.due_date + step) if last_p else sub.start_date
    if isinstance(due, str):
        from datetime import datetime
        try:
            due = datetime.strptime(due.strip(), "%Y-%m-%d").date()
        except ValueError:
            from django.utils import timezone
            due = timezone.localdate()
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
                             subscription_period=None, subscription_prepaid_periods=None,
                             subscription_autorenew=True,
                             payment_method="cash", actor=None):
    """Единая точка вызова при создании продажи / согласовании оплаты (§5.3, §5.6)."""
    from decimal import Decimal
    from django.db import transaction
    from ..models import SubscriptionConsalting, SubscriptionPaymentConsalting, ServicesConsalting

    with transaction.atomic():
        tariff = sale.tariff
        amount = subscription_amount if subscription_amount is not None else (
            getattr(sale, "subscription_amount", None) or (tariff.subscription_amount if tariff else 0)
        )
        service = getattr(sale, "services", None) or getattr(sale, "service", None)
        if not service and sale.company:
            service = ServicesConsalting.objects.filter(company=sale.company).first()

        prepaid_N = 0
        if subscription_prepaid_periods is not None:
            try:
                prepaid_N = max(1, int(subscription_prepaid_periods))
            except (ValueError, TypeError):
                prepaid_N = 1
        elif not subscription_autorenew:
            try:
                prepaid_N = max(1, int(getattr(sale, "paid_months", 1) or 1))
            except (ValueError, TypeError):
                prepaid_N = 1

        if (prepaid_N > 1 or not subscription_autorenew) and (amount is None or float(amount) <= 0) and getattr(sale, "total", 0):
            count_for_div = max(1, prepaid_N)
            amount = (sale.total / count_for_div).quantize(Decimal("0.01"))

        if subscription_enabled and amount and float(amount) > 0 and sale.client and service:
            period = subscription_period or getattr(sale, "subscription_period", None) or (tariff.subscription_period if tariff else "month") or "month"
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
                    autorenew=subscription_autorenew,
                    created_by=sale.user,
                ),
            )
            if not created and sub.autorenew != subscription_autorenew:
                sub.autorenew = subscription_autorenew
                sub.save(update_fields=["autorenew"])

            # Идемпотентность (§5.6, пункт 5): повторный register-payment не оплачивает периоды повторно
            if not created and sub.payments.filter(status=SubscriptionPaymentConsalting.Status.PAID).exists():
                return sub

            # Сценарий C: Фиксированный график ровно на N периодов (§5.6)
            if not subscription_autorenew:
                count = max(1, prepaid_N)
                if created:
                    generate_schedule(sub, horizon_months=count)
                payments = list(sub.payments.order_by("due_date")[:count])
                now = timezone.now()
                for p in payments:
                    p.status = SubscriptionPaymentConsalting.Status.PAID
                    p.paid_at = now
                    p.paid_via = "lead_prepayment"
                    p.payment_method = payment_method or "cash"
                    p.save(update_fields=["status", "paid_at", "paid_via", "payment_method"])
                if payments:
                    sub.paid_through = payments[-1].due_date
                    sub.save(update_fields=["paid_through"])
                return sub

            # Сценарии A и B: Полноценная подписка на 12 месяцев
            if created:
                generate_schedule(sub, horizon_months=12)

            if prepaid_N >= 1:
                unpaid = sub.payments.filter(
                    status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
                ).order_by("due_date")
                if unpaid.count() < prepaid_N:
                    generate_schedule(sub, horizon_months=prepaid_N + 12)
                    unpaid = sub.payments.filter(
                        status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
                    ).order_by("due_date")

                to_pay = list(unpaid[:prepaid_N])
                if to_pay:
                    from .cash_confirmation import needs_confirmation
                    from ..models import CashRequestConsalting, CashOperationConsalting
                    author = actor or sale.user
                    pay_mode = payment_method or "cash"
                    prepay_total = sum(p.amount for p in to_pay)
                    p_first = to_pay[0].period_month
                    p_last = to_pay[-1].period_month
                    p_range = p_first if len(to_pay) == 1 else f"{p_first}..{p_last}"

                    already_done = CashRequestConsalting.objects.filter(
                        subscription=sub, period_month=p_range, status=CashRequestConsalting.Status.PENDING
                    ).exists() or CashOperationConsalting.objects.filter(
                        subscription=sub, comment__icontains=p_range
                    ).exists()

                    if not already_done:
                        if needs_confirmation(sale.company, pay_mode, author):
                            CashRequestConsalting.objects.create(
                                company=sale.company,
                                user=author,
                                client=sale.client,
                                sale=sale,
                                subscription=sub,
                                subscription_payment=to_pay[0],
                                kind=CashRequestConsalting.Kind.SUBSCRIPTION,
                                direction="income",
                                amount=prepay_total,
                                payment_method=pay_mode,
                                comment=f"Предоплата абонентской платы ({p_range}) при оплате лида",
                                period_month=p_range,
                                prepaid_count=len(to_pay),
                                status=CashRequestConsalting.Status.PENDING,
                            )
                        else:
                            now = timezone.now()
                            for p in to_pay:
                                p.status = SubscriptionPaymentConsalting.Status.PAID
                                p.paid_at = now
                                p.paid_via = "lead_prepayment"
                                p.payment_method = pay_mode
                                p.save(update_fields=["status", "paid_at", "paid_via", "payment_method"])
                            sub.paid_through = to_pay[-1].due_date
                            sub.save(update_fields=["paid_through"])
                            CashOperationConsalting.objects.create(
                                company=sale.company,
                                user=author,
                                sale=sale,
                                subscription=sub,
                                kind=CashOperationConsalting.Kind.SUBSCRIPTION,
                                direction=CashOperationConsalting.Direction.INCOME,
                                amount=prepay_total,
                                payment_method=pay_mode,
                                comment=f"Предоплата абонентской платы ({p_range}) при оплате лида",
                            )
                            if sub.client and subscription_autorenew:
                                from .tenant_lifecycle import extend_tenant_subscription
                                extend_tenant_subscription(client=sub.client, subscription_payment=to_pay[-1], actor=author)

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
