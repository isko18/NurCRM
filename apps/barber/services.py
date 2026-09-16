import logging
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from .models import (
    Appointment, Service, ServiceSalaryRate, MasterSalaryAccrual, MasterSalaryPayout
)

logger = logging.getLogger(__name__)


class BarberSalaryService:
    """
    Сервис для расчета и начисления зарплаты мастерам (барберам).
    Реализует логику начислений при завершении записи и списаний при отмене.
    """

    @classmethod
    def handle_appointment_status_change(cls, appointment: Appointment, old_status: str, new_status: str):
        """
        Отслеживает изменение статуса записи на предмет начисления/корректировки зарплаты мастера.
        Вызывается при изменении статуса записи (сохранение/обновление).
        """
        if old_status == new_status:
            return

        if new_status == Appointment.Status.COMPLETED:
            # Запись завершена -> Начисляем проценты по услугам
            cls.accrue_salary(appointment)
        elif old_status == Appointment.Status.COMPLETED:
            # Статус completed снят (откатили или отменили запись) -> Списываем/корректируем
            cls.revert_salary(appointment)

    @classmethod
    def accrue_salary(cls, appointment: Appointment):
        """
        Создает начисления для мастера по каждой услуге из записи.
        """
        if not appointment.price or appointment.price <= 0:
            logger.info(f"Запись {appointment.id} завершена, но цена <= 0. Начисления не создаются.")
            return

        # Получаем все услуги в записи
        appointment_services = list(appointment.appointment_services.all().select_related("service"))
        if not appointment_services:
            logger.info(f"Запись {appointment.id} завершена, но список услуг пуст.")
            return

        # Фильтруем услуги, у которых есть ставка
        valid_services = []
        for aps in appointment_services:
            service = aps.service
            if not service:
                continue
            
            # Получаем ставку (по умолчанию 0.00)
            rate, _ = ServiceSalaryRate.objects.get_or_create(
                company_id=appointment.company_id,
                service=service
            )
            if rate.percent > 0:
                valid_services.append((service, rate.percent))

        if not valid_services:
            logger.info(f"Запись {appointment.id} завершена, но нет услуг с ненулевой процентной ставкой.")
            return

        # Распределяем appointment.price пропорционально базовым ценам услуг
        total_base_price = sum(s.price for s, _ in valid_services)
        n = len(valid_services)
        shares = []

        if total_base_price == 0:
            # Если у всех услуг базовая цена 0, делим поровну
            for service, percent in valid_services:
                shares.append((service, percent, Decimal("1.0") / n))
        else:
            for service, percent in valid_services:
                shares.append((service, percent, service.price / total_base_price))

        # Вычисляем предварительные суммы базы начисления (service_amount)
        accruals_data = []
        total_allocated = Decimal("0.00")
        max_share_idx = 0
        max_share = Decimal("-1.0")

        for idx, (service, percent, share) in enumerate(shares):
            amount_share = (appointment.price * share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            accruals_data.append({
                "service": service,
                "percent": percent,
                "service_amount": amount_share
            })
            total_allocated += amount_share
            if share > max_share:
                max_share = share
                max_share_idx = idx

        # Корректируем копеечную погрешность округления на услугу с наибольшей долей
        diff = appointment.price - total_allocated
        if diff != 0:
            accruals_data[max_share_idx]["service_amount"] += diff

        # Группируем начисления по услуге для сохранения уникальности (appointment, service)
        grouped_accruals = {}
        for item in accruals_data:
            svc_id = item["service"].id
            if svc_id not in grouped_accruals:
                grouped_accruals[svc_id] = {
                    "service": item["service"],
                    "percent": item["percent"],
                    "service_amount": Decimal("0.00"),
                }
            grouped_accruals[svc_id]["service_amount"] += item["service_amount"]

        # Создаем начисления в БД
        with transaction.atomic():
            for item in grouped_accruals.values():
                service = item["service"]
                percent = item["percent"]
                service_amount = item["service_amount"]
                amount = (service_amount * percent / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Не создаем начисление, если сумма = 0
                if amount <= 0:
                    continue

                MasterSalaryAccrual.objects.create(
                    company_id=appointment.company_id,
                    master=appointment.barber,
                    appointment=appointment,
                    service=service,
                    service_amount=service_amount,
                    percent=percent,
                    amount=amount,
                    status=MasterSalaryAccrual.Status.ACCRUED
                )
                logger.info(f"Создано начисление {amount} мастеру {appointment.barber.id} по услуге {service.id}")

    @classmethod
    def revert_salary(cls, appointment: Appointment):
        """
        Отменяет начисления при переводе записи из статуса completed.
        """
        active_accruals = MasterSalaryAccrual.objects.filter(
            appointment=appointment
        ).exclude(status=MasterSalaryAccrual.Status.CANCELED)

        with transaction.atomic():
            for acc in active_accruals:
                if acc.status == MasterSalaryAccrual.Status.ACCRUED:
                    # Если еще не выплачено — просто отменяем
                    acc.status = MasterSalaryAccrual.Status.CANCELED
                    acc.save(update_fields=["status", "updated_at"])
                    logger.info(f"Начисление {acc.id} отменено (CANCELED)")
                elif acc.status == MasterSalaryAccrual.Status.PAID:
                    # Если уже выплачено — создаем корректирующее начисление с отрицательной суммой
                    MasterSalaryAccrual.objects.create(
                        company_id=acc.company_id,
                        master=acc.master,
                        appointment=appointment,
                        service=acc.service,
                        service_amount=acc.service_amount,
                        percent=acc.percent,
                        amount=-acc.amount,
                        status=MasterSalaryAccrual.Status.ACCRUED
                    )
                    logger.info(f"Создано корректирующее начисление {-acc.amount} для выплаченного начисления {acc.id}")
