import uuid
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from django.db import models, transaction, connection
from django.conf import settings
from django.db.models import Q, Max, IntegerField
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

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
    name = models.CharField(max_length=128, verbose_name="Название", null=True, blank=True)
    location = models.TextField(verbose_name="Локация", blank=False)

    class Status(models.TextChoices):
        active = "active", "Активен"
        inactive = "inactive", "Неактивен"

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.inactive,
        verbose_name="Статус"
    )

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"


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
    name = models.CharField(max_length=128, verbose_name="Название")

    parent = TreeForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='children', verbose_name='Родительская категория')

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
# Warehouse product group (grouping inside warehouse, like 1C)
# -----------------------


class WarehouseProductGroup(BaseModelId, BaseModelCompanyBranch):
    """
    Группа товаров внутри склада (иерархия как в 1С).
    Одна группа может содержать подгруппы и/или товары.
    """
    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        related_name="product_groups",
        verbose_name="Склад",
    )
    name = models.CharField(max_length=128, verbose_name="Название")

    parent = TreeForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Родительская группа",
    )

    class MPTTMeta:
        order_insertion_by = ["name"]

    class Meta:
        verbose_name = "Группа товаров (склад)"
        verbose_name_plural = "Группы товаров (склад)"
        constraints = [
            models.UniqueConstraint(
                fields=("warehouse", "name"),
                condition=Q(parent__isnull=True),
                name="uq_wh_product_group_root_name_per_warehouse",
            ),
            models.UniqueConstraint(
                fields=("parent", "name"),
                condition=Q(parent__isnull=False),
                name="uq_wh_product_group_child_name_per_parent",
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse"]),
            models.Index(fields=["company", "warehouse"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании."})
        if self.branch_id and self.warehouse_id and self.warehouse.branch_id not in (None, self.branch_id):
            raise ValidationError({"warehouse": "Склад другого филиала."})
        if self.parent_id:
            if self.parent.warehouse_id != self.warehouse_id:
                raise ValidationError({"parent": "Родительская группа должна принадлежать тому же складу."})
            if self.parent.company_id != self.company_id:
                raise ValidationError({"parent": "Родительская группа другой компании."})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({"parent": "Родительская группа другого филиала."})

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            if not self.company_id:
                self.company_id = self.warehouse.company_id
            if self.branch_id is None:
                self.branch_id = self.warehouse.branch_id
        super().save(*args, **kwargs)


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

    product_group = models.ForeignKey(
        "warehouse.WarehouseProductGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="Группа (склад)",
        help_text="Группировка товаров внутри склада (как в 1С).",
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
        # Use PostgreSQL advisory locks when available. Skip for SQLite/other backends.
        try:
            vendor = connection.vendor
        except Exception:
            vendor = None

        if vendor != "postgresql":
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

        if self.product_group_id and self.warehouse_id:
            if self.product_group.warehouse_id != self.warehouse_id:
                raise ValidationError({"product_group": "Группа должна принадлежать тому же складу, что и товар."})

        if self.discount_percent is not None:
            dp = Decimal(self.discount_percent)
            if not (Decimal("0") <= dp <= Decimal("100")):
                raise ValidationError({"discount_percent": "Скидка должна быть от 0 до 100%."})

        if self.quantity is not None and Decimal(self.quantity) < 0:
            raise ValidationError({"quantity": "Количество не может быть отрицательным."})

    def save(self, *args, **kwargs):
        # Инвалидация кэша при изменении barcode или plu
        old_barcode = None
        old_plu = None
        if self.pk:
            try:
                old_instance = WarehouseProduct.objects.get(pk=self.pk)
                old_barcode = old_instance.barcode
                old_plu = old_instance.plu
            except WarehouseProduct.DoesNotExist:
                pass
        
        self._recalc_price()
        self.quantity = q_qty(Decimal(self.quantity or 0))

        with transaction.atomic():
            self._pg_lock_company()
            self._auto_generate_code()
            self._auto_generate_plu()
            result = super().save(*args, **kwargs)
            
            # Инвалидация кэша после сохранения
            from django.core.cache import cache
            if old_barcode and old_barcode != self.barcode:
                cache_key = f"warehouse_product_barcode:{self.company_id}:{old_barcode}"
                cache.delete(cache_key)
            if self.barcode:
                cache_key = f"warehouse_product_barcode:{self.company_id}:{self.barcode}"
                cache.delete(cache_key)
            if old_plu and old_plu != self.plu:
                cache_key = f"warehouse_product_plu:{self.company_id}:{self.warehouse_id}:{old_plu}"
                cache.delete(cache_key)
            if self.plu:
                cache_key = f"warehouse_product_plu:{self.company_id}:{self.warehouse_id}:{self.plu}"
                cache.delete(cache_key)
            
            return result


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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

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


# -----------------------
# Documents / stock models
# -----------------------


class StockBalance(models.Model):
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.CASCADE, related_name="balances", verbose_name="Склад")
    product = models.ForeignKey("warehouse.WarehouseProduct", on_delete=models.CASCADE, related_name="balances", verbose_name="Товар")
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0.000"), verbose_name="Количество")

    class Meta:
        verbose_name = "Остаток на складе"
        verbose_name_plural = "Остатки на складах"
        unique_together = ("warehouse", "product")
        indexes = [models.Index(fields=["warehouse", "product"]) ]

    def __str__(self):
        return f"Balance {self.warehouse} / {self.product} = {self.qty}"


class AgentStockBalance(BaseModelId, BaseModelCompanyBranch):
    """
    Остатки товаров на руках у агента.
    """
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="warehouse_agent_balances")
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.CASCADE, related_name="agent_balances")
    product = models.ForeignKey("warehouse.WarehouseProduct", on_delete=models.CASCADE, related_name="agent_balances")
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0.000"), verbose_name="Количество")

    class Meta:
        verbose_name = "Остаток у агента"
        verbose_name_plural = "Остатки у агентов"
        unique_together = ("agent", "warehouse", "product")
        indexes = [
            models.Index(fields=["company", "agent", "warehouse"]),
            models.Index(fields=["company", "agent", "product"]),
        ]

    def __str__(self):
        return f"AgentBalance {self.agent_id} / {self.product_id} = {self.qty}"

    def clean(self):
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании."})
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})
        if self.branch_id and self.warehouse_id and self.warehouse.branch_id not in (None, self.branch_id):
            raise ValidationError({"warehouse": "Склад другого филиала."})
        if self.branch_id and self.product_id and self.product.branch_id not in (None, self.branch_id):
            raise ValidationError({"product": "Товар другого филиала."})


class AgentStockMove(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey("warehouse.Document", on_delete=models.CASCADE, related_name="agent_moves", verbose_name="Документ")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="warehouse_agent_moves")
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.CASCADE, verbose_name="Склад")
    product = models.ForeignKey("warehouse.WarehouseProduct", on_delete=models.CASCADE, verbose_name="Товар")
    qty_delta = models.DecimalField(max_digits=18, decimal_places=3, verbose_name="Изменение количества")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Движение товара у агента"
        verbose_name_plural = "Движения товаров у агентов"
        indexes = [models.Index(fields=["agent", "warehouse", "product", "created_at"])]

    def __str__(self):
        return f"AgentMove {self.document.number} {self.product} {self.qty_delta} @ {self.agent_id}"


class Counterparty(models.Model):
    class Type(models.TextChoices):
        CLIENT = "CLIENT", "Клиент"
        SUPPLIER = "SUPPLIER", "Поставщик"
        BOTH = "BOTH", "Оба"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "users.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Компания",
    )
    branch = models.ForeignKey(
        "users.Branch",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="warehouse_counterparties",
        verbose_name="Агент",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.BOTH, verbose_name="Тип")

    class Meta:
        verbose_name = "Контрагент"
        verbose_name_plural = "Контрагенты"
        indexes = [
            models.Index(fields=["company", "branch"]),
            models.Index(fields=["company", "agent"]),
            models.Index(fields=["agent", "name"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})
        if self.agent_id and self.company_id and getattr(self.agent, "company_id", None) not in (None, self.company_id):
            raise ValidationError({"agent": "Агент принадлежит другой компании."})


class DocumentSequence(models.Model):
    doc_type = models.CharField(max_length=32, verbose_name="Тип документа")
    date = models.DateField(verbose_name="Дата")
    seq = models.PositiveIntegerField(default=0, verbose_name="Последовательность")

    class Meta:
        verbose_name = "Последовательность документов"
        verbose_name_plural = "Последовательности документов"
        unique_together = (("doc_type", "date"),)


class Document(models.Model):
    class DocType(models.TextChoices):
        SALE = "SALE", "Продажа"
        PURCHASE = "PURCHASE", "Покупка"
        SALE_RETURN = "SALE_RETURN", "Возврат продажи"
        PURCHASE_RETURN = "PURCHASE_RETURN", "Возврат покупки"
        INVENTORY = "INVENTORY", "Инвентаризация"
        RECEIPT = "RECEIPT", "Приход"
        WRITE_OFF = "WRITE_OFF", "Списание"
        TRANSFER = "TRANSFER", "Перемещение"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Черновик"
        POSTED = "POSTED", "Проведен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doc_type = models.CharField(max_length=32, choices=DocType.choices, verbose_name="Тип документа")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, verbose_name="Статус")
    number = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name="Номер")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    warehouse_from = models.ForeignKey("warehouse.Warehouse", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents_from", verbose_name="Склад-источник")
    warehouse_to = models.ForeignKey("warehouse.Warehouse", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents_to", verbose_name="Склад-приемник")
    counterparty = models.ForeignKey("warehouse.Counterparty", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Контрагент")
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="warehouse_documents",
        verbose_name="Агент",
    )

    comment = models.TextField(blank=True, verbose_name="Комментарий")
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="Итого")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"

    def __str__(self):
        return f"{self.number} ({self.doc_type})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.doc_type == self.DocType.TRANSFER:
            if not self.warehouse_from or not self.warehouse_to:
                raise ValidationError("TRANSFER requires both warehouse_from and warehouse_to")
            if self.warehouse_from_id == self.warehouse_to_id:
                raise ValidationError("warehouse_from and warehouse_to must be different")
            if self.warehouse_from and self.warehouse_to:
                if self.warehouse_from.company_id != self.warehouse_to.company_id:
                    raise ValidationError("TRANSFER requires warehouses from the same company")

        if self.agent_id:
            if self.doc_type in (self.DocType.TRANSFER, self.DocType.INVENTORY):
                raise ValidationError("Agent documents cannot be TRANSFER or INVENTORY")
            if not self.warehouse_from:
                raise ValidationError("Agent document requires warehouse_from")
            if getattr(self.agent, "company_id", None) and self.warehouse_from:
                if self.agent.company_id != self.warehouse_from.company_id:
                    raise ValidationError("Agent belongs to another company")
            if self.counterparty_id:
                cp_agent_id = getattr(self.counterparty, "agent_id", None)
                if cp_agent_id != self.agent_id:
                    raise ValidationError({"counterparty": "Контрагент не принадлежит агенту."})

        if self.doc_type in (self.DocType.SALE, self.DocType.SALE_RETURN, self.DocType.PURCHASE, self.DocType.PURCHASE_RETURN, self.DocType.RECEIPT, self.DocType.WRITE_OFF):
            # require warehouse_from for most operations (warehouse where stock changes).
            if not self.warehouse_from:
                raise ValidationError("Document requires warehouse_from")
            if self.doc_type in (self.DocType.SALE, self.DocType.PURCHASE, self.DocType.SALE_RETURN, self.DocType.PURCHASE_RETURN) and not self.counterparty:
                raise ValidationError("Document requires counterparty")


class DocumentItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="items", verbose_name="Документ")
    product = models.ForeignKey("warehouse.WarehouseProduct", on_delete=models.PROTECT, verbose_name="Товар")
    qty = models.DecimalField(max_digits=18, decimal_places=3, verbose_name="Количество")
    price = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="Цена")
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), verbose_name="Скидка, %")
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="Итого по строке")

    class Meta:
        verbose_name = "Строка документа"
        verbose_name_plural = "Строки документов"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.qty is None or Decimal(self.qty) <= Decimal("0"):
            raise ValidationError({"qty": "Quantity must be > 0"})

        if self.discount_percent is None:
            self.discount_percent = Decimal("0.00")
        dp = Decimal(self.discount_percent)
        if not (Decimal("0") <= dp <= Decimal("100")):
            raise ValidationError({"discount_percent": "Must be between 0 and 100"})

        # Проверка соответствия товара складу документа
        # Проверяем только если document уже сохранен (имеет pk) или передан напрямую
        doc = getattr(self, 'document', None)
        if doc and self.product_id:
            # Если document еще не сохранен, но передан - проверяем по ID
            if hasattr(doc, 'pk') and doc.pk is None:
                # Документ еще не сохранен - проверяем по warehouse_from_id напрямую
                if hasattr(doc, 'warehouse_from_id') and doc.warehouse_from_id:
                    prod = self.product
                    if doc.doc_type == doc.DocType.TRANSFER:
                        if prod.warehouse_id != doc.warehouse_from_id:
                            raise ValidationError({"product": "Товар должен принадлежать складу-источнику перемещения."})
                    else:
                        if prod.warehouse_id != doc.warehouse_from_id:
                            raise ValidationError({"product": "Товар должен принадлежать складу документа."})
            elif hasattr(doc, 'pk') and doc.pk:
                # Документ сохранен - полная проверка
                prod = self.product
                
                # Проверка компании
                if hasattr(doc, 'warehouse_from') and doc.warehouse_from_id:
                    # Загружаем warehouse_from если нужно
                    if not hasattr(doc.warehouse_from, 'company_id'):
                        doc.warehouse_from.refresh_from_db()
                    if prod.company_id != doc.warehouse_from.company_id:
                        raise ValidationError({"product": "Товар принадлежит другой компании, чем склад документа."})
                
                # Проверка склада для операций с одним складом
                if doc.doc_type != doc.DocType.TRANSFER:
                    if doc.warehouse_from_id and prod.warehouse_id != doc.warehouse_from_id:
                        warehouse_name = doc.warehouse_from.name if hasattr(doc, 'warehouse_from') and doc.warehouse_from else "документа"
                        raise ValidationError({"product": f"Товар должен принадлежать складу '{warehouse_name}'."})
                else:
                    # Для TRANSFER товар должен принадлежать складу-источнику
                    if doc.warehouse_from_id and prod.warehouse_id != doc.warehouse_from_id:
                        raise ValidationError({"product": "Товар должен принадлежать складу-источнику перемещения."})

            if doc.agent_id and doc.warehouse_from_id:
                has_balance = AgentStockBalance.objects.filter(
                    agent_id=doc.agent_id,
                    warehouse_id=doc.warehouse_from_id,
                    product_id=self.product_id,
                ).exists()
                if not has_balance:
                    raise ValidationError({"product": "Товар отсутствует в остатках агента."})

        # integral check for PCS
        try:
            unit = self.product.unit if self.product_id else None
        except Exception:
            unit = None

        if unit and unit.lower().startswith("pcs") or (self.product_id and getattr(self.product, "is_weight", False) is False):
            # treat as pieces: require integer qty
            if (Decimal(self.qty) % 1) != 0:
                raise ValidationError({"qty": "Quantity must be integer for piece items"})

    def save(self, *args, **kwargs):
        # compute line total: price * qty * (1 - discount)
        q = Decimal(self.qty or 0)
        p = Decimal(self.price or 0)
        dp = Decimal(self.discount_percent or 0) / Decimal("100")
        self.line_total = (p * q * (Decimal("1") - dp)).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class AgentRequestCart(BaseModelId, BaseModelDate, BaseModelCompanyBranch):
    """
    Заявка агента на получение товара со склада.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SUBMITTED = "submitted", "Отправлено владельцу"
        APPROVED = "approved", "Одобрено и выдано"
        REJECTED = "rejected", "Отклонено"

    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="warehouse_agent_carts",
        verbose_name="Агент",
    )
    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="agent_request_carts",
        verbose_name="Склад",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    note = models.CharField(max_length=255, blank=True, verbose_name="Комментарий агента")

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="warehouse_approved_agent_carts",
        verbose_name="Кем одобрено",
    )

    class Meta:
        verbose_name = "Заявка агента (склад)"
        verbose_name_plural = "Заявки агентов (склад)"
        ordering = ["-created_date"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["agent", "status"]),
        ]

    def __str__(self):
        return f"Заявка {self.id} от {getattr(self.agent, 'username', self.agent_id)} [{self.get_status_display()}]"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Склад принадлежит другой компании."})
        if self.branch_id and self.warehouse_id and self.warehouse.branch_id not in (None, self.branch_id):
            raise ValidationError({"warehouse": "Склад другого филиала."})
        if self.agent_id and getattr(self.agent, "company_id", None) not in (None, self.company_id):
            raise ValidationError({"agent": "Агент принадлежит другой компании."})

    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    @transaction.atomic
    def submit(self):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Можно отправить только черновик.")
        if not self.items.exists():
            raise ValidationError("Нельзя отправить пустую заявку.")
        self.status = self.Status.SUBMITTED
        self.submitted_at = timezone.now()
        self.full_clean()
        self.save(update_fields=["status", "submitted_at", "updated_date"])

    @transaction.atomic
    def approve(self, by_user):
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Можно одобрить только заявку в статусе 'submitted'.")
        if not self.items.exists():
            raise ValidationError("Нельзя одобрить пустую заявку.")

        for it in self.items.select_related("product"):
            prod = it.product
            need_qty = q_qty(Decimal(it.quantity_requested or 0))
            if need_qty <= 0:
                continue

            bal, created = StockBalance.objects.select_for_update().get_or_create(
                warehouse=self.warehouse,
                product=prod,
                defaults={"qty": Decimal("0.000")},
            )
            if created and prod.warehouse_id == self.warehouse_id:
                bal.qty = Decimal(prod.quantity or 0)

            cur_qty = Decimal(bal.qty or 0)
            if cur_qty < need_qty:
                raise ValidationError({
                    "items": f"Недостаточно на складе для {prod.name}: нужно {need_qty}, доступно {cur_qty}."
                })

            bal.qty = cur_qty - need_qty
            bal.save(update_fields=["qty"])
            if prod.warehouse_id == self.warehouse_id:
                type(prod).objects.filter(pk=prod.pk).update(quantity=q_qty(bal.qty))

            stock, _ = AgentStockBalance.objects.select_for_update().get_or_create(
                agent=self.agent,
                warehouse=self.warehouse,
                product=prod,
                defaults={
                    "qty": Decimal("0.000"),
                    "company": self.company,
                    "branch": self.branch,
                },
            )
            stock.qty = q_qty(Decimal(stock.qty or 0) + need_qty)
            stock.save(update_fields=["qty"])

        self.status = self.Status.APPROVED
        self.approved_at = timezone.now()
        self.approved_by = by_user
        self.full_clean()
        self.save(update_fields=["status", "approved_at", "approved_by", "updated_date"])

    @transaction.atomic
    def reject(self, by_user):
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Можно отклонить только заявку в статусе 'submitted'.")
        self.status = self.Status.REJECTED
        self.approved_at = timezone.now()
        self.approved_by = by_user
        self.full_clean()
        self.save(update_fields=["status", "approved_at", "approved_by", "updated_date"])


class AgentRequestItem(BaseModelId, BaseModelDate, BaseModelCompanyBranch):
    cart = models.ForeignKey(
        AgentRequestCart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Заявка",
    )
    product = models.ForeignKey(
        "warehouse.WarehouseProduct",
        on_delete=models.PROTECT,
        related_name="agent_request_items",
        verbose_name="Товар",
    )
    quantity_requested = models.DecimalField(max_digits=18, decimal_places=3, verbose_name="Запрошено")

    class Meta:
        verbose_name = "Позиция заявки агента (склад)"
        verbose_name_plural = "Позиции заявок агента (склад)"
        indexes = [
            models.Index(fields=["cart", "product"]),
        ]

    def __str__(self):
        return f"{self.cart_id} · {self.product_id} · {self.quantity_requested}"

    def clean(self):
        if self.quantity_requested is None or Decimal(self.quantity_requested) <= 0:
            raise ValidationError({"quantity_requested": "Количество должно быть больше 0."})

        if self.cart_id and self.cart.status != AgentRequestCart.Status.DRAFT:
            raise ValidationError({"cart": "Нельзя редактировать позиции, когда заявка не в черновике."})

        if self.cart_id and self.product_id:
            if self.cart.company_id and self.product.company_id != self.cart.company_id:
                raise ValidationError({"product": "Товар другой компании."})
            if self.cart.branch_id and self.product.branch_id not in (None, self.cart.branch_id):
                raise ValidationError({"product": "Товар другого филиала."})
            if self.cart.warehouse_id and self.product.warehouse_id != self.cart.warehouse_id:
                raise ValidationError({"product": "Товар должен принадлежать выбранному складу."})

    def save(self, *args, **kwargs):
        if self.cart_id:
            if not self.company_id:
                self.company_id = self.cart.company_id
            if self.branch_id is None:
                self.branch_id = self.cart.branch_id
        super().save(*args, **kwargs)


class StockMove(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="moves", verbose_name="Документ")
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.CASCADE, verbose_name="Склад")
    product = models.ForeignKey("warehouse.WarehouseProduct", on_delete=models.CASCADE, verbose_name="Товар")
    qty_delta = models.DecimalField(max_digits=18, decimal_places=3, verbose_name="Изменение количества")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Движение товара"
        verbose_name_plural = "Движения товаров"
        indexes = [models.Index(fields=["warehouse", "product", "created_at"])]

    def __str__(self):
        return f"Move {self.document.number} {self.product} {self.qty_delta} @ {self.warehouse}"


# -----------------------
# Money documents
# -----------------------


class PaymentCategory(BaseModelId, BaseModelCompanyBranch):
    """
    Категория платежа для денежных документов (приход/расход).
    """

    title = models.CharField(max_length=255, verbose_name="Название")

    class Meta:
        verbose_name = "Категория платежа"
        verbose_name_plural = "Категории платежей"
        constraints = [
            models.UniqueConstraint(
                fields=("branch", "title"),
                name="uq_wh_payment_category_title_per_branch",
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "title"),
                name="uq_wh_payment_category_title_global_per_company",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "title"]),
            models.Index(fields=["company", "branch", "title"]),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})


class MoneyDocument(BaseModelCompanyBranch):
    """
    Денежные документы:
    - MONEY_RECEIPT: получаем деньги от контрагента
    - MONEY_EXPENSE: отправляем деньги контрагенту

    В отличие от товарных документов, здесь нет items и нет StockMove.
    """

    class DocType(models.TextChoices):
        MONEY_RECEIPT = "MONEY_RECEIPT", "Приход"
        MONEY_EXPENSE = "MONEY_EXPENSE", "Расход"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Черновик"
        POSTED = "POSTED", "Проведен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doc_type = models.CharField(max_length=32, choices=DocType.choices, verbose_name="Тип документа")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, verbose_name="Статус")
    number = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name="Номер")
    date = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="money_documents",
        verbose_name="Счёт (склад)",
    )

    counterparty = models.ForeignKey(
        "warehouse.Counterparty",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="money_documents",
        verbose_name="Контрагент",
    )

    payment_category = models.ForeignKey(
        "warehouse.PaymentCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="money_documents",
        verbose_name="Категория платежа",
    )

    amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    comment = models.TextField(blank=True, verbose_name="Комментарий")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Денежный документ"
        verbose_name_plural = "Денежные документы"
        indexes = [
            models.Index(fields=["doc_type", "status", "date"]),
            models.Index(fields=["counterparty", "date"]),
            models.Index(fields=["warehouse", "date"]),
            models.Index(fields=["payment_category", "date"]),
        ]

    def __str__(self):
        return f"{self.number or self.id} ({self.doc_type})"

    def clean(self):
        super().clean()
        # warehouse is required for both money operations (account)
        if not self.warehouse_id:
            raise ValidationError({"warehouse": "Укажите счёт (склад)."})

        if self.doc_type in (self.DocType.MONEY_RECEIPT, self.DocType.MONEY_EXPENSE):
            if not self.counterparty_id:
                raise ValidationError({"counterparty": "Укажите контрагента."})
            if not self.payment_category_id:
                raise ValidationError({"payment_category": "Укажите категорию платежа."})

        if self.amount is None or Decimal(self.amount) <= 0:
            raise ValidationError({"amount": "Сумма должна быть больше 0."})

        # company/branch consistency (based on warehouse)
        if self.company_id and self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        if self.warehouse_id:
            wh = self.warehouse
            if wh and self.company_id and wh.company_id != self.company_id:
                raise ValidationError({"warehouse": "Склад принадлежит другой компании."})