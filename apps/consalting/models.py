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
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="consalting_sales",
        related_query_name="consalting_sale",
        verbose_name="Клиент"
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

    def clean(self):
        # company согласованность
        if self.company_id:
            if self.user and getattr(self.user, 'company_id', None) not in (None, self.company_id):
                raise ValidationError({'user': 'Пользователь из другой компании.'})
            if self.services and self.services.company_id != self.company_id:
                raise ValidationError({'services': 'Услуга принадлежит другой компании.'})
            if self.client and getattr(self.client, 'company_id', None) != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})

        # branch согласованность (если задан)
        if self.branch_id:
            if self.services and self.services.branch_id not in (None, self.branch_id):
                raise ValidationError({'services': 'Услуга другого филиала.'})
            if self.client and getattr(self.client, 'branch_id', None) not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


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

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'branch', 'is_active']),
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
        ]

    def __str__(self):
        return self.name or str(self.id)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ======== Стадия воронки ========
class FunnelStageConsalting(TimeStampedModel):
    """Стадия (этап) воронки продаж."""
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
    is_final = models.BooleanField(
        default=False,
        verbose_name='Финальная стадия',
        help_text='Стадия закрытия лида (успех или провал)'
    )
    is_success = models.BooleanField(
        default=False,
        verbose_name='Успешная стадия',
        help_text='Стадия успешного закрытия лида'
    )

    class Meta:
        verbose_name = 'Стадия воронки'
        verbose_name_plural = 'Стадии воронки'
        ordering = ['funnel', 'order']
        indexes = [
            models.Index(fields=['company', 'funnel', 'order']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('funnel', 'order'),
                name='uniq_consalting_stage_order_per_funnel',
            ),
        ]

    def __str__(self):
        return f"{self.funnel.name} — {self.name}"

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

    class Meta:
        verbose_name = 'Лид'
        verbose_name_plural = 'Лиды'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'funnel', 'stage']),
            models.Index(fields=['company', 'branch', 'status']),
            models.Index(fields=['company', 'owner']),
            models.Index(fields=['company', 'created_at']),
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
