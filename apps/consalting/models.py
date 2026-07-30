from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.users.models import Company, User, Branch
import uuid


# ======== База ========
class TimeStampedModel(models.Model):
    """Abstract model that provides created/updated timestamps."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        abstract = True


# ======== Услуги ========
class ServicesConsalting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_services',
        related_query_name='consalting_service',
        verbose_name='Компания'
    )
    # услуга может быть глобальной (NULL) или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_services',
        related_query_name='consalting_service',
        verbose_name='Филиал',
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена")
    installation_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Стоимость установки"
    )
    description = models.TextField(verbose_name="Описание", blank=True)
    # Привязка услуги к кастомной роли: NULL = «общая» услуга (видна во всех
    # воронках); конкретная роль = услуга показывается только в воронке этой роли.
    # При удалении роли услуга не удаляется, а становится общей (SET NULL).
    custom_role = models.ForeignKey(
        'users.CustomRole',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_services',
        related_query_name='consalting_service',
        verbose_name='Роль',
    )

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        ordering = ['name']
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]
        # уникальность названия в рамках ветки/компании, как в барбере
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_consalting_service_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_consalting_service_global_per_company',
                condition=models.Q(branch__isnull=True),
            ),
        ]

    def __str__(self):
        return self.name or str(self.id)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Тариф услуги ========
class TariffConsalting(TimeStampedModel):
    """Тариф (вариант) услуги — у услуги может быть несколько тарифов на выбор."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_tariffs',
        related_query_name='consalting_tariff',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_tariffs',
        related_query_name='consalting_tariff',
        verbose_name='Филиал',
    )
    service = models.ForeignKey(
        ServicesConsalting,
        on_delete=models.CASCADE,
        related_name='tariffs',
        related_query_name='tariff',
        verbose_name='Услуга'
    )
    name = models.CharField(max_length=255, verbose_name='Название тарифа')
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Цена тарифа')
    # абонентская плата (опционально)
    subscription_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Абонентская плата'
    )
    subscription_period = models.CharField(
        max_length=8, blank=True,
        choices=[('month', 'Месяц'), ('year', 'Год')],
        verbose_name='Период абонентки'
    )

    class Meta:
        verbose_name = 'Тариф услуги'
        verbose_name_plural = 'Тарифы услуг'
        ordering = ['service', 'price']
        indexes = [
            models.Index(fields=['company', 'service']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('service', 'name'),
                name='uniq_consalting_tariff_per_service',
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.price}"

    def clean(self):
        if self.service_id:
            if self.company_id and self.service.company_id != self.company_id:
                raise ValidationError({'service': 'Услуга принадлежит другой компании.'})
            if self.service.branch_id not in (None, self.branch_id):
                raise ValidationError({'service': 'Услуга относится к другому филиалу.'})
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Продажа услуги ========
class SaleConsalting(TimeStampedModel):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Проведена"
        PENDING_CONFIRMATION = "pending_confirmation", "Ждёт подтверждения"
        CANCELED = "canceled", "Отменена"
        REFUNDED = "refunded", "Частичный возврат"

    class CancelReason(models.TextChoices):
        CLIENT_REFUSED = "client_refused", "Клиент отказался"
        INPUT_ERROR = "input_error", "Ошибка оформления"
        WARRANTY = "warranty", "Возврат по гарантии"
        DUPLICATE = "duplicate", "Дубль"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_sales',
        related_query_name='consalting_sale',
        verbose_name='Компания'
    )
    # продажа может быть глобальной или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_sales',
        related_query_name='consalting_sale',
        verbose_name='Филиал',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consalting_sales',
        related_query_name='consalting_sale',
        verbose_name='Пользователь'
    )
    services = models.ForeignKey(
        ServicesConsalting,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="sales",
        related_query_name="sale",
        verbose_name="Услуга"
    )
    tariff = models.ForeignKey(
        TariffConsalting,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="sales",
        related_query_name="sale",
        verbose_name="Тариф"
    )
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="consalting_sales",
        related_query_name="consalting_sale",
        verbose_name="Клиент"
    )
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Скидка (сумма)"
    )
    markup = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Наценка сверху"
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name="Итого", help_text="Считается автоматически"
    )
    # связь с лидом (для продаж, созданных при завершении лида) + абонентка
    lead = models.ForeignKey(
        "LeadConsalting", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sales", related_query_name="sale", verbose_name="Лид"
    )
    subscription_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Абонентская плата"
    )
    subscription_period = models.CharField(
        max_length=8, blank=True,
        choices=[('month', 'Месяц'), ('year', 'Год')],
        verbose_name="Период абонентки"
    )
    subscription_started_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Старт абонентки"
    )
    # Сделка-«подложка» для оплаты абонентки: ClientDeal(kind=DEBT) с помесячными
    # взносами (DealInstallment). Создаётся лениво при запросе расписания; через неё
    # работает существующий /api/main/clients/{cid}/deals/{did}/pay/.
    subscription_deal = models.ForeignKey(
        "main.ClientDeal",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="consalting_subscription_sales",
        related_query_name="consalting_subscription_sale",
        verbose_name="Сделка абонентки",
    )
    description = models.TextField(verbose_name="Заметка", blank=True)

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.COMPLETED, db_index=True
    )
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="canceled_consalting_sales"
    )
    cancel_reason = models.CharField(max_length=32, choices=CancelReason.choices, blank=True)
    cancel_comment = models.TextField(blank=True)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Продажа услуги"
        verbose_name_plural = "Продажи услуг"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
            models.Index(fields=['company', 'user']),
        ]

    def __str__(self):
        service_name = self.services.name if self.services else "(без услуги)"
        return f"{service_name} — {self.company}"


class SaleRefundConsalting(TimeStampedModel):
    """Частичный возврат: продажа остаётся, часть суммы возвращается (§8.2)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="consalting_sale_refunds")
    sale = models.ForeignKey(SaleConsalting, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=32, choices=SaleConsalting.CancelReason.choices)
    comment = models.TextField(blank=True)
    refund_mode = models.CharField(max_length=16, default="cash")  # cash | transfer | none
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Частичный возврат продажи"
        verbose_name_plural = "Частичные возвраты продаж"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund {self.sale_id}: {self.amount} ({self.reason})"

    # ----- расчёт итоговой суммы -----
    def base_price(self):
        """Цена тарифа, если выбран; иначе базовая цена услуги (с учетом ролевого переопределения)."""
        seller_role = getattr(self.user, 'custom_role', None) if self.user else None
        if self.tariff_id:
            if seller_role:
                rp = self.tariff.role_prices.filter(custom_role=seller_role).first()
                if rp:
                    return rp.price
            return self.tariff.price or Decimal("0.00")
        if self.services_id:
            if seller_role:
                rp = self.services.role_prices.filter(custom_role=seller_role).first()
                if rp:
                    return rp.price
            return self.services.price or Decimal("0.00")
        return Decimal("0.00")

    def installation_price(self):
        return Decimal("0.00")

    def items_total(self):
        return sum((i.price or 0) for i in self.items.all())

    def compute_total(self):
        """Итого = цена по роли + доп. товары − скидка + наценка."""
        return (
            self.base_price()
            + self.items_total()
            - (self.discount or 0)
            + (self.markup or 0)
        )

    def recalc_total(self, save=True):
        self.total = self.compute_total()
        if save:
            SaleConsalting.objects.filter(pk=self.pk).update(total=self.total)
        return self.total

    def clean(self):
        # company согласованность
        if self.company_id:
            if self.user and getattr(self.user, 'company_id', None) not in (None, self.company_id):
                raise ValidationError({'user': 'Пользователь из другой компании.'})
            if self.services and self.services.company_id != self.company_id:
                raise ValidationError({'services': 'Услуга принадлежит другой компании.'})
            if self.tariff and self.tariff.company_id != self.company_id:
                raise ValidationError({'tariff': 'Тариф принадлежит другой компании.'})
            if self.client and getattr(self.client, 'company_id', None) != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})

        # тариф должен принадлежать выбранной услуге
        if self.tariff_id and self.services_id and self.tariff.service_id != self.services_id:
            raise ValidationError({'tariff': 'Тариф относится к другой услуге.'})

        # branch согласованность (если задан)
        if self.branch_id:
            if self.services and self.services.branch_id not in (None, self.branch_id):
                raise ValidationError({'services': 'Услуга другого филиала.'})
            if self.client and getattr(self.client, 'branch_id', None) not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


# ======== Доп. товар в продаже ========
class SaleItemConsalting(TimeStampedModel):
    """Произвольный доп. товар/позиция в продаже (название + цена)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(
        SaleConsalting,
        on_delete=models.CASCADE,
        related_name='items',
        related_query_name='item',
        verbose_name='Продажа'
    )
    name = models.CharField(max_length=255, verbose_name='Название')
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Цена')

    class Meta:
        verbose_name = 'Доп. товар продажи'
        verbose_name_plural = 'Доп. товары продаж'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['sale']),
        ]

    def __str__(self):
        return f"{self.name} — {self.price}"


# ======== Зарплата/выплата ========
class SalaryConsalting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_salaries',
        related_query_name='consalting_salary',
        verbose_name='Компания'
    )
    # выплата может быть глобальной или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_salaries',
        related_query_name='consalting_salary',
        verbose_name='Филиал',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_salaries',
        related_query_name='consalting_salary',
        verbose_name='Пользователь'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма")
    percent = models.CharField(max_length=255, verbose_name="Процент")
    description = models.TextField(verbose_name="Описание", blank=True)

    class Meta:
        verbose_name = "Зарплата / Выплата"
        verbose_name_plural = "Зарплаты / Выплаты"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'user']),
            models.Index(fields=['company', 'branch', 'user']),
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        return f"{self.company} — {self.amount}"

    def clean(self):
        if self.company_id:
            if self.user and getattr(self.user, 'company_id', None) not in (None, self.company_id):
                raise ValidationError({'user': 'Пользователь из другой компании.'})
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Заявки ========
class RequestsConsalting(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        IN_WORK = 'in_work', 'В работе'
        DONE = 'done', 'Завершена'
        CANCELED = 'canceled', 'Отменена'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_requests',
        related_query_name='consalting_request',
        verbose_name='Компания'
    )
    # заявка может быть глобальной или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_requests',
        related_query_name='consalting_request',
        verbose_name='Филиал',
    )
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="consalting_requests",
        related_query_name="consalting_request",
        verbose_name="Клиент"
    )
    status = models.CharField(max_length=20, choices=Status.choices, verbose_name='Статус', default=Status.NEW)
    name = models.CharField(max_length=255, verbose_name="Заявка")
    description = models.TextField(verbose_name="Описание", blank=True)

    class Meta:
        verbose_name = "Заявка на консультацию"
        verbose_name_plural = "Заявки на консультацию"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
            models.Index(fields=['company', 'name']),
        ]

    def __str__(self):
        return f"{self.name} — {self.get_status_display()}"

    def clean(self):
        if self.company_id:
            if self.client and getattr(self.client, 'company_id', None) != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})
        if self.branch_id:
            if self.branch.company_id != self.company_id:
                raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
            if self.client and getattr(self.client, 'branch_id', None) not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


# ======== Бронирование ========
class BookingConsalting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_bookings',
        related_query_name='consalting_booking',
        verbose_name='Компания'
    )
    # бронь может быть глобальной или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_bookings',
        related_query_name='consalting_booking',
        verbose_name='Филиал',
    )
    title = models.CharField(max_length=255, verbose_name='Название')
    date = models.DateField(verbose_name='Дата')
    time = models.TimeField(verbose_name='Время')
    employee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_bookings',
        related_query_name='consalting_booking',
        verbose_name='Сотрудник'
    )
    note = models.TextField(blank=True, verbose_name='Заметка')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = "Бронирование"
        verbose_name_plural = "Бронирования"
        ordering = ['-date', 'time']
        indexes = [
            models.Index(fields=['company', 'date', 'time']),
            models.Index(fields=['company', 'branch', 'date', 'time']),
            models.Index(fields=['company', 'employee']),
        ]
        # запретим двойную бронь слота для одного сотрудника
        constraints = [
            models.UniqueConstraint(
                fields=('company', 'branch', 'date', 'time', 'employee'),
                name='uniq_consalting_booking_slot_per_employee',
            ),
        ]

    def __str__(self):
        return f"{self.title} — {self.date} {self.time}"

    def clean(self):
        if self.company_id:
            if self.employee and getattr(self.employee, 'company_id', None) not in (None, self.company_id):
                raise ValidationError({'employee': 'Сотрудник из другой компании.'})
        if self.branch_id:
            if self.branch.company_id != self.company_id:
                raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Воронка продаж ========
class FunnelConsalting(TimeStampedModel):
    """Воронка продаж (набор стадий, по которым движутся лиды)."""

    class FunnelKind(models.TextChoices):
        MAIN = 'main', 'Основная'
        ROLE = 'role', 'Роль'
        CUSTOM = 'custom', 'Пользовательская'

    class NextAssign(models.TextChoices):
        KEEP = "keep", "Оставить текущего ответственного"
        POOL = "pool", "Вернуть в общий пул"
        AUTO = "auto", "Распределить автоматически"
        USER = "user", "Назначить конкретному сотруднику"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_funnels',
        related_query_name='consalting_funnel',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_funnels',
        related_query_name='consalting_funnel',
        verbose_name='Филиал',
    )
    name = models.CharField(max_length=255, verbose_name='Название воронки')
    description = models.TextField(blank=True, verbose_name='Описание')
    is_active = models.BooleanField(default=True, verbose_name='Активна')

    # ----- тип воронки и защита -----
    funnel_kind = models.CharField(
        max_length=16, choices=FunnelKind.choices, default=FunnelKind.CUSTOM,
        db_index=True, verbose_name='Тип воронки'
    )
    is_main = models.BooleanField(default=False, verbose_name='Основная')
    is_static = models.BooleanField(
        default=False, verbose_name='Статичная',
        help_text='True для основной и ролевых воронок (нельзя удалять/переименовывать).'
    )
    custom_role = models.ForeignKey(
        'users.CustomRole',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_funnels',
        verbose_name='Роль (для воронки роли)'
    )

    # ----- иерархия и автопереход по цепочке -----
    next_funnel = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="prev_funnels", verbose_name="Следующая воронка"
    )
    next_stage = models.ForeignKey(
        "FunnelStageConsalting", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="Следующая стадия"
    )
    next_assign = models.CharField(
        max_length=8, choices=NextAssign.choices, default=NextAssign.KEEP, verbose_name="Кому назначить"
    )
    next_assign_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="Конкретный ответственный"
    )
    is_final = models.BooleanField(
        default=True, verbose_name="Финальная воронка (оформляет продажу)"
    )
    stage_sla_hours = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="SLA воронки (часов)"
    )

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'branch', 'is_active']),
            models.Index(fields=['company', 'funnel_kind']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_consalting_funnel_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_consalting_funnel_global_per_company',
                condition=models.Q(branch__isnull=True),
            ),
            models.UniqueConstraint(
                fields=('company', 'custom_role'),
                name='uniq_consalting_funnel_per_role_per_company',
                condition=models.Q(custom_role__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company',),
                name='uniq_consalting_main_funnel_per_company',
                condition=models.Q(is_main=True),
            ),
        ]

    def __str__(self):
        return self.name or str(self.id)

    @property
    def is_protected(self) -> bool:
        """Основную и ролевые воронки нельзя удалять/переименовывать."""
        return bool(self.is_main or self.custom_role_id or self.is_static)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.custom_role_id and self.custom_role.company_id not in (None, self.company_id):
            raise ValidationError({'custom_role': 'Роль принадлежит другой компании.'})

        if self.next_funnel_id:
            if self.next_funnel_id == self.id:
                raise ValidationError({'next_funnel': 'Воронка не может быть следующей для самой себя.'})
            visited = {self.id}
            curr = self.next_funnel
            while curr:
                if curr.id in visited:
                    raise ValidationError({'next_funnel': 'Цепочка воронок зациклена.'})
                visited.add(curr.id)
                curr = curr.next_funnel

        if self.next_stage_id and self.next_funnel_id:
            if self.next_stage.funnel_id != self.next_funnel_id:
                raise ValidationError({'next_stage': 'Следующая стадия должна принадлежать следующей воронке.'})

        if self.next_assign == self.NextAssign.USER and not self.next_assign_user_id:
            raise ValidationError({'next_assign_user': 'Укажите сотрудника для назначения.'})

    def save(self, *args, **kwargs):
        if not self.next_funnel_id:
            self.is_final = True
        super().save(*args, **kwargs)


class LeadFunnelHistoryConsalting(TimeStampedModel):
    """Путь лида: где был, кто вёл, сколько времени."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(
        "LeadConsalting", on_delete=models.CASCADE,
        related_name="funnel_history", verbose_name="Лид"
    )
    funnel = models.ForeignKey(
        FunnelConsalting, on_delete=models.CASCADE, verbose_name="Воронка"
    )
    stage = models.ForeignKey(
        "FunnelStageConsalting", null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Стадия"
    )
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Ответственный"
    )
    entered_at = models.DateTimeField(default=timezone.now, verbose_name="Время входа")
    left_at = models.DateTimeField(null=True, blank=True, verbose_name="Время выхода")
    transition = models.CharField(max_length=16, default="auto", verbose_name="Тип перехода")  # auto | manual | initial

    class Meta:
        verbose_name = "История прохождения воронки"
        verbose_name_plural = "История прохождения воронок"
        ordering = ["entered_at"]


# ======== Стадия воронки ========
class FunnelStageConsalting(TimeStampedModel):
    """Стадия (этап) воронки продаж."""

    class StageType(models.TextChoices):
        NEW_LEAD = 'new_lead', 'Новый лид'
        FIRST_CONTACT = 'first_contact', 'Первый контакт'
        QUALIFICATION = 'qualification', 'Квалификация'
        NURTURE = 'nurture', 'Прогрев / в работе'
        PROPOSAL_SENT = 'proposal_sent', 'КП отправлено'
        NEGOTIATION = 'negotiation', 'Переговоры'
        DECISION_PENDING = 'decision_pending', 'Ожидание решения'
        WON = 'won', 'Оплачено / выиграно'
        ONBOARDING = 'onboarding', 'Онбординг'
        COMPLETED = 'completed', 'Завершено'
        LOST = 'lost', 'Потеряно'

    # стадии, считающиеся «закрытием» (терминальными)
    TERMINAL_TYPES = {StageType.WON, StageType.COMPLETED, StageType.LOST}
    SUCCESS_TYPES = {StageType.WON, StageType.COMPLETED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_funnel_stages',
        related_query_name='consalting_funnel_stage',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_funnel_stages',
        related_query_name='consalting_funnel_stage',
        verbose_name='Филиал',
    )
    funnel = models.ForeignKey(
        FunnelConsalting,
        on_delete=models.CASCADE,
        related_name='stages',
        related_query_name='stage',
        verbose_name='Воронка'
    )
    name = models.CharField(max_length=255, verbose_name='Название стадии')
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок')
    color = models.CharField(
        max_length=7,
        default='#3498db',
        verbose_name='Цвет',
        help_text='HEX-цвет (например, #3498db)'
    )
    # системные (неизменяемые) стадии воронки роли: intake / in_progress / completed
    is_system = models.BooleanField(default=False, verbose_name='Системная стадия')
    system_key = models.CharField(
        max_length=32, blank=True, db_index=True,
        choices=[('intake', 'intake'), ('in_progress', 'in_progress'), ('completed', 'completed')],
        verbose_name='Системный ключ'
    )
    # Семантический тип стадии — на нём строятся переходы и аналитика
    stage_type = models.CharField(
        max_length=20, choices=StageType.choices, default=StageType.NEW_LEAD,
        db_index=True, verbose_name='Тип стадии'
    )
    # переопределение матрицы переходов на уровне воронки (список stage_type).
    # пусто → берётся каноничная матрица из funnel/state_machine.py
    allowed_next = models.JSONField(default=list, blank=True, verbose_name='Разрешённые переходы')
    # поля, которые обязаны быть заполнены перед уходом со стадии
    required_fields = models.JSONField(default=list, blank=True, verbose_name='Обязательные поля')
    # порог (часов) бездействия/нахождения в стадии для пометки «at risk»
    sla_hours = models.PositiveIntegerField(null=True, blank=True, verbose_name='SLA (часов)')
    allow_skip = models.BooleanField(default=False, verbose_name='Разрешить пропуск стадий')

    # is_final / is_success — выводятся из stage_type (оставлены для совместимости)
    is_final = models.BooleanField(
        default=False,
        verbose_name='Финальная стадия',
        help_text='Выводится из типа стадии. Стадия закрытия лида (успех или провал)'
    )
    is_success = models.BooleanField(
        default=False,
        verbose_name='Успешная стадия',
        help_text='Выводится из типа стадии. Стадия успешного закрытия лида'
    )

    class Meta:
        verbose_name = 'Стадия воронки'
        verbose_name_plural = 'Стадии воронки'
        ordering = ['funnel', 'order']
        indexes = [
            models.Index(fields=['company', 'funnel', 'order']),
            models.Index(fields=['funnel', 'stage_type']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('funnel', 'order'),
                name='uniq_consalting_stage_order_per_funnel',
            ),
        ]

    def __str__(self):
        return f"{self.service.name} — {self.name}"


class ServiceRolePriceConsalting(TimeStampedModel):
    """Переопределение цены услуги для конкретной роли."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        ServicesConsalting, on_delete=models.CASCADE,
        related_name="role_prices", verbose_name="Услуга"
    )
    custom_role = models.ForeignKey(
        'users.CustomRole', on_delete=models.CASCADE, verbose_name="Кастомная роль"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Переопределённая цена")

    class Meta:
        verbose_name = "Цена услуги по роли"
        verbose_name_plural = "Цены услуг по ролям"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "custom_role"],
                name="uniq_consalting_service_role_price"
            )
        ]

    def __str__(self):
        return f"{self.service.name} ({self.custom_role}): {self.price}"


class TariffRolePriceConsalting(TimeStampedModel):
    """Переопределение цены тарифа для конкретной роли."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tariff = models.ForeignKey(
        TariffConsalting, on_delete=models.CASCADE,
        related_name="role_prices", verbose_name="Тариф"
    )
    custom_role = models.ForeignKey(
        'users.CustomRole', on_delete=models.CASCADE, verbose_name="Кастомная роль"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Переопределённая цена")

    class Meta:
        verbose_name = "Цена тарифа по роли"
        verbose_name_plural = "Цены тарифов по ролям"
        constraints = [
            models.UniqueConstraint(
                fields=["tariff", "custom_role"],
                name="uniq_consalting_tariff_role_price"
            )
        ]

    def __str__(self):
        return f"{self.tariff.name} ({self.custom_role}): {self.price}"


    def save(self, *args, **kwargs):
        # синхронизируем устаревшие флаги с семантическим типом
        self.is_final = self.stage_type in self.TERMINAL_TYPES
        self.is_success = self.stage_type in self.SUCCESS_TYPES
        super().save(*args, **kwargs)

    def clean(self):
        if self.funnel_id:
            if self.company_id and self.funnel.company_id != self.company_id:
                raise ValidationError({'funnel': 'Воронка принадлежит другой компании.'})
            if self.funnel.branch_id not in (None, self.branch_id):
                raise ValidationError({'funnel': 'Воронка относится к другому филиалу.'})
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Лид (карточка) ========
class LeadConsalting(TimeStampedModel):
    """Лид — карточка потенциального клиента, движется по стадиям воронки."""
    class Status(models.TextChoices):
        NEW = 'new', 'Новый'
        IN_WORK = 'in_work', 'В работе'
        WON = 'won', 'Успешно закрыт'
        LOST = 'lost', 'Потерян'

    class Grade(models.TextChoices):
        A = 'A', 'Горячий'
        B = 'B', 'Тёплый'
        C = 'C', 'Холодный'

    class Urgency(models.TextChoices):
        LOW = 'low', 'Низкая'
        MEDIUM = 'medium', 'Средняя'
        HIGH = 'high', 'Высокая'

    class NextAction(models.TextChoices):
        CALL = 'call', 'Звонок'
        MESSAGE = 'message', 'Сообщение'
        MEETING = 'meeting', 'Встреча'
        FOLLOW_UP = 'follow_up', 'Follow-up'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_leads',
        related_query_name='consalting_lead',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True,
        related_name='consalting_leads',
        related_query_name='consalting_lead',
        verbose_name='Филиал',
    )
    funnel = models.ForeignKey(
        FunnelConsalting,
        on_delete=models.CASCADE,
        related_name='leads',
        related_query_name='lead',
        verbose_name='Воронка'
    )
    stage = models.ForeignKey(
        FunnelStageConsalting,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='leads',
        related_query_name='lead',
        verbose_name='Текущая стадия'
    )
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_leads',
        related_query_name='consalting_lead',
        verbose_name='Клиент'
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='consalting_leads',
        related_query_name='consalting_lead',
        verbose_name='Ответственный'
    )

    title = models.CharField(max_length=255, verbose_name='Название лида')
    description = models.TextField(blank=True, verbose_name='Описание')

    # Контактные данные карточки (если ещё нет привязанного клиента)
    full_name = models.CharField(max_length=255, blank=True, verbose_name='Контактное лицо')
    phone = models.CharField(max_length=32, blank=True, db_index=True, verbose_name='Телефон')
    email = models.EmailField(blank=True, verbose_name='Email')

    source = models.CharField(max_length=100, blank=True, verbose_name='Источник')
    estimated_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Оценочная стоимость'
    )
    probability = models.PositiveIntegerField(default=0, verbose_name='Вероятность закрытия (%)')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NEW, verbose_name='Статус'
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата закрытия')

    # ----- Скоринг -----
    score_grade = models.CharField(
        max_length=1, choices=Grade.choices, default=Grade.C, db_index=True, verbose_name='Грейд'
    )
    score_value = models.PositiveIntegerField(default=0, verbose_name='Скоринг (0–100)')
    score_updated_at = models.DateTimeField(null=True, blank=True, verbose_name='Скоринг обновлён')
    budget_confirmed = models.BooleanField(default=False, verbose_name='Бюджет подтверждён')
    urgency = models.CharField(
        max_length=10, choices=Urgency.choices, default=Urgency.LOW, verbose_name='Срочность'
    )
    decision_maker_engaged = models.BooleanField(default=False, verbose_name='ЛПР вовлечён')
    avg_response_minutes = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Среднее время ответа (мин)'
    )

    # ----- Следующее действие (обязательно в активных стадиях) -----
    next_action_type = models.CharField(
        max_length=12, choices=NextAction.choices, null=True, blank=True, verbose_name='Тип след. действия'
    )
    next_action_date = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name='Дата след. действия'
    )
    next_action_note = models.CharField(max_length=500, blank=True, verbose_name='Заметка к действию')

    # ----- Риск / тайминги -----
    is_at_risk = models.BooleanField(default=False, db_index=True, verbose_name='Под риском')
    risk_reason = models.CharField(max_length=255, blank=True, verbose_name='Причина риска')
    last_activity_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name='Последняя активность'
    )
    stage_entered_at = models.DateTimeField(null=True, blank=True, verbose_name='Вход в текущую стадию')

    # ----- Проигрыш -----
    loss_reason = models.ForeignKey(
        'LossReasonConsalting', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads', verbose_name='Причина проигрыша'
    )
    loss_comment = models.TextField(blank=True, verbose_name='Комментарий к проигрышу')

    # ----- Передача лида между воронками -----
    source_lead = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='derived_leads', related_query_name='derived_lead',
        verbose_name='Лид-источник (откуда передан)'
    )

    # ----- Услуга/тариф и участники -----
    service = models.ForeignKey(
        ServicesConsalting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads', related_query_name='lead', verbose_name='Услуга'
    )
    tariff = models.ForeignKey(
        TariffConsalting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads', related_query_name='lead', verbose_name='Тариф'
    )
    participants = models.ManyToManyField(
        User, blank=True,
        related_name='consalting_participating_leads',
        verbose_name='Участники лида'
    )

    # ----- Архив -----
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name='В архиве')
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата архивации')

    # ----- Оплата (фиксация факта; финансовая сделка — в main) -----
    payment_registered = models.BooleanField(default=False, verbose_name='Оплата оформлена')
    payment_mode = models.CharField(
        max_length=16, blank=True, verbose_name='Способ оплаты',
        help_text='cash | transfer | debt | installment'
    )
    # Сделка из register-payment (для атрибуции «факта оплаты» в аналитике/дашборде).
    payment_deal = models.ForeignKey(
        "main.ClientDeal",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="consalting_payment_leads",
        related_query_name="consalting_payment_lead",
        verbose_name="Сделка оплаты",
    )

    # ----- Lifecycle -----
    first_contact_at = models.DateTimeField(null=True, blank=True, verbose_name='Первый контакт')
    won_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата выигрыша')
    lost_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата проигрыша')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата завершения')

    class Meta:
        verbose_name = 'Лид'
        verbose_name_plural = 'Лиды'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'funnel', 'stage']),
            models.Index(fields=['company', 'branch', 'status']),
            models.Index(fields=['company', 'owner']),
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'score_grade', 'next_action_date']),
            models.Index(fields=['company', 'owner', 'next_action_date']),
            models.Index(fields=['company', 'is_at_risk']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def clean(self):
        # company-согласованность
        if self.company_id:
            if self.funnel and self.funnel.company_id != self.company_id:
                raise ValidationError({'funnel': 'Воронка принадлежит другой компании.'})
            if self.stage and self.stage.company_id != self.company_id:
                raise ValidationError({'stage': 'Стадия принадлежит другой компании.'})
            if self.client and getattr(self.client, 'company_id', None) != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})
            if self.owner and getattr(self.owner, 'company_id', None) not in (None, self.company_id):
                raise ValidationError({'owner': 'Ответственный из другой компании.'})

        # стадия должна принадлежать выбранной воронке
        if self.stage_id and self.funnel_id and self.stage.funnel_id != self.funnel_id:
            raise ValidationError({'stage': 'Стадия относится к другой воронке.'})

        # branch-согласованность
        if self.branch_id:
            if self.company_id and self.branch.company_id != self.company_id:
                raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
            if self.funnel and self.funnel.branch_id not in (None, self.branch_id):
                raise ValidationError({'funnel': 'Воронка относится к другому филиалу.'})
            if self.client and getattr(self.client, 'branch_id', None) not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


# ======== Причина проигрыша (справочник) ========
class LossReasonConsalting(TimeStampedModel):
    """Структурированная причина проигрыша сделки (на уровне компании)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_loss_reasons',
        related_query_name='consalting_loss_reason',
        verbose_name='Компания'
    )
    code = models.SlugField(max_length=50, verbose_name='Код')
    label = models.CharField(max_length=255, verbose_name='Название')
    is_active = models.BooleanField(default=True, verbose_name='Активна')

    class Meta:
        verbose_name = 'Причина проигрыша'
        verbose_name_plural = 'Причины проигрыша'
        ordering = ['label']
        constraints = [
            models.UniqueConstraint(fields=('company', 'code'), name='uniq_consalting_loss_reason_code'),
        ]

    def __str__(self):
        return self.label


# ======== Лента активностей (audit trail, append-only) ========
class LeadActivityConsalting(TimeStampedModel):
    """Неизменяемая лента событий лида. Создаётся только через ActivityLogger."""
    class Type(models.TextChoices):
        NOTE = 'note', 'Заметка'
        CALL = 'call', 'Звонок'
        MESSAGE = 'message', 'Сообщение'
        EMAIL = 'email', 'Email'
        MEETING = 'meeting', 'Встреча'
        FILE = 'file', 'Файл'
        STAGE_CHANGE = 'stage_change', 'Смена стадии'
        SCORE_CHANGE = 'score_change', 'Смена скоринга'
        TASK = 'task', 'Задача'
        AUTOMATION = 'automation', 'Автоматизация'
        SYSTEM = 'system', 'Система'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='consalting_lead_activities',
        related_query_name='consalting_lead_activity', verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, null=True, blank=True, db_index=True,
        related_name='consalting_lead_activities',
        related_query_name='consalting_lead_activity', verbose_name='Филиал'
    )
    lead = models.ForeignKey(
        LeadConsalting, on_delete=models.CASCADE,
        related_name='activities', related_query_name='activity', verbose_name='Лид'
    )
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consalting_lead_activities', verbose_name='Автор'
    )
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Тип')
    title = models.CharField(max_length=255, verbose_name='Заголовок')
    body = models.TextField(blank=True, verbose_name='Текст')
    payload = models.JSONField(default=dict, blank=True, verbose_name='Данные')
    file = models.FileField(upload_to='consalting/lead_activities/', null=True, blank=True, verbose_name='Файл')

    class Meta:
        verbose_name = 'Активность лида'
        verbose_name_plural = 'Активности лидов'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'lead', 'created_at']),
            models.Index(fields=['company', 'type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.get_type_display()}: {self.title}"


# ======== Лог переходов по стадиям (для аналитики) ========
class StageTransitionConsalting(TimeStampedModel):
    """Запись перехода лида между стадиями — основа аналитики времени/конверсии."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='consalting_stage_transitions',
        related_query_name='consalting_stage_transition', verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, null=True, blank=True, db_index=True,
        related_name='consalting_stage_transitions',
        related_query_name='consalting_stage_transition', verbose_name='Филиал'
    )
    lead = models.ForeignKey(
        LeadConsalting, on_delete=models.CASCADE,
        related_name='transitions', related_query_name='transition', verbose_name='Лид'
    )
    from_stage = models.ForeignKey(
        FunnelStageConsalting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Из стадии'
    )
    to_stage = models.ForeignKey(
        FunnelStageConsalting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='В стадию'
    )
    from_type = models.CharField(max_length=20, blank=True, verbose_name='Тип (из)')
    to_type = models.CharField(max_length=20, blank=True, verbose_name='Тип (в)')
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consalting_stage_transitions', verbose_name='Автор'
    )
    automated = models.BooleanField(default=False, verbose_name='Автоматический')
    seconds_in_prev = models.PositiveBigIntegerField(
        null=True, blank=True, verbose_name='Секунд в прошлой стадии'
    )

    class Meta:
        verbose_name = 'Переход по стадии'
        verbose_name_plural = 'Переходы по стадиям'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'lead', 'created_at']),
            models.Index(fields=['company', 'to_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.from_type or '—'} → {self.to_type}"


# ======== Задача по лиду (follow-up) ========
class LeadTaskConsalting(TimeStampedModel):
    """Задача/напоминание по лиду. Питает next_action и автоматизацию."""
    class Status(models.TextChoices):
        OPEN = 'open', 'Открыта'
        DONE = 'done', 'Выполнена'
        OVERDUE = 'overdue', 'Просрочена'
        CANCELED = 'canceled', 'Отменена'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='consalting_lead_tasks',
        related_query_name='consalting_lead_task', verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, null=True, blank=True, db_index=True,
        related_name='consalting_lead_tasks',
        related_query_name='consalting_lead_task', verbose_name='Филиал'
    )
    lead = models.ForeignKey(
        LeadConsalting, on_delete=models.CASCADE,
        related_name='tasks', related_query_name='task', verbose_name='Лид'
    )
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consalting_lead_tasks', verbose_name='Исполнитель'
    )
    type = models.CharField(
        max_length=12, choices=LeadConsalting.NextAction.choices, verbose_name='Тип'
    )
    title = models.CharField(max_length=255, verbose_name='Название')
    due_date = models.DateTimeField(db_index=True, verbose_name='Срок')
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True, verbose_name='Статус'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consalting_created_lead_tasks', verbose_name='Создал'
    )
    created_by_automation = models.BooleanField(default=False, verbose_name='Создано автоматикой')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Выполнена в')

    class Meta:
        verbose_name = 'Задача по лиду'
        verbose_name_plural = 'Задачи по лидам'
        ordering = ['due_date']
        indexes = [
            models.Index(fields=['company', 'status', 'due_date']),
            models.Index(fields=['company', 'assignee', 'status']),
            models.Index(fields=['lead', 'status']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


# ======== Правило автоматизации ========
class AutomationRuleConsalting(TimeStampedModel):
    """Декларативное правило автоматизации воронки."""
    class Trigger(models.TextChoices):
        STAGE_CHANGED = 'stage_changed', 'Смена стадии'
        ACTIVITY_ADDED = 'activity_added', 'Добавлена активность'
        NO_ACTIVITY = 'no_activity', 'Нет активности'
        PROPOSAL_OPENED = 'proposal_opened', 'КП открыто'
        TASK_OVERDUE = 'task_overdue', 'Задача просрочена'
        LEAD_WON = 'lead_won', 'Лид выигран'
        LEAD_LOST = 'lead_lost', 'Лид проигран'
        SLA_BREACH = 'sla_breach', 'Нарушение SLA'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='consalting_automation_rules',
        related_query_name='consalting_automation_rule', verbose_name='Компания'
    )
    funnel = models.ForeignKey(
        FunnelConsalting, on_delete=models.CASCADE, null=True, blank=True,
        related_name='automation_rules', verbose_name='Воронка'
    )
    name = models.CharField(max_length=255, verbose_name='Название')
    trigger = models.CharField(max_length=20, choices=Trigger.choices, db_index=True, verbose_name='Триггер')
    conditions = models.JSONField(default=dict, blank=True, verbose_name='Условия')
    actions = models.JSONField(default=list, blank=True, verbose_name='Действия')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='Активно')
    priority = models.IntegerField(default=100, verbose_name='Приоритет')

    class Meta:
        verbose_name = 'Правило автоматизации'
        verbose_name_plural = 'Правила автоматизации'
        ordering = ['priority', 'name']
        indexes = [
            models.Index(fields=['company', 'trigger', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_trigger_display()}]"


# ======== Лог автоматизации ========
class AutomationLogConsalting(TimeStampedModel):
    """Аудит срабатываний автоматизации (+ идемпотентность через dedup_key)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='consalting_automation_logs',
        related_query_name='consalting_automation_log', verbose_name='Компания'
    )
    rule = models.ForeignKey(
        AutomationRuleConsalting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='logs', verbose_name='Правило'
    )
    lead = models.ForeignKey(
        LeadConsalting, on_delete=models.CASCADE,
        related_name='automation_logs', verbose_name='Лид'
    )
    trigger = models.CharField(max_length=20, verbose_name='Триггер')
    matched = models.BooleanField(default=False, verbose_name='Условие выполнено')
    actions_result = models.JSONField(default=list, blank=True, verbose_name='Результат действий')
    dedup_key = models.CharField(max_length=255, db_index=True, blank=True, verbose_name='Ключ дедупликации')

    class Meta:
        verbose_name = 'Лог автоматизации'
        verbose_name_plural = 'Логи автоматизации'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'lead', 'created_at']),
            models.Index(fields=['dedup_key']),
        ]

    def __str__(self):
        return f"{self.trigger} · {self.lead_id}"


# ======== Доступ сотрудника к воронке ========
class EmployeeFunnelGrant(TimeStampedModel):
    """Доп. доступ сотрудника к воронке (просмотр + опционально управление лидами).

    Воронка роли сотрудника сюда НЕ дублируется — доступ к ней определяется
    связкой custom_role + флагами can_view_funnel / can_manage_funnel_leads.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='funnel_grants',
        related_query_name='funnel_grant',
        verbose_name='Сотрудник'
    )
    funnel = models.ForeignKey(
        FunnelConsalting,
        on_delete=models.CASCADE,
        related_name='grants',
        related_query_name='grant',
        verbose_name='Воронка'
    )
    can_manage_leads = models.BooleanField(
        default=False, verbose_name='Может управлять лидами в этой воронке'
    )
    can_manage_stages = models.BooleanField(
        default=False, verbose_name='Может управлять стадиями в этой воронке'
    )

    class Meta:
        verbose_name = 'Доступ к воронке'
        verbose_name_plural = 'Доступы к воронкам'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=('employee', 'funnel'),
                name='uniq_consalting_funnel_grant_per_employee',
            ),
        ]
        indexes = [
            models.Index(fields=['employee']),
            models.Index(fields=['funnel']),
        ]

    def __str__(self):
        return f"{self.employee_id} → {self.funnel_id} (manage={self.can_manage_leads})"


# ======== Пользовательские предпочтения по воронкам ========
class FunnelUserPreferenceConsalting(TimeStampedModel):
    """Персональные настройки пользователя для страницы воронок.

    Сейчас хранит только порядок воронок-строк (drag-and-drop), который
    раньше жил в localStorage. Порядок — список id воронок (как строки);
    воронки, отсутствующие в списке, фронт/сервер добавляет в конец, а
    исчезнувшие — игнорируются при отдаче.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='consalting_funnel_preference',
        verbose_name='Пользователь',
    )
    funnel_order = models.JSONField(
        default=list, blank=True,
        verbose_name='Порядок воронок',
        help_text='Упорядоченный список id воронок.',
    )

    class Meta:
        verbose_name = 'Предпочтения по воронкам'
        verbose_name_plural = 'Предпочтения по воронкам'

    def __str__(self):
        return f"prefs:{self.user_id}"


# ======== WhatsApp сообщения консалтинга (каркас) ========
class WhatsAppMessageConsalting(TimeStampedModel):
    """Модель для хранения сообщений WhatsApp, привязанных к лиду консалтинга."""
    class Direction(models.TextChoices):
        INBOUND = 'inbound', 'Входящее'
        OUTBOUND = 'outbound', 'Исходящее'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает отправки'
        SENT = 'sent', 'Отправлено'
        DELIVERED = 'delivered', 'Доставлено'
        READ = 'read', 'Прочитано'
        FAILED = 'failed', 'Ошибка отправки'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_whatsapp_messages',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='consalting_whatsapp_messages',
        verbose_name='Филиал'
    )
    lead = models.ForeignKey(
        LeadConsalting,
        on_delete=models.CASCADE,
        related_name='whatsapp_messages',
        verbose_name='Лид'
    )
    message_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='ID сообщения WhatsApp'
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        verbose_name='Направление'
    )
    text = models.TextField(verbose_name='Текст сообщения')
    content_uri = models.TextField(
        null=True,
        blank=True,
        verbose_name='URL медиа-файла'
    )
    media_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name='Тип медиа'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name='Статус'
    )

    class Meta:
        verbose_name = 'WhatsApp сообщение консалтинга'
        verbose_name_plural = 'WhatsApp сообщения консалтинга'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['company', 'lead', 'created_at']),
            models.Index(fields=['company', 'direction', 'status']),
        ]

    def __str__(self):
        return f"{self.direction} - {self.message_id} ({self.status})"


# ======== Wazzup аккаунт консалтинга ========
class WazzupAccountConsalting(TimeStampedModel):
    """Аккаунт Wazzup для интеграции воронки консалтинга с WhatsApp, Instagram, Telegram."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting_wazzup_accounts',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='consalting_wazzup_accounts',
        verbose_name='Филиал'
    )
    api_key = models.CharField(
        max_length=255,
        verbose_name='API Ключ Wazzup',
        help_text='Ключ API из личного кабинета Wazzup'
    )
    api_url = models.URLField(
        default='https://api.wazzup24.com',
        verbose_name='API URL'
    )
    channel_id = models.CharField(
        max_length=255,
        verbose_name='Channel ID (ID Канала)',
        help_text='ID канала WhatsApp/Instagram из Wazzup'
    )
    INTEGRATION_TYPES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('telegram', 'Telegram'),
    ]
    integration_type = models.CharField(
        max_length=20,
        choices=INTEGRATION_TYPES,
        default='whatsapp',
        verbose_name='Тип интеграции'
    )
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_connected = models.BooleanField(default=False, verbose_name='Подключен')

    class Meta:
        verbose_name = 'Wazzup аккаунт консалтинга'
        verbose_name_plural = 'Wazzup аккаунты консалтинга'
        ordering = ['-created_at']

    def __str__(self):
        return f"Wazzup Consalting ({self.get_integration_type_display()}): {self.channel_id}"



# ======== Зарплатная система консалтинга (ставки, схемы, авто-начисления, премии, штрафы, выплаты) ========

class SalarySchemeConsalting(TimeStampedModel):
    """Схема оплаты конкретного сотрудника. Части складываются."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_salary_schemes", verbose_name="Компания"
    )
    user = models.OneToOneField(
        User, on_delete=models.CASCADE,
        related_name="salary_scheme", verbose_name="Сотрудник"
    )
    base_salary_enabled = models.BooleanField(default=False, verbose_name="Оклад включён")
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма оклада")
    base_salary_period = models.CharField(max_length=8, default="month", verbose_name="Период оклада")

    percent_enabled = models.BooleanField(default=False, verbose_name="Процент включён")
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Процент со сделок (%)")

    fixed_enabled = models.BooleanField(default=False, verbose_name="Фикс за сделку включён")
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Фикс за сделку")

    class Meta:
        verbose_name = "Схема оплаты сотрудника"
        verbose_name_plural = "Схемы оплаты сотрудников"

    def __str__(self):
        return f"Схема {self.user}"


class SalarySchemeServiceOverrideConsalting(TimeStampedModel):
    """Особая ставка сотрудника по конкретной услуге."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.ForeignKey(
        SalarySchemeConsalting, on_delete=models.CASCADE,
        related_name="service_overrides", verbose_name="Схема"
    )
    service = models.ForeignKey(
        ServicesConsalting, on_delete=models.CASCADE,
        related_name="scheme_overrides", verbose_name="Услуга"
    )
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Процент (%)")
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Фикс за сделку")

    class Meta:
        verbose_name = "Индивидуальная ставка по услуге"
        verbose_name_plural = "Индивидуальные ставки по услугам"
        constraints = [
            models.UniqueConstraint(fields=["scheme", "service"], name="uniq_scheme_service")
        ]


class SalaryDefaultsConsalting(TimeStampedModel):
    """Ставки компании по умолчанию — нижний уровень приоритета."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE,
        related_name="salary_defaults", verbose_name="Компания"
    )
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Дефолтный процент (%)")
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Дефолтный фикс за сделку")
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Дефолтный оклад")
    base_salary_period = models.CharField(max_length=8, default="month", verbose_name="Период оклада")

    class Meta:
        verbose_name = "Ставки компании по умолчанию"
        verbose_name_plural = "Ставки компании по умолчанию"


class ServiceSalaryRateConsalting(TimeStampedModel):
    """Ставка авто-начисления % зарплаты продавца по конкретной услуге."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_salary_rates", verbose_name="Компания"
    )
    service = models.OneToOneField(
        ServicesConsalting, on_delete=models.CASCADE,
        related_name="consulting_salary_rate", verbose_name="Услуга"
    )
    percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name="Процент начисления (%)"
    )
    fixed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Фиксированное начисление"
    )

    class Meta:
        verbose_name = "Ставка зарплаты по услуге"
        verbose_name_plural = "Ставки зарплаты по услугам"

    def __str__(self):
        return f"{self.service.name}: {self.percent}%, {self.fixed_amount} сом"


class BonusRuleConsalting(TimeStampedModel):
    """Правило автоматической премии."""
    class Condition(models.TextChoices):
        SERVICE_COUNT = "service_count", "За количество продаж услуги"
        REVENUE_AMOUNT = "revenue_amount", "За объём выручки"
        DEALS_COUNT = "deals_count", "За количество сделок"
        REVENUE_LADDER = "revenue_ladder", "Прогрессивная шкала"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_bonus_rules", verbose_name="Компания"
    )
    name = models.CharField(max_length=255, verbose_name="Название правила")
    condition = models.CharField(max_length=24, choices=Condition.choices, verbose_name="Условие")
    service = models.ForeignKey(
        ServicesConsalting, null=True, blank=True, on_delete=models.CASCADE, verbose_name="Услуга"
    )
    threshold = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Порог")

    reward_type = models.CharField(max_length=8, default="fixed", verbose_name="Тип вознаграждения")  # fixed | percent
    reward_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Значение вознаграждения")

    period = models.CharField(max_length=8, default="month", verbose_name="Период")  # week | month | quarter
    applies_to = models.CharField(max_length=8, default="all", verbose_name="Применяется к")  # all | role | user
    role = models.ForeignKey(
        "users.CustomRole", null=True, blank=True, on_delete=models.CASCADE, verbose_name="Роль"
    )
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, verbose_name="Сотрудник"
    )

    valid_from = models.DateField(null=True, blank=True, verbose_name="Действительно с")
    valid_to = models.DateField(null=True, blank=True, verbose_name="Действительно по")
    is_active = models.BooleanField(default=True, verbose_name="Активно")

    class Meta:
        verbose_name = "Правило премии"
        verbose_name_plural = "Правила премий"


class BonusTierConsalting(TimeStampedModel):
    """Ступень прогрессивной шкалы."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(
        BonusRuleConsalting, on_delete=models.CASCADE,
        related_name="tiers", verbose_name="Правило премии"
    )
    from_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="От суммы")
    to_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="До суммы")
    percent = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Процент (%)")

    class Meta:
        verbose_name = "Ступень шкалы премий"
        verbose_name_plural = "Ступени шкалы премий"
        ordering = ["from_amount"]


class SalaryAdjustmentConsalting(TimeStampedModel):
    """Ручные штрафы, разовые премии и удержания."""
    class Kind(models.TextChoices):
        FINE = "fine", "Штраф"
        MANUAL_BONUS = "manual_bonus", "Премия"
        DEDUCTION = "deduction", "Удержание"

    class Reason(models.TextChoices):
        LATE = "late", "Опоздание"
        CLIENT_COMPLAINT = "client_complaint", "Жалоба клиента"
        LOST_LEAD = "lost_lead", "Потеря лида"
        RULES_VIOLATION = "rules_violation", "Нарушение регламента"
        SHORTAGE = "shortage", "Недостача по подотчёту"
        SALE_CANCELED = "sale_canceled", "Отмена продажи"
        BONUS = "bonus", "Премия"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_salary_adjustments", verbose_name="Компания"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name="consalting_salary_adjustments", verbose_name="Сотрудник"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, verbose_name="Тип")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма")
    reason = models.CharField(max_length=32, choices=Reason.choices, verbose_name="Причина")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    date = models.DateField(db_index=True, verbose_name="Дата")
    status = models.CharField(max_length=16, default="active", verbose_name="Статус")  # active | canceled
    source_sale = models.ForeignKey("SaleConsalting", null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Продажа")
    created_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name="created_consalting_adjustments", verbose_name="Кем создано"
    )

    class Meta:
        verbose_name = "Корректировка зарплаты"
        verbose_name_plural = "Корректировки зарплаты"
        ordering = ['-created_at']


class SalaryPayoutConsalting(TimeStampedModel):
    """Выплата зарплаты сотруднику."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_salary_payouts", verbose_name="Компания"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name="consalting_salary_payouts", verbose_name="Сотрудник"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма выплаты")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")

    class Meta:
        verbose_name = "Выплата зарплаты"
        verbose_name_plural = "Выплаты зарплаты"
        ordering = ['-created_at']

    def __str__(self):
        return f"Выплата {self.user}: {self.amount}"


class SalaryAccrualConsalting(TimeStampedModel):
    """Автоматическое начисление зарплаты продавцу с закрытой продажи / лида / оклада / премии / штрафа."""
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        ACCRUED = "accrued", "Начислено"
        PAID = "paid", "Выплачено"
        CANCELED = "canceled", "Отменено"

    class Kind(models.TextChoices):
        SALARY = "salary", "Оклад"
        PERCENT = "percent", "Процент со сделок"
        FIXED = "fixed", "Фикс за сделку"
        BONUS = "bonus", "Премия"
        MANUAL_BONUS = "manual_bonus", "Разовая премия"
        FINE = "fine", "Штраф"
        DEDUCTION = "deduction", "Удержание"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_salary_accruals", verbose_name="Компания"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name="consalting_salary_accruals", verbose_name="Продавец"
    )
    service = models.ForeignKey(
        ServicesConsalting, on_delete=models.PROTECT,
        null=True, blank=True, related_name="salary_accruals", verbose_name="Услуга"
    )
    sale = models.ForeignKey(
        "SaleConsalting", null=True, blank=True, on_delete=models.CASCADE,
        related_name="salary_accruals", verbose_name="Продажа"
    )
    lead = models.ForeignKey(
        "LeadConsalting", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="salary_accruals", verbose_name="Лид"
    )
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.PERCENT, verbose_name="Вид начисления"
    )
    rule = models.ForeignKey(
        BonusRuleConsalting, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="accruals", verbose_name="Правило премии"
    )
    period_month = models.CharField(
        max_length=7, blank=True, verbose_name="Месяц периода (YYYY-MM)"
    )
    base_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Базовая сумма сделки"
    )
    percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name="Снимок ставки (%)"
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="Сумма начисления"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACCRUED, verbose_name="Статус"
    )
    payout = models.ForeignKey(
        SalaryPayoutConsalting, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="accruals", verbose_name="Выплата"
    )

    class Meta:
        verbose_name = "Начисление зарплаты"
        verbose_name_plural = "Начисления зарплаты"
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=["sale", "kind"],
                condition=~models.Q(status="canceled"),
                name="uniq_consalting_accrual_per_sale_kind"
            )
        ]

    def __str__(self):
        return f"Начисление {self.user}: {self.amount} ({self.kind}, {self.status})"


# ======== Входящие лиды из WhatsApp и авто-распределение по ролям ========
class InboundLeadConsalting(TimeStampedModel):
    """Входящий лид (WhatsApp / ручной ввод) до авто-распределения или создания воронки."""
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        ASSIGNED = "assigned", "Назначен"
        IN_WORK = "in_work", "В работе"
        DEFERRED = "deferred", "Отложен"
        CONVERTED = "converted", "Купил"
        REJECTED = "rejected", "Отказ"

    class DeferReason(models.TextChoices):
        NO_ANSWER_CALL = "no_answer_call", "Не взял трубку"
        NO_ANSWER_CHAT = "no_answer_chat", "Не ответил в переписке"
        CALL_LATER = "call_later", "Просил перезвонить позже"
        THINKING = "thinking", "Думает / советуется"
        NO_MONEY = "no_money", "Нет денег сейчас"
        OTHER = "other", "Другое"

    class RejectReason(models.TextChoices):
        EXPENSIVE = "expensive", "Дорого"
        COMPETITOR = "competitor", "Ушёл к конкуренту"
        NO_NEED = "no_need", "Не актуально"
        NO_CONTACT = "no_contact", "Не выходит на связь"
        SPAM = "spam", "Спам / нецелевой"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="consalting_inbound_leads", verbose_name="Компания"
    )
    full_name = models.CharField(max_length=255, blank=True, verbose_name="Имя клиента")
    phone = models.CharField(max_length=32, blank=True, db_index=True, verbose_name="Телефон")
    source = models.CharField(max_length=32, default="whatsapp", verbose_name="Источник")
    external_id = models.CharField(max_length=128, blank=True, verbose_name="ID сообщения провайдера")
    message = models.TextField(blank=True, verbose_name="Текст сообщения")
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="consalting_inbound_leads", verbose_name="Владелец"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NEW, verbose_name="Статус"
    )
    lead = models.ForeignKey(
        "LeadConsalting", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="inbound_leads", verbose_name="Карточка воронки"
    )

    # --- Новые поля согласно 01-leads.md ---
    remind_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Время напоминания")
    defer_reason = models.CharField(max_length=32, choices=DeferReason.choices, blank=True, verbose_name="Причина откладывания")
    defer_comment = models.TextField(blank=True, verbose_name="Комментарий к откладыванию")
    defer_count = models.PositiveIntegerField(default=0, verbose_name="Количество откладываний")
    deferred_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда отложен")
    reminded_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда отправлено напоминание")

    reject_reason = models.CharField(max_length=32, choices=RejectReason.choices, blank=True, verbose_name="Причина отказа")
    reject_comment = models.TextField(blank=True, verbose_name="Комментарий к отказу")

    first_reply_at = models.DateTimeField(null=True, blank=True, verbose_name="Время первого ответа")
    converted_at = models.DateTimeField(null=True, blank=True, verbose_name="Время конвертации")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Время закрытия")
    sale = models.ForeignKey(
        "SaleConsalting", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="inbound_leads", verbose_name="Продажа"
    )

    class Meta:
        verbose_name = "Входящий лид WhatsApp"
        verbose_name_plural = "Входящие лиды WhatsApp"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["company", "owner", "status"]),
            models.Index(fields=["company", "status", "remind_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "source", "external_id"],
                condition=~models.Q(external_id=""),
                name="uniq_consalting_inbound_external"
            )
        ]

    def __str__(self):
        return f"{self.full_name or self.phone} ({self.status})"


class LeadDistributionSettingsConsalting(TimeStampedModel):
    """Правила авто-распределения входящих лидов между сотрудниками компании."""
    class Strategy(models.TextChoices):
        ROUND_ROBIN = "round_robin", "Поровну (Round-Robin)"
        LEAST_LOADED = "least_loaded", "По наименьшей загрузке"
        MANUAL = "manual", "Вручную"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE,
        related_name="consalting_lead_distribution", verbose_name="Компания"
    )
    enabled = models.BooleanField(default=True, verbose_name="Авто-распределение включено")
    strategy = models.CharField(
        max_length=16, choices=Strategy.choices, default=Strategy.ROUND_ROBIN, verbose_name="Стратегия"
    )
    roles = models.ManyToManyField(
        "users.CustomRole", blank=True, related_name="consalting_distribution_settings", verbose_name="Роли-получатели"
    )
    _rr_cursor = models.IntegerField(default=0, verbose_name="Указатель Round-Robin")

    class Meta:
        verbose_name = "Настройки распределения лидов"
        verbose_name_plural = "Настройки распределения лидов"

    def __str__(self):
        return f"Распределение {self.company}: {self.strategy} (enabled={self.enabled})"


# ======== Абонентская плата и график платежей (§5.2) ========
class SubscriptionConsalting(TimeStampedModel):
    """Подключённая клиенту абонентская услуга (§5.2)."""
    class Period(models.TextChoices):
        MONTH = "month", "Ежемесячно"
        YEAR = "year", "Ежегодно"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        PAUSED = "paused", "Приостановлена"
        CANCELED = "canceled", "Отменена"
        FINISHED = "finished", "Завершена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="consalting_subscriptions")
    client = models.ForeignKey("main.Client", on_delete=models.CASCADE, related_name="consalting_subscriptions")
    service = models.ForeignKey(ServicesConsalting, on_delete=models.PROTECT, related_name="subscriptions")
    tariff = models.ForeignKey(TariffConsalting, null=True, blank=True, on_delete=models.PROTECT, related_name="subscriptions")
    sale = models.ForeignKey("SaleConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="subscriptions")
    lead = models.ForeignKey("LeadConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="subscriptions")

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    period = models.CharField(max_length=8, choices=Period.choices, default=Period.MONTH)
    start_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    canceled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Абонентская подписка"
        verbose_name_plural = "Абонентские подписки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client} — {self.service.name}: {self.amount} ({self.period})"


class SubscriptionPaymentConsalting(models.Model):
    """Одна строка графика: период → сумма → статус (§5.2)."""
    class Status(models.TextChoices):
        PLANNED = "planned", "Запланирован"
        PAID = "paid", "Оплачен"
        OVERDUE = "overdue", "Просрочен"
        CANCELED = "canceled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(SubscriptionConsalting, on_delete=models.CASCADE, related_name="payments")
    period_month = models.CharField(max_length=7)  # "2026-07" — ключ ячейки матрицы
    due_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    paid_at = models.DateTimeField(null=True, blank=True)
    cashbox_id = models.UUIDField(null=True, blank=True)
    payment_method = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        verbose_name = "Платёж абонентской подписки"
        verbose_name_plural = "Платежи абонентских подписок"
        constraints = [
            models.UniqueConstraint(fields=["subscription", "period_month"], name="uniq_consalting_sub_period")
        ]
        indexes = [models.Index(fields=["due_date", "status"])]
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.subscription.client} [{self.period_month}] — {self.amount} ({self.status})"


# ======== План продаж и веса КПД (§6.3, §6.4) ========
class SalesPlanConsalting(TimeStampedModel):
    """Личный план продаж сотрудника по месяцам (§6.4)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="consalting_sales_plans")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consalting_sales_plans")
    period_month = models.CharField(max_length=7)  # "2026-07"
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "План продаж сотрудника"
        verbose_name_plural = "Планы продаж сотрудников"
        constraints = [
            models.UniqueConstraint(fields=["user", "period_month"], name="uniq_consalting_user_plan_month")
        ]

    def __str__(self):
        return f"{self.user} [{self.period_month}]: {self.amount}"


class KpiWeightsConsalting(TimeStampedModel):
    """Настройки весов КПД на уровне компании (§6.3)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="consalting_kpi_weights")
    conversion = models.FloatField(default=0.35)
    plan = models.FloatField(default=0.3)
    speed = models.FloatField(default=0.2)
    discipline = models.FloatField(default=0.15)

    class Meta:
        verbose_name = "Веса составляющих КПД"
        verbose_name_plural = "Веса составляющих КПД"

    def __str__(self):
        return f"KPI Weights ({self.company}): conv={self.conversion}, plan={self.plan}, speed={self.speed}, disc={self.discipline}"


# ======== Кассовые операции и заявки сдачи наличных (§7.2) ========
class CashOperationConsalting(TimeStampedModel):
    """Кассовая операция с привязкой к сотруднику (§7.2)."""
    class Kind(models.TextChoices):
        SALE = "sale", "Продажа"
        HANDOVER = "handover", "Сдача наличных"
        REFUND = "refund", "Возврат"
        SUBSCRIPTION = "subscription", "Абонентская плата"

    class Direction(models.TextChoices):
        INCOME = "income", "Приход"
        OUTCOME = "outcome", "Расход"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="consalting_cash_operations")
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="consalting_cash_operations", db_index=True, verbose_name="Сотрудник"
    )
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="Кассир/Подтвердивший"
    )
    sale = models.ForeignKey(
        "SaleConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_operations"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SALE, db_index=True)
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.INCOME)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=16, default="cash")  # cash | transfer
    comment = models.TextField(blank=True)
    cashbox_id = models.UUIDField(null=True, blank=True)

    class Meta:
        verbose_name = "Кассовая операция"
        verbose_name_plural = "Кассовые операции"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} ({self.direction}): {self.amount} [{self.user}]"


class CashRequestConsalting(TimeStampedModel):
    """Заявка на проведение/сдачу кассовой операции (§7.2, §7.4, §9.2)."""
    class Kind(models.TextChoices):
        SALE = "sale", "Продажа"
        HANDOVER = "handover", "Сдача наличных"
        REFUND = "refund", "Возврат клиенту"
        SUBSCRIPTION = "subscription", "Абонентский платёж"

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        CONFIRMED = "confirmed", "Подтверждено"
        REJECTED = "rejected", "Отклонено"
        CANCELED = "canceled", "Снято"

    class RejectReason(models.TextChoices):
        NO_MONEY = "no_money", "Деньги не поступили"
        AMOUNT_MISMATCH = "amount_mismatch", "Сумма не совпадает"
        OTHER_METHOD = "other_method", "Оплата прошла другим способом"
        DUPLICATE = "duplicate", "Дубль операции"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="consalting_cash_requests")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="consalting_cash_requests", verbose_name="Сотрудник"
    )
    sale = models.ForeignKey(
        "SaleConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_requests"
    )
    subscription_payment = models.ForeignKey(
        "SubscriptionPaymentConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_requests"
    )
    client = models.ForeignKey("main.Client", null=True, blank=True, on_delete=models.SET_NULL)

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.HANDOVER, db_index=True)
    direction = models.CharField(max_length=8, default="income")  # income | expense
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=16, blank=True, default="cash")
    comment = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="confirmed_consalting_requests"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=32, choices=RejectReason.choices, blank=True)
    reject_comment = models.TextField(blank=True)
    cash_operation = models.ForeignKey(
        "CashOperationConsalting", null=True, blank=True, on_delete=models.SET_NULL, related_name="requests"
    )
    cashbox_id = models.UUIDField(null=True, blank=True)

    class Meta:
        verbose_name = "Заявка на кассовую операцию"
        verbose_name_plural = "Заявки на кассовые операции"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Request {self.kind} ({self.status}): {self.amount} [{self.user}]"


class CashConfirmationSettingsConsalting(TimeStampedModel):
    """Настройки подтверждения кассы (§9.2)."""
    class Mode(models.TextChoices):
        ALWAYS = "always", "Всегда"
        CASH_ONLY = "cash_only", "Только для наличных"
        OFF = "off", "Выключено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name="consalting_cash_confirmation"
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.CASH_ONLY)
    skip_for_cashier = models.BooleanField(default=True)
    overdue_hours = models.PositiveIntegerField(default=24)

    class Meta:
        verbose_name = "Настройки подтверждения кассы"
        verbose_name_plural = "Настройки подтверждения кассы"

    def __str__(self):
        return f"CashConfirmationSettings ({self.company}): mode={self.mode}"



