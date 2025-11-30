from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Sum, Count, Q
import uuid

from apps.users.models import Company, Branch


# ==========================
# Касса
# ==========================
class Cashbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cash_cashboxes',
        verbose_name='Компания',
    )
    # может быть глобальной (для компании) или филиальной
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='cash_cashboxes',
        verbose_name='Филиал',
        null=True,
        blank=True,
        db_index=True,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Название кассы',
    )

    # флаг «расходной» кассы, если используется в бизнес-логике
    is_consumption = models.BooleanField(
        verbose_name="Расход",
        default=False,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = 'Касса'
        verbose_name_plural = 'Кассы'
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]
        constraints = [
            # уникальность имён в пределах филиала (для филиальных касс)
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_cashbox_name_per_branch',
                condition=models.Q(branch__isnull=False) & models.Q(name__isnull=False),
            ),
            # и глобально по компании, если branch IS NULL (касса компании)
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_cashbox_name_global_per_company',
                condition=models.Q(branch__isnull=True) & models.Q(name__isnull=False),
            ),
        ]

    def __str__(self):
        # касса филиала
        if self.branch_id:
            base = f"Касса филиала {self.branch.name}"
            return f"{base}{f' ({self.name})' if self.name else ''}"
        # касса компании
        return self.name or f"Касса компании {self.company.name}"

    def clean(self):
        # company ↔ branch.company согласованность
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def save(self, *args, **kwargs):
        # отделов больше нет, company/branch задаются напрямую
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_summary(self) -> dict:
        """
        Сводка по кассе только по утверждённым операциям
        (status=APPROVED), раздельно по приходу/расходу.
        """
        from .models import CashFlow  # локальный импорт, чтобы избежать циклов

        approved_qs = self.flows.filter(
            status=CashFlow.Status.APPROVED
        )

        agg = approved_qs.aggregate(
            income_total=Sum('amount', filter=Q(type=CashFlow.Type.INCOME)),
            expense_total=Sum('amount', filter=Q(type=CashFlow.Type.EXPENSE)),
            income_count=Count('id', filter=Q(type=CashFlow.Type.INCOME)),
            expense_count=Count('id', filter=Q(type=CashFlow.Type.EXPENSE)),
        )

        z = Decimal('0')

        return {
            'income': {
                'total': agg['income_total'] or z,
                'count': agg['income_count'] or 0,
            },
            'expense': {
                'total': agg['expense_total'] or z,
                'count': agg['expense_count'] or 0,
            },
        }


# ==========================
# Движение по кассе
# ==========================
class CashFlow(models.Model):
    class Type(models.TextChoices):
        INCOME = 'income', 'Приход'
        EXPENSE = 'expense', 'Расход'

    class Status(models.TextChoices):
        PENDING = 'pending', 'В ожидании'
        APPROVED = 'approved', 'Успешно'
        REJECTED = 'rejected', 'Отклонено'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cash_cashflows',
        verbose_name='Компания',
    )

    # может быть глобальным (для кассы компании) или филиальным
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='cash_cashflows',
        verbose_name='Филиал',
        null=True,
        blank=True,
        db_index=True,
    )

    cashbox = models.ForeignKey(
        Cashbox,
        on_delete=models.CASCADE,
        related_name='flows',
        verbose_name='Касса',
    )

    # тип движения денег
    type = models.CharField(
        max_length=10,
        choices=Type.choices,
        verbose_name='Тип',
    )

    name = models.CharField(
        max_length=255,
        verbose_name='Наименование',
        null=True,
        blank=True,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Сумма',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # статус проведения
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name='Статус',
        db_index=True,
    )

    # ID кассовой операции-источника (например, при переводе между кассами)
    source_cashbox_flow_id = models.CharField(
        max_length=36,
        null=True,
        blank=True,
        verbose_name='ID кассовой операции-источника',
        help_text='ID другой кассовой операции, из которой появилась эта запись (перевод и т.п.)',
    )

    # ID бизнес-операции (продажа, приход товара и т.д.)
    source_business_operation_id = models.CharField(
        max_length=36,
        null=True,
        blank=True,
        verbose_name='ID бизнес-операции',
        help_text='ID операции продаж/поступления товара/внешнего документа, породившего движение денег',
    )

    class Meta:
        verbose_name = 'Движение по кассе'
        verbose_name_plural = 'Движения по кассе'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
            models.Index(fields=['cashbox', 'created_at']),
            models.Index(fields=['status']),
            models.Index(
                fields=['cashbox', 'status', 'type', 'created_at'],
                name='ix_flow_cb_stat_type_created',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name='ck_cashflow_amount_positive',
            ),
        ]

    def __str__(self):
        if self.cashbox and self.cashbox.branch_id:
            place = f"Филиал {self.cashbox.branch.name}"
        else:
            place = f"Компания {self.company.name}"
        return f"{self.get_type_display()} {self.amount} ₽ ({place})"

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
