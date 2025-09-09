from django.db import models
from django.core.exceptions import ValidationError
import uuid
from apps.users.models import Company, User
import random


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name='Название отдела')
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='departments',
        verbose_name='Компания'
    )
    employees = models.ManyToManyField(
        User,
        blank=True,
        related_name='departments',
        verbose_name='Сотрудники отдела'
    )
    color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        default='',
        verbose_name='Цвет отдела (RGB)'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.company.name})"

    def cashflow_summary(self):
        if hasattr(self, 'cashbox'):
            return self.cashbox.get_summary()
        return {
            'income': {'total': 0, 'count': 0},
            'expense': {'total': 0, 'count': 0}
        }

    def save(self, *args, **kwargs):
        if not self.color:
            self.color = self._generate_random_color()
        super().save(*args, **kwargs)

    def _generate_random_color(self):
        return "#{:06x}".format(random.randint(0, 0xFFFFFF)).upper()

    class Meta:
        verbose_name = 'Отдел'
        verbose_name_plural = 'Отделы'


class Cashbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Название кассы")

    # ЯВНАЯ привязка к компании
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cashboxes',
        verbose_name='Компания'
    )

    # По-прежнему можно связать с отделом (его компания должна совпадать с company)
    department = models.OneToOneField(
        Department,
        on_delete=models.CASCADE,
        related_name='cashbox',
        null=True, blank=True,
        verbose_name='Отдел'
    )

    def __str__(self):
        if self.department:
            base = f"Касса отдела {self.department.name}"
            return f"{base}{f' ({self.name})' if self.name else ''}"
        return self.name or "Свободная касса"

    def clean(self):
        # Защита от несоответствия компаний
        if self.department and self.department.company_id != self.company_id:
            raise ValidationError({'company': 'Компания кассы должна совпадать с компанией отдела.'})

    def save(self, *args, **kwargs):
        # Если отдел задан, но company не задан явно — подставим из отдела
        if self.department and not self.company_id:
            self.company = self.department.company
        self.full_clean(exclude=None)  # чтобы сработал clean() и валидаторы
        super().save(*args, **kwargs)

    def get_summary(self):
        """Аналитика по кассе (без баланса)"""
        income = self.flows.filter(type='income')
        expense = self.flows.filter(type='expense')

        income_total = income.aggregate(total=models.Sum('amount'))['total'] or 0
        expense_total = expense.aggregate(total=models.Sum('amount'))['total'] or 0

        return {
            'income': {
                'total': income_total,
                'count': income.count()
            },
            'expense': {
                'total': expense_total,
                'count': expense.count()
            }
        }

    class Meta:
        verbose_name = 'Касса'
        verbose_name_plural = 'Кассы'


class CashFlow(models.Model):
    TYPE_CHOICES = [
        ('income', 'Приход'),
        ('expense', 'Расход'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ЯВНАЯ привязка к компании
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cashflows',
        verbose_name='Компания'
    )

    cashbox = models.ForeignKey(
        Cashbox,
        on_delete=models.CASCADE,
        related_name='flows',
        verbose_name='Касса'
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name='Тип')
    name = models.CharField(max_length=255, verbose_name='Наименование', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        department_name = (
            self.cashbox.department.name
            if self.cashbox and self.cashbox.department
            else "Без отдела"
        )
        return f"{self.get_type_display()} {self.amount} ₽ ({department_name}, {self.company.name})"

    def clean(self):
        # Компания движения должна совпадать с компанией кассы
        if self.cashbox and self.company_id != self.cashbox.company_id:
            raise ValidationError({'company': 'Компания движения должна совпадать с компанией кассы.'})

    def save(self, *args, **kwargs):
        # Если company не задана явно — наследуем из кассы
        if self.cashbox and not self.company_id:
            self.company_id = self.cashbox.company_id
        self.full_clean(exclude=None)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Движение по кассе'
        verbose_name_plural = 'Движения по кассе'
        ordering = ['-created_at']
