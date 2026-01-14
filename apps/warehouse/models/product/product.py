from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction, connection
from django.core.exceptions import ValidationError
from django.db.models import Q, Max, IntegerField
from django.db.models.functions import Cast

from apps.warehouse.models.base import BaseModelId, BaseModelDate, BaseModelCompanyBranch


QTY3 = Decimal("0.001")
MONEY = Decimal("0.01")


def q_money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(MONEY, rounding=ROUND_HALF_UP)


def q_qty(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(QTY3, rounding=ROUND_HALF_UP)


class WarehouseProduct(BaseModelId, BaseModelDate, BaseModelCompanyBranch):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидание"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отказ"

    class Kind(models.TextChoices):
        PRODUCT = "product", "Товар"
        SERVICE = "service", "Услуга"
        BUNDLE = "bundle", "Комплект"

    brand = models.ForeignKey(
        "warehouse.WarehouseProductBrand",
        on_delete=models.SET_NULL,
        verbose_name="Бренд",
        null=True,
        blank=True,
    )

    category = models.ForeignKey(
        "warehouse.WarehouseProductCategory",
        on_delete=models.CASCADE,
        verbose_name="Категория",
    )

    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        verbose_name="Склад",
        related_name="products",
    )

    article = models.CharField("Артикул", max_length=64, blank=True)
    name = models.CharField("Название", max_length=255)
    description = models.TextField("Описание", blank=True, null=True)

    barcode = models.CharField("Штрихкод", max_length=64, null=True, blank=True)

    code = models.CharField(
        "Код товара",
        max_length=32,
        blank=True,
        null=True,
        db_index=True,
        help_text="Автогенерация, если не указан. Уникален в рамках склада.",
    )

    unit = models.CharField(
        "Единица измерения",
        max_length=32,
        default="шт.",
        help_text="Вводится вручную: шт., кг, м, упак., л и т.д.",
    )

    is_weight = models.BooleanField(
        "Весовой товар",
        default=False,
        help_text="Если товар продаётся по весу (обычно кг).",
    )

    quantity = models.DecimalField(
        "Количество",
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
    )

    purchase_price = models.DecimalField(
        "Цена закупки",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    markup_percent = models.DecimalField(
        "Наценка, %",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    price = models.DecimalField(
        "Цена продажи",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Считается автоматически из закупки и наценки (если наценка > 0).",
    )

    discount_percent = models.DecimalField(
        "Скидка, %",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    plu = models.PositiveIntegerField(
        "ПЛУ",
        blank=True,
        null=True,
        help_text="Номер ПЛУ для весов (можно не заполнять). Уникален в рамках склада.",
    )

    country = models.CharField("Страна происхождения", max_length=64, blank=True)

    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        db_index=True,
        blank=True,
        null=True,
    )

    stock = models.BooleanField("Акционный товар", default=False)

    expiration_date = models.DateField("Срок годности", null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        indexes = [
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["company", "warehouse", "status"]),
            models.Index(fields=["company", "warehouse", "plu"]),
            models.Index(fields=["company", "warehouse", "barcode"]),
        ]
        constraints = [
            # ✅ один и тот же barcode может быть в разных складах,
            # но в одном складе — уникален (если заполнен)
            models.UniqueConstraint(
                fields=("company", "warehouse", "barcode"),
                condition=Q(barcode__isnull=False) & ~Q(barcode=""),
                name="uq_wh_company_warehouse_barcode_not_empty",
            ),
            # ✅ code уникален в рамках склада (если заполнен)
            models.UniqueConstraint(
                fields=("company", "warehouse", "code"),
                condition=Q(code__isnull=False) & ~Q(code=""),
                name="uq_wh_company_warehouse_code_not_empty",
            ),
            # ✅ ПЛУ уникален в рамках склада (если заполнен)
            models.UniqueConstraint(
                fields=("company", "warehouse", "plu"),
                condition=Q(plu__isnull=False),
                name="uq_wh_company_warehouse_plu_not_null",
            ),
        ]

    def _pg_lock_company(self):
        """
        Защита от гонок при генерации max()+1.
        Лочим на уровне компании (достаточно, чтобы два потока не сгенерили одинаковые значения).
        """
        if not self.company_id:
            return

        key = int(str(self.company_id).replace("-", "")[:16], 16) & 0x7FFFFFFFFFFFFFFF
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s::bigint);", [key])

    def _auto_generate_plu(self):
        if not self.is_weight:
            return
        if self.plu is not None or not self.company_id or not self.warehouse_id:
            return

        max_plu = (
            WarehouseProduct.objects.filter(
                company_id=self.company_id,
                warehouse_id=self.warehouse_id,
                plu__isnull=False,
            )
            .aggregate(m=Max("plu"))
            .get("m")
            or 0
        )
        self.plu = max_plu + 1

    def _auto_generate_code(self):
        if self.code or not self.company_id or not self.warehouse_id:
            return

        qs = (
            WarehouseProduct.objects.filter(company_id=self.company_id, warehouse_id=self.warehouse_id)
            .exclude(code__isnull=True)
            .exclude(code__exact="")
            .filter(code__regex=r"^\d+$")
            .annotate(code_int=Cast("code", IntegerField()))
        )
        last_num = qs.aggregate(max_num=Max("code_int"))["max_num"] or 0
        self.code = f"{last_num + 1:04d}"

    def _recalc_price(self):
        base = Decimal(self.purchase_price or 0)
        percent = Decimal(self.markup_percent or 0)

        # если наценка 0 — цену ведут руками
        if percent == Decimal("0"):
            self.price = q_money(Decimal(self.price or 0))
            return

        result = base * (Decimal("1") + percent / Decimal("100"))
        self.price = q_money(result)

    def clean(self):
        super().clean()

        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        # brand/category — должны быть той же компании (и филиала, если применимо)
        for rel, name in [(self.brand, "brand"), (self.category, "category")]:
            if rel and getattr(rel, "company_id", None) and rel.company_id != self.company_id:
                raise ValidationError({name: "Объект принадлежит другой компании."})
            if self.branch_id and rel and getattr(rel, "branch_id", None) not in (None, self.branch_id):
                raise ValidationError({name: "Объект другого филиала."})

        if self.discount_percent is not None:
            dp = Decimal(self.discount_percent)
            if not (Decimal("0") <= dp <= Decimal("100")):
                raise ValidationError({"discount_percent": "Скидка должна быть от 0 до 100%."})

        if self.quantity is not None and Decimal(self.quantity) < 0:
            raise ValidationError({"quantity": "Количество не может быть отрицательным."})

    def save(self, *args, **kwargs):
        self._recalc_price()
        self.quantity = q_qty(Decimal(self.quantity or 0))

        with transaction.atomic():
            self._pg_lock_company()
            self._auto_generate_code()
            self._auto_generate_plu()
            return super().save(*args, **kwargs)
