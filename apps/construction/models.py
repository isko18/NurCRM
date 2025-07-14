from django.db import models
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
    department = models.OneToOneField(
        Department,
        on_delete=models.CASCADE,
        related_name='cashbox'
    )

    def __str__(self):
        return f"Касса {self.department.name}"

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
    cashbox = models.ForeignKey(
        Cashbox,
        on_delete=models.CASCADE,
        related_name='flows'
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name='Тип')
    name = models.CharField(max_length=255, verbose_name='Наименование')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} ₽ ({self.cashbox.department.name})"

    class Meta:
        verbose_name = 'Движение по кассе'
        verbose_name_plural = 'Движения по кассе'
        ordering = ['-created_at']
