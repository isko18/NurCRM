from apps.warehouse.models.base import (
    BaseModelId,BaseModelDate,BaseModelCompanyBranch
)

from django.db import models

from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.db.models import Q, Max, IntegerField
from django.db.models.functions import Cast

from decimal import Decimal, ROUND_HALF_UP

class WarehouseProduct(BaseModelId,BaseModelDate,BaseModelCompanyBranch):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидание"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отказ"
 

    class Kind(models.TextChoices):
        PRODUCT = "product", "Товар"
        SERVICE = "service", "Услуга"
        BUNDLE = "bundle", "Комплект"

    # Бренды 
    brand = models.ForeignKey(
        "warehouse.WarehouseProductBrand",
        on_delete=models.SET_NULL,
        verbose_name="Бренд ",null=True,blank=True
    )

    # Категории
    category = models.ForeignKey(
       "warehouse.WarehouseProductCategory",
       on_delete=models.CASCADE,
       verbose_name="Категория "
    )

    # склад в котором находится товар
    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        verbose_name="Склад",related_name="products"
    )

    # Информация 
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
    )

    # ---- Единица и весовой товар ----
    unit = models.CharField(
        "Единица измерения",
        max_length=32,
        default="шт.",
        help_text="Вводится вручную: шт., кг, м, упак., л и т.д.",
    )
    
    is_weight = models.BooleanField(
        "Весовой товар",
        default=False,
        help_text="Если товар продаётся по весу (обычно кг)",
    )

    quantity = models.DecimalField(
        "Цена закупки",
        max_digits=12,
        decimal_places=2,
        default=0, null=True, blank=True
    )

    # ---- Цены / наценка / скидка ----
    purchase_price = models.DecimalField(
        "Цена закупки",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    markup_percent = models.DecimalField(
        "Наценка, %",
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Наценка в процентах к закупочной цене",
    )
    price = models.DecimalField(
        "Цена продажи",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Считается автоматически из закупки и наценки",
    )
    discount_percent = models.DecimalField(
        "Скидка, %",
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Скидка в процентах от цены продажи",
    )

    # ---- ПЛУ для весов ----
    plu = models.PositiveIntegerField(
        "ПЛУ",
        blank=True,
        null=True,
        help_text="Номер ПЛУ для весов (можно не заполнять)",
    )

    # ---- Страна и прочее ----
    country = models.CharField(
        "Страна происхождения",
        max_length=64,
        blank=True,
        help_text="Например: Россия, Китай, Кыргызстан",
    )
    
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        db_index=True,
        blank=True,
        null=True,
    )

    # ✅ фикс: без null=True
    stock = models.BooleanField("Акционный товар", default=False)

    # Cрок годности
    expiration_date = models.DateField("Срок годности", null=True, blank=True)
    
    

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Товар"
        verbose_name = "Товары"

        indexes = [
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["company", "plu"]),
        ]
        constraints = [
            # ✅ штрихкод уникален в рамках компании, только если задан и не пустой
            models.UniqueConstraint(
                fields=("company", "barcode"),
                condition=Q(barcode__isnull=False) & ~Q(barcode=""),
                name="uq_warehouse_company_barcode_not_empty",
            ),
            # код товара уникален в рамках компании, если указан и не пустой
            models.UniqueConstraint(
                fields=("company", "code"),
                condition=Q(code__isnull=False) & ~Q(code=""),
                name="uq_warehouse_company_code_not_empty",
            ),
            # ПЛУ уникален в рамках компании, если задан
            models.UniqueConstraint(
                fields=("company", "plu"),
                condition=Q(plu__isnull=False),
                name="uq_warehouse_company_plu_not_null",
            ),
        ]

    # ---------- Postgres доп функции ----------
    def _pg_lock_company(self):
        """
        Защита от гонок при генерации max()+1.
        В Postgres pg_advisory_xact_lock принимает BIGINT (int8).
        """
        if not self.company_id:
            return

        # 64-bit key (0..2^63-1)
        key = int(str(self.company_id).replace("-", "")[:16], 16)
        key = key & 0x7FFFFFFFFFFFFFFF  # чтобы точно влез в signed BIGINT

        with connection.cursor() as cur:
            # ЯВНО кастим к BIGINT, чтобы не улетало в numeric
            cur.execute("SELECT pg_advisory_xact_lock(%s::bigint);", [key])
    
    # --------- внутренние методы ---------
    def _auto_generate_plu(self):
        if not self.is_weight:
            return
        if self.plu is not None or not self.company_id:
            return

        max_plu = (
            Product.objects
            .filter(company_id=self.company_id, plu__isnull=False)
            .aggregate(m=Max("plu"))
            .get("m") or 0
        )
        self.plu = max_plu + 1

    def _auto_generate_code(self):
        if self.code or not self.company_id:
            return

        qs = (
            WarehouseProduct.objects
            .filter(company_id=self.company_id)
            .exclude(code__isnull=True)
            .exclude(code__exact="")
            .filter(code__regex=r"^\d+$")
            .annotate(code_int=Cast("code", IntegerField()))
        )

        last_num = qs.aggregate(max_num=Max("code_int"))["max_num"] or 0
        self.code = f"{last_num + 1:04d}"

    def _recalc_price(self):
        base = self.purchase_price or Decimal("0")
        percent = self.markup_percent or Decimal("0")

        # ✅ уважай ручную цену даже если price=0
        if self.price is not None and percent == Decimal("0"):
            self.price = Decimal(self.price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return

        result = base * (Decimal("1") + percent / Decimal("100"))
        self.price = result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        for rel, name in [(self.brand, "brand"), (self.category, "category"), (self.client, "client")]:
            if rel and getattr(rel, "company_id", None) != self.company_id:
                raise ValidationError({name: "Объект принадлежит другой компании."})
            if self.branch_id and rel and getattr(rel, "branch_id", None) not in (None, self.branch_id):
                raise ValidationError({name: "Объект другого филиала."})

        if self.discount_percent is not None and not (Decimal("0") <= self.discount_percent <= Decimal("100")):
            raise ValidationError({"discount_percent": "Скидка должна быть от 0 до 100%."})

    def save(self, *args, **kwargs):
        self._recalc_price()
        with transaction.atomic():
            self._pg_lock_company()
            self._auto_generate_code()
            self._auto_generate_plu()
            super().save(*args, **kwargs)
 



