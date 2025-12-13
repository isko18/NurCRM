from decimal import Decimal
import uuid

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Sum, Count, Q
from django.utils import timezone

from apps.users.models import Company, Branch


class Cashbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="cash_cashboxes",
        verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="cash_cashboxes",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )

    name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Название кассы")
    is_consumption = models.BooleanField(verbose_name="Расход", default=False, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Касса"
        verbose_name_plural = "Кассы"
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "branch"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("branch", "name"),
                name="uq_cashbox_name_per_branch",
                condition=Q(branch__isnull=False) & Q(name__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "name"),
                name="uq_cashbox_name_global_per_company",
                condition=Q(branch__isnull=True) & Q(name__isnull=False),
            ),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        if self.branch_id:
            base = f"Касса филиала {self.branch.name}"
            return f"{base}{f' ({self.name})' if self.name else ''}"
        return self.name or f"Касса компании {self.company.name}"


class CashShift(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        CLOSED = "closed", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="shifts")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shifts",
        db_index=True,
    )

    cashbox = models.ForeignKey("construction.Cashbox", on_delete=models.PROTECT, related_name="shifts")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts")

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    closing_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # кешируем итоги ТОЛЬКО при закрытии
    income_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    expense_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sales_count = models.PositiveIntegerField(default=0)
    sales_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cash_sales_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    noncash_sales_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        constraints = [
            # ✅ ОДНА открытая смена на кассу
            models.UniqueConstraint(
                fields=("cashbox",),
                condition=Q(status="open"),
                name="uq_open_shift_per_cashbox",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "branch", "opened_at"]),
            models.Index(fields=["cashbox", "opened_at"]),
            models.Index(fields=["cashier", "opened_at"]),
            models.Index(fields=["status", "opened_at"]),
        ]

    def clean(self):
        if self.cashbox_id:
            if self.company_id != self.cashbox.company_id:
                raise ValidationError({"company": "Компания смены должна совпадать с компанией кассы."})
            if (self.branch_id or None) != (self.cashbox.branch_id or None):
                raise ValidationError({"branch": "Филиал смены должен совпадать с филиалом кассы (или оба None)."})

        if self.cashier_id:
            cashier_company_id = getattr(self.cashier, "company_id", None)
            if cashier_company_id and cashier_company_id != self.company_id:
                raise ValidationError({"cashier": "Кассир другой компании."})

    # ─────────────────────────────────────────────
    # ✅ LIVE totals (для OPEN смены, без записи в БД)
    # ─────────────────────────────────────────────
    def calc_live_totals(self) -> dict:
        from django.db import connection
        from django.db.models import Sum, Count, Q, DecimalField, Case, When, Value
        from django.db.models.expressions import RawSQL

        z = Decimal("0.00")

        # flows (approved)
        flows = self.shift_flows.filter(status=CashFlow.Status.APPROVED)
        fa = flows.aggregate(
            income=Sum("amount", filter=Q(type=CashFlow.Type.INCOME)),
            expense=Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
        )

        # sales (paid) — берём МОДЕЛЬ Sale от related_name="sales"
        Sale = self.sales.model

        sales_qs = Sale.objects.filter(shift_id=self.id, status=Sale.Status.PAID)

        # 🔥 берём колонку БД напрямую, игнорируя любые annotate(total=...)
        table = connection.ops.quote_name(Sale._meta.db_table)
        col_total = connection.ops.quote_name("total")
        total_col = RawSQL(f"{table}.{col_total}", [])

        sa = sales_qs.aggregate(
            cnt=Count("id"),
            total_sum=Sum(total_col),
            cash_sum=Sum(
                Case(
                    When(payment_method=Sale.PaymentMethod.CASH, then=total_col),
                    default=Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            noncash_sum=Sum(
                Case(
                    When(~Q(payment_method=Sale.PaymentMethod.CASH), then=total_col),
                    default=Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
        )

        income_total = fa["income"] or z
        expense_total = fa["expense"] or z
        sales_count = sa["cnt"] or 0
        sales_total = sa["total_sum"] or z
        cash_sales_total = sa["cash_sum"] or z
        noncash_sales_total = sa["noncash_sum"] or z

        expected_cash = (self.opening_cash or z) + cash_sales_total + income_total - expense_total

        return {
            "income_total": income_total,
            "expense_total": expense_total,
            "sales_count": sales_count,
            "sales_total": sales_total,
            "cash_sales_total": cash_sales_total,
            "noncash_sales_total": noncash_sales_total,
            "expected_cash": expected_cash,
        }

    @property
    def expected_cash(self) -> Decimal:
        # это "кеш-логика": после закрытия будет точно (кеш записан),
        # до закрытия в API мы подменим цифры на live-значения в сериализаторе.
        return (self.opening_cash or 0) + (self.cash_sales_total or 0) + (self.income_total or 0) - (self.expense_total or 0)

    @property
    def cash_diff(self) -> Decimal:
        if self.closing_cash is None:
            return Decimal("0.00")
        return (self.closing_cash or 0) - (self.expected_cash or 0)

    def recalc_totals_for_close(self):
        """
        Кешируем итоги ТОЛЬКО при закрытии.
        """
        t = self.calc_live_totals()
        self.income_total = t["income_total"]
        self.expense_total = t["expense_total"]
        self.sales_count = t["sales_count"]
        self.sales_total = t["sales_total"]
        self.cash_sales_total = t["cash_sales_total"]
        self.noncash_sales_total = t["noncash_sales_total"]

    def close(self, closing_cash: Decimal):
        if self.status != self.Status.OPEN:
            raise ValidationError({"status": "Смена уже закрыта."})

        self.closing_cash = closing_cash
        self.closed_at = timezone.now()

        self.recalc_totals_for_close()

        self.status = self.Status.CLOSED
        self.save(
            update_fields=[
                "closing_cash",
                "closed_at",
                "income_total",
                "expense_total",
                "sales_count",
                "sales_total",
                "cash_sales_total",
                "noncash_sales_total",
                "status",
            ]
        )

    def __str__(self):
        return f"Смена {self.cashier} / {self.cashbox} ({self.status})"


class CashFlow(models.Model):
    class Type(models.TextChoices):
        INCOME = "income", "Приход"
        EXPENSE = "expense", "Расход"

    class Status(models.TextChoices):
        PENDING = "pending", "В ожидании"
        APPROVED = "approved", "Успешно"
        REJECTED = "rejected", "Отклонено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="cash_cashflows", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="cash_cashflows",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )

    cashbox = models.ForeignKey(Cashbox, on_delete=models.CASCADE, related_name="flows", verbose_name="Касса")
    type = models.CharField(max_length=10, choices=Type.choices, verbose_name="Тип")
    name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Наименование")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма")
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    source_cashbox_flow_id = models.CharField(max_length=36, null=True, blank=True)
    source_business_operation_id = models.CharField(max_length=36, null=True, blank=True)

    shift = models.ForeignKey(
        "construction.CashShift",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shift_flows",
        db_index=True,
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_flows",
    )

    class Meta:
        verbose_name = "Движение по кассе"
        verbose_name_plural = "Движения по кассе"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "branch", "created_at"]),
            models.Index(fields=["cashbox", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["cashbox", "status", "type", "created_at"], name="ix_flow_cb_stat_type_created"),
            models.Index(fields=["shift", "created_at"]),
            models.Index(fields=["cashier", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="ck_cashflow_amount_positive"),
        ]

    def clean(self):
        if self.cashbox_id:
            if self.company_id != self.cashbox.company_id:
                raise ValidationError({"company": "Компания движения должна совпадать с компанией кассы."})
            if (self.branch_id or None) != (self.cashbox.branch_id or None):
                raise ValidationError({"branch": "Филиал движения должен совпадать с филиалом кассы (или оба None)."})

        if self.shift_id:
            if self.shift.cashbox_id != self.cashbox_id:
                raise ValidationError({"shift": "Смена относится к другой кассе."})
            if self.cashier_id and self.cashier_id != self.shift.cashier_id:
                raise ValidationError({"cashier": "Кассир не совпадает с кассиром смены."})

    def save(self, *args, **kwargs):
        if self.cashbox_id:
            self.company_id = self.cashbox.company_id
            self.branch_id = self.cashbox.branch_id

        if self.shift_id:
            self.cashier_id = self.shift.cashier_id

        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} ({self.status})"
