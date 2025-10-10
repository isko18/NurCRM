from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Sum
import uuid
import random

from apps.users.models import Company, User, Branch


# ==========================
# Отдел
# ==========================
class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cash_departments',   # уникальный related_name для модуля "cash"
        verbose_name='Компания',
    )
    # может быть глобальным (NULL) или филиальным
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='cash_departments',
        verbose_name='Филиал',
        null=True, blank=True, db_index=True,
    )

    name = models.CharField(max_length=255, verbose_name='Название отдела')

    employees = models.ManyToManyField(
        User,
        blank=True,
        related_name='cash_departments',
        verbose_name='Сотрудники отдела',
    )

    color = models.CharField(
        max_length=7, blank=True, null=True, default='',
        verbose_name='Цвет отдела (RGB)',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Отдел'
        verbose_name_plural = 'Отделы'
        constraints = [
            # уникальность названия в рамках филиала
            models.UniqueConstraint(
                fields=('company', 'branch', 'name'),
                name='uniq_cash_department_company_branch_name',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.name})"

    def clean(self):
        # company ↔ branch.company согласованность
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def save(self, *args, **kwargs):
        if not self.color:
            self.color = "#{:06x}".format(random.randint(0, 0xFFFFFF)).upper()
        self.full_clean()
        return super().save(*args, **kwargs)

    # быстрая сводка с учётом только утверждённых движений
    def cashflow_summary(self):
        if hasattr(self, 'cashbox') and self.cashbox_id:
            return self.cashbox.get_summary()
        return {'income': {'total': 0, 'count': 0}, 'expense': {'total': 0, 'count': 0}}


# ==========================
# Касса
# ==========================
class Cashbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='cash_cashboxes', verbose_name='Компания',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name='cash_cashboxes', verbose_name='Филиал',
        null=True, blank=True, db_index=True,
    )
    name = models.CharField(max_length=255, blank=True, null=True, verbose_name='Название кассы')

    # привязка к отделу той же company/branch (проверяем в clean())
    department = models.OneToOneField(
        Department, on_delete=models.CASCADE, related_name='cashbox',
        null=True, blank=True, verbose_name='Отдел',
    )

    class Meta:
        verbose_name = 'Касса'
        verbose_name_plural = 'Кассы'
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]
        constraints = [
            # безопасные (без join) уникальности имён
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_cashbox_name_per_branch',
                condition=models.Q(branch__isnull=False) & models.Q(name__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_cashbox_name_global_per_company',
                condition=models.Q(branch__isnull=True) & models.Q(name__isnull=False),
            ),
        ]

    def __str__(self):
        if self.department:
            base = f"Касса отдела {self.department.name}"
            return f"{base}{f' ({self.name})' if self.name else ''}"
        return self.name or "Свободная касса"

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # department согласован с company/branch
        if self.department_id:
            if self.department.company_id != self.company_id:
                raise ValidationError({'department': 'Отдел другой компании.'})
            if (self.department.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'department': 'Отдел другого филиала (или глобальности).'})

    def save(self, *args, **kwargs):
        # если задан отдел — подберём company/branch из отдела
        if self.department_id:
            self.company_id = self.department.company_id
            self.branch_id = self.department.branch_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_summary(self) -> dict:
        """
        Сводка по кассе только по утверждённым операциям (status=True),
        раздельно по приходу/расходу.
        """
        qs = self.flows.filter(status=True)  # CashFlow.related_name='flows'
        income_qs = qs.filter(type='income')
        expense_qs = qs.filter(type='expense')

        income_total = income_qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')
        expense_total = expense_qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')

        return {
            'income':  {'total': income_total,  'count': income_qs.count()},
            'expense': {'total': expense_total, 'count': expense_qs.count()},
        }


# ==========================
# Движение по кассе
# ==========================
class CashFlow(models.Model):
    TYPE_CHOICES = [
        ('income', 'Приход'),
        ('expense', 'Расход'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='cash_cashflows', verbose_name='Компания',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name='cash_cashflows', verbose_name='Филиал',
        null=True, blank=True, db_index=True,
    )

    cashbox = models.ForeignKey(
        Cashbox, on_delete=models.CASCADE,
        related_name='flows', verbose_name='Касса',
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name='Тип')
    name = models.CharField(max_length=255, verbose_name='Наименование', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.BooleanField(default=False, verbose_name="Принять")

    class Meta:
        verbose_name = 'Движение по кассе'
        verbose_name_plural = 'Движения по кассе'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
            models.Index(fields=['cashbox', 'created_at']),
            models.Index(fields=['status']),
        ]
        constraints = [
            # допустимый CHECK без join
            models.CheckConstraint(check=models.Q(amount__gt=0), name='ck_cashflow_amount_positive'),
        ]

    def __str__(self):
        dept = self.cashbox.department.name if self.cashbox and self.cashbox.department_id else "Без отдела"
        return f"{self.get_type_display()} {self.amount} ₽ ({dept}, {self.company.name})"

    def clean(self):
        # company/branch должны совпадать с кассой
        if self.cashbox_id:
            if self.company_id != self.cashbox.company_id:
                raise ValidationError({'company': 'Компания движения должна совпадать с компанией кассы.'})
            if (self.branch_id or None) != (self.cashbox.branch_id or None):
                raise ValidationError({'branch': 'Филиал движения должен совпадать с филиалом кассы (или быть глобальным вместе с ней).'})

    def save(self, *args, **kwargs):
        # наследуем company/branch из кассы
        if self.cashbox_id:
            self.company_id = self.cashbox.company_id
            self.branch_id = self.cashbox.branch_id
        self.full_clean()
        return super().save(*args, **kwargs)
