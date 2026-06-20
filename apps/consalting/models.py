from django.db import models
from django.core.exceptions import ValidationError
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
    description = models.TextField(verbose_name="Заметка", blank=True)

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

    # ----- расчёт итоговой суммы -----
    def base_price(self):
        """Цена тарифа, если выбран; иначе базовая цена услуги."""
        if self.tariff_id:
            return self.tariff.price or 0
        if self.services_id:
            return self.services.price or 0
        return 0

    def installation_price(self):
        return (self.services.installation_price or 0) if self.services_id else 0

    def items_total(self):
        # при пересчёте элементы могут быть ещё не сохранены — используем уже сохранённые
        return sum((i.price or 0) for i in self.items.all())

    def compute_total(self):
        """Итого = тариф + установка + доп. товары − скидка + наценка."""
        return (
            self.base_price()
            + self.installation_price()
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
        return f"{self.funnel.name} — {self.name}"

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
    phone = models.CharField(max_length=32, blank=True, verbose_name='Телефон')
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
