import uuid
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from django.db import models, transaction, connection
from django.db.models import Q, Max, IntegerField
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from PIL import Image


# -----------------------
# Base / helpers
# -----------------------


class BaseModelId(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BaseModelDate(models.Model):
    created_date = models.DateTimeField(auto_now_add=True, verbose_name="Дата открытия")
    updated_date = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True


class BaseModelCompanyBranch(models.Model):
    company = models.ForeignKey(
        "users.Company",
        on_delete=models.CASCADE,
        verbose_name='Компания'
    )

    branch = models.ForeignKey(
        "users.Branch",
        on_delete=models.CASCADE,
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    class Meta:
        abstract = True


def product_image_upload_to(instance, filename: str) -> str:
    return f"products/{instance.product_id}/{uuid.uuid4().hex}.webp"


# -----------------------
# Warehouse model
# -----------------------


class Warehouse(BaseModelId, BaseModelDate, BaseModelCompanyBranch):
    name = models.CharField(max_length=128, verbose_name="Название ", null=True, blank=True)
    location = models.TextField(verbose_name="Локация", blank=False)

    class Status(models.TextChoices):
        active = "active", "Активен"
        inactive = "inactive", "Неактивен"

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.inactive
    )


# -----------------------
# Brand / Category
# -----------------------


from mptt.models import TreeForeignKey


class WarehouseProductBrand(BaseModelId, BaseModelCompanyBranch):
    name = models.CharField(max_length=128, verbose_name="Название")

    parent = TreeForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='children', verbose_name='Родительский бренд')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_warehouse_brand_name_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_warehouse_brand_name_global_per_company',
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительский бренд другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительский бренд другого филиала.'})


class WarehouseProductCategory(BaseModelId, BaseModelCompanyBranch):
    name = models.CharField(max_length=128, verbose_name="Название ")

    parent = TreeForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='children', verbose_name='Родительская категория ')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Категория товара'
        verbose_name_plural = 'Категории товаров'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_warehouse_category_name_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_warehouse_category_name_global_per_company',
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительская категория другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительская категория другого филиала.'})


# -----------------------
# Product and related
# -----------------------


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
            models.UniqueConstraint(
                fields=("company", "warehouse", "barcode"),
                condition=Q(barcode__isnull=False) & ~Q(barcode=""),
                name="uq_wh_company_warehouse_barcode_not_empty",
            ),
            models.UniqueConstraint(
                fields=("company", "warehouse", "code"),
                condition=Q(code__isnull=False) & ~Q(code=""),
                name="uq_wh_company_warehouse_code_not_empty",
            ),
            models.UniqueConstraint(
                fields=("company", "warehouse", "plu"),
                condition=Q(plu__isnull=False),
                name="uq_wh_company_warehouse_plu_not_null",
            ),
        ]

    def _pg_lock_company(self):
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

        if percent == Decimal("0"):
            self.price = q_money(Decimal(self.price or 0))
            return

        result = base * (Decimal("1") + percent / Decimal("100"))
        self.price = q_money(result)

    def clean(self):
        super().clean()

        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

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


class WarehouseProductCharasteristics(BaseModelId, BaseModelCompanyBranch, BaseModelDate):
    product = models.OneToOneField(
        "warehouse.WarehouseProduct",
        on_delete=models.CASCADE,
        related_name="characteristics",
        verbose_name="Товар",
    )

    height_cm = models.DecimalField("Высота, см", max_digits=8, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField("Ширина, см", max_digits=8, decimal_places=2, null=True, blank=True)
    depth_cm = models.DecimalField("Глубина, см", max_digits=8, decimal_places=2, null=True, blank=True)
    factual_weight_kg = models.DecimalField("Фактический вес, кг", max_digits=8, decimal_places=3, null=True, blank=True)
    description = models.TextField("Описание", blank=True)

    class Meta:
        verbose_name = "Характеристики товара"
        verbose_name_plural = "Характеристики товара"

    def __str__(self):
        return f"Характеристики: {self.product}"

    def clean(self):
        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
        super().save(*args, **kwargs)


class WarehouseProductImage(BaseModelId, BaseModelCompanyBranch):
    product = models.ForeignKey(
        "WarehouseProduct",
        on_delete=models.CASCADE,
        related_name="images", verbose_name="Товар"
    )

    image = models.ImageField(upload_to=product_image_upload_to, null=True, blank=True, verbose_name="Изображение (WebP)")
    alt = models.CharField(max_length=255, blank=True, verbose_name="Alt-текст")
    is_primary = models.BooleanField(default=False, verbose_name="Основное изображение")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото товара"
        verbose_name_plural = "Фото товара"
        constraints = [
            models.UniqueConstraint(
                fields=("product",),
                condition=models.Q(is_primary=True),
                name="uq_warehouse_primary_product_image",
            )
        ]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "branch"]),
            models.Index(fields=["product", "is_primary"]),
        ]

    def __str__(self):
        return f"{self.product.name} — image {self.pk}"

    def clean(self):
        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания изображения должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id not in (None, self.branch_id):
                raise ValidationError({"branch": "Филиал изображения должен совпадать с филиалом товара (или быть глобальным вместе с ним)."})

    def save(self, *args, **kwargs):
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id

        if self.image and hasattr(self.image, "file"):
            try:
                self.image = self._convert_to_webp(self.image)
            except Exception as e:
                raise ValidationError({"image": f"Не удалось конвертировать в WebP: {e}"})

        super().save(*args, **kwargs)

        if self.is_primary:
            (type(self).objects
                .filter(product=self.product, is_primary=True)
                .exclude(pk=self.pk)
                .update(is_primary=False))

    def delete(self, *args, **kwargs):
        storage = self.image.storage if self.image else None
        name = self.image.name if self.image else None
        super().delete(*args, **kwargs)
        if storage and name and storage.exists(name):
            storage.delete(name)

    @staticmethod
    def _convert_to_webp(field_file) -> ContentFile:
        field_file.seek(0)
        im = Image.open(field_file)

        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

        buf = BytesIO()
        im.save(buf, format="WEBP", quality=80, method=6)
        buf.seek(0)

        content = ContentFile(buf.read())
        content.name = f"{uuid.uuid4().hex}.webp"
        return content


class WarehouseProductPackage(BaseModelId, BaseModelCompanyBranch):
    product = models.ForeignKey(
        "warehouse.WarehouseProduct",
        on_delete=models.CASCADE,
        related_name="packages",
        verbose_name="Товар",
    )

    name = models.CharField("Упаковка", max_length=64, help_text="Например: коробка, пачка, блок, рулон")

    quantity_in_package = models.DecimalField(
        "Количество в упаковке", max_digits=10, decimal_places=3, help_text="Сколько базовых единиц в одной упаковке",
    )

    unit = models.CharField("Ед. изм.", max_length=32, blank=True, help_text="Если пусто — берём единицу товара")

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Упаковка товара"
        verbose_name_plural = "Упаковки товара"

    def __str__(self):
        return f"{self.name}: {self.quantity_in_package} {self.unit or self.product.unit}"

    def clean(self):
        if self.quantity_in_package is not None and self.quantity_in_package <= 0:
            raise ValidationError({"quantity_in_package": "Количество в упаковке должно быть больше 0."})

        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
            if not self.unit:
                self.unit = self.product.unit

        super().save(*args, **kwargs)
