from django.db import models
from apps.users.models import Company, User
from apps.main.models import Client
import uuid


class TimeStampedModel(models.Model):
    """Abstract model that provides created/updated timestamps."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True


class ServicesConsalting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='services_consalting',
        verbose_name='Компания'
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена")
    description = models.TextField(verbose_name="Описание", blank=True)

    def __str__(self):
        return self.name or str(self.id)

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        ordering = ['name']
        indexes = [models.Index(fields=['company', 'name'])]


class SaleConsalting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='consalting',
        verbose_name='Компания'
    )
    # user should reference User model (previously incorrectly referenced Company)
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_consalting',
        verbose_name='Пользователь'
    )
    services = models.ForeignKey(
        'consalting.ServicesConsalting',  # латинская "c"
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services_sale",
        verbose_name="Услуга"
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consalting_sale",
        verbose_name="Клиент"
    )
    description = models.TextField(verbose_name="Заметка", blank=True)

    def __str__(self):
        # return a readable string even if related fields are null
        service_name = self.services.name if self.services else "(без услуги)"
        return f"{service_name} — {self.company}"

    class Meta:
        verbose_name = "Продажа услуги"
        verbose_name_plural = "Продажа услуг"
        ordering = ['-created_at']
        indexes = [models.Index(fields=['company', 'created_at'])]


class SalaryConsalting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='salary_consalting',
        verbose_name='Компания'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_consalting_salary',
        verbose_name='Пользователь'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма")
    percent = models.CharField(max_length=255, verbose_name="Процент")
    description = models.TextField(verbose_name="Описание", blank=True)

    def __str__(self):
        return f"{self.company} — {self.amount}"

    class Meta:
        verbose_name = "Зарплата / Выплата"
        verbose_name_plural = "Зарплаты / Выплаты"
        ordering = ['-created_at']
        indexes = [models.Index(fields=['company', 'user'])]


class RequestsConsalting(TimeStampedModel):
    TYPE_CHOICES = [
        ('new', 'Новая'),
        ('in_progress', 'В работе'),
        ('completed', 'Завершена'),
        ('cancelled', 'Отменена'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='requests_consalting',
        verbose_name='Компания'
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consalting_requests",
        verbose_name="Клиент"
    )
    status = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Статус', default='new')
    name = models.CharField(max_length=255, verbose_name="Заявка")
    description = models.TextField(verbose_name="Описание", blank=True)

    def __str__(self):
        return f"{self.name} — {self.get_status_display()}"

    class Meta:
        verbose_name = "Заявка на консультацию"
        verbose_name_plural = "Заявки на консультацию"
        ordering = ['-created_at']
        indexes = [models.Index(fields=['company', 'status'])]
