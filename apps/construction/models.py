from django.db import models
import uuid
from apps.users.models import Company, User
class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name='Название отдела')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='departments', verbose_name='Компания')
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='managed_departments', verbose_name='Ответственный сотрудник'
    )
    employees = models.ManyToManyField(
        User,
        blank=True,
        related_name='departments',
        verbose_name='Сотрудники отдела'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.company.name})"

    def cashflow_summary(self):
        if hasattr(self, 'cashbox'):
            return self.cashbox.get_summary()
        return {
            'income_total': 0,
            'expense_total': 0,
            'income_count': 0,
            'expense_count': 0,
            'balance': 0
        }

    class Meta:
        verbose_name = 'Отдел'
        verbose_name_plural = 'Отделы'



class Cashbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.OneToOneField(Department, on_delete=models.CASCADE, related_name='cashbox')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"Касса {self.department.name}"

    def get_summary(self):
        """Аналитика по кассе"""
        income = self.flows.filter(type='income')
        expense = self.flows.filter(type='expense')

        income_total = income.aggregate(total=models.Sum('amount'))['total'] or 0
        expense_total = expense.aggregate(total=models.Sum('amount'))['total'] or 0
        income_count = income.count()
        expense_count = expense.count()

        return {
            'income_total': income_total,
            'expense_total': expense_total,
            'income_count': income_count,
            'expense_count': expense_count,
            'balance': self.balance
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
    cashbox = models.ForeignKey(Cashbox, on_delete=models.CASCADE, related_name='flows')
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
