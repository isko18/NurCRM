import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

from apps.users.models import Company, Branch
from apps.main.models import ProductBrand, ProductCategory


# 📦 Склад
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="storehouse_warehouses", verbose_name="Компания"
    )
    # глобальный (NULL) или филиальный склад
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="storehouse_warehouses",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    address = models.CharField(max_length=500, verbose_name="Адрес", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ["name"]
        constraints = [
            # имя склада уникально в рамках филиала
            models.UniqueConstraint(
                fields=("branch", "name"),
                name="uq_wh_name_per_branch",
                condition=models.Q(branch__isnull=False),
            ),
            # и отдельно — среди глобальных складов в рамках компании
            models.UniqueConstraint(
                fields=("company", "name"),
                name="uq_wh_name_global_per_company",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "branch", "name"]),
        ]

    def __str__(self):
        return f"{self.name}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})


# 🚚 Поставщик
class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="suppliers", verbose_name="Компания"
    )
    # глобальный (NULL) или филиальный поставщик
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="suppliers",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    name = models.CharField(max_length=255, verbose_name="Название поставщика")
    contact_name = models.CharField(max_length=255, verbose_name="ФИО контакта", blank=True, null=True)
    phone = models.CharField(max_length=20, verbose_name="Телефон", blank=True, null=True)
    address = models.CharField(max_length=255, verbose_name="Адрес", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Поставщик"
        verbose_name_plural = "Поставщики"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=("branch", "name"),
                name="uq_supplier_name_per_branch",
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "name"),
                name="uq_supplier_name_global_per_company",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "branch", "name"]),
        ]

    def __str__(self):
        return f"{self.name}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})


# 🛒 Товар
class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="storehouse_products", verbose_name="Компания"
    )
    # глобальный (NULL) или филиальный товар
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="storehouse_products",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    name = models.CharField(max_length=255, verbose_name="Название товара")
    barcode = models.CharField(max_length=64, blank=True, null=True, verbose_name="Штрих-код")

    brand = models.ForeignKey(
        ProductBrand, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="products", verbose_name="Бренд"
    )
    category = models.ForeignKey(
        ProductCategory, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="products", verbose_name="Категория"
    )

    unit = models.CharField(max_length=32, verbose_name="Ед. изм.", default="шт")
    purchase_price = models.DecimalField(
        max_digits=13, decimal_places=3, verbose_name="Цена закупки",
        default=0, validators=[MinValueValidator(Decimal("0"))]
    )
    selling_price = models.DecimalField(
        max_digits=13, decimal_places=3, verbose_name="Цена продажи",
        default=0, validators=[MinValueValidator(Decimal("0"))]
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["name"]
        constraints = [
            # barcode уникален в пределах филиала
            models.UniqueConstraint(
                fields=("branch", "barcode"),
                name="uq_product_barcode_per_branch",
                condition=models.Q(branch__isnull=False) & models.Q(barcode__isnull=False) & ~models.Q(barcode=""),
            ),
            # и отдельно — среди глобальных товаров в рамках компании
            models.UniqueConstraint(
                fields=("company", "barcode"),
                name="uq_product_barcode_global_per_company",
                condition=models.Q(branch__isnull=True) & models.Q(barcode__isnull=False) & ~models.Q(barcode=""),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "branch", "name"]),
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "branch", "category"]),
            models.Index(fields=["company", "branch", "brand"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})


# 📊 Остатки
class Stock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="stocks", verbose_name="Склад"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stocks", verbose_name="Товар"
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Количество",
        default=0, validators=[MinValueValidator(Decimal("0"))]
    )

    class Meta:
        verbose_name = "Остаток на складе"
        verbose_name_plural = "Остатки на складах"
        constraints = [
            models.UniqueConstraint(fields=("warehouse", "product"), name="uq_stock_wh_product"),
        ]
        indexes = [
            models.Index(fields=["warehouse", "product"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.quantity} {self.product.unit} (на {self.warehouse.name})"

    def clean(self):
        # company: склад и товар одной компании
        if self.product_id and self.warehouse_id:
            if self.product.company_id != self.warehouse.company_id:
                raise ValidationError({"product": "Товар и склад принадлежат разным компаниям."})
            # branch: товар глобальный или того же филиала, что и склад
            if self.warehouse.branch_id and self.product.branch_id not in (None, self.warehouse.branch_id):
                raise ValidationError({"product": "Товар другого филиала, чем склад."})


# 📥 Приход
class StockIn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_ins", verbose_name="Компания"
    )
    # документ глобальный или филиальный (по складу/поставщику)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="stock_ins",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    document_number = models.CharField(max_length=50, verbose_name="№ документа")
    date = models.DateField(verbose_name="Дата")
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="deliveries", verbose_name="Поставщик"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="incoming", verbose_name="Склад"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Приход"
        verbose_name_plural = "Приходы"
        ordering = ["-date", "-id"]
        constraints = [
            # уникальный номер в пределах филиала
            models.UniqueConstraint(
                fields=("company", "branch", "document_number"),
                name="uq_stockin_company_branch_docnum",
                condition=models.Q(branch__isnull=False),
            ),
            # и отдельно — среди глобальных документов компании
            models.UniqueConstraint(
                fields=("company", "document_number"),
                name="uq_stockin_company_docnum_global",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "date"]),
            models.Index(fields=["company", "branch", "date"]),
            models.Index(fields=["company", "branch", "warehouse", "date"]),
            models.Index(fields=["company", "branch", "supplier", "date"]),
        ]

    def __str__(self):
        return f"Приход {self.document_number} от {self.date}"

    def clean(self):
        # company
        if self.supplier and self.supplier.company_id != self.company_id:
            raise ValidationError({"supplier": "Поставщик другой компании."})
        if self.warehouse and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Склад другой компании."})
        # branch согласованность
        if self.branch_id and self.supplier and self.supplier.branch_id not in (None, self.branch_id):
            raise ValidationError({"supplier": "Поставщик другого филиала."})
        if self.branch_id and self.warehouse and self.warehouse.branch_id not in (None, self.branch_id):
            raise ValidationError({"warehouse": "Склад другого филиала."})


class StockInItem(models.Model):
    stock_in = models.ForeignKey(
        StockIn, on_delete=models.CASCADE, related_name="items", verbose_name="Документ прихода"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_in_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Количество",
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    price = models.DecimalField(
        max_digits=13, decimal_places=3, verbose_name="Цена закупки",
        validators=[MinValueValidator(Decimal("0"))]
    )

    class Meta:
        verbose_name = "Позиция прихода"
        verbose_name_plural = "Позиции прихода"
        indexes = [
            models.Index(fields=["stock_in"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"

    def clean(self):
        if not (self.stock_in and self.product):
            return
        # company
        if self.product.company_id != self.stock_in.company_id:
            raise ValidationError({"product": "Товар другой компании."})
        # branch: товар глобальный или branch документа
        if self.stock_in.branch_id and self.product.branch_id not in (None, self.stock_in.branch_id):
            raise ValidationError({"product": "Товар другого филиала, чем документ прихода."})


# 📤 Расход
class StockOut(models.Model):
    TYPE_CHOICES = [
        ("sale", "Продажа"),
        ("return", "Возврат"),
        ("write_off", "Списание"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_outs", verbose_name="Компания"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="stock_outs",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    document_number = models.CharField(max_length=50, verbose_name="№ документа")
    date = models.DateField(verbose_name="Дата")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="outgoing", verbose_name="Склад"
    )
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, verbose_name="Тип операции")
    recipient = models.CharField(max_length=255, verbose_name="Получатель/Компания", blank=True, null=True)
    destination_address = models.CharField(max_length=500, verbose_name="Адрес назначения", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Расход"
        verbose_name_plural = "Расходы"
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("company", "branch", "document_number"),
                name="uq_stockout_company_branch_docnum",
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "document_number"),
                name="uq_stockout_company_docnum_global",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "date"]),
            models.Index(fields=["company", "branch", "date"]),
            models.Index(fields=["company", "branch", "warehouse", "date"]),
            models.Index(fields=["company", "branch", "type", "date"]),
        ]

    def __str__(self):
        return f"Расход {self.document_number} от {self.date}"

    def clean(self):
        if self.warehouse and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Склад другой компании."})
        if self.branch_id and self.warehouse and self.warehouse.branch_id not in (None, self.branch_id):
            raise ValidationError({"warehouse": "Склад другого филиала."})


class StockOutItem(models.Model):
    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE, related_name="items", verbose_name="Документ расхода"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_out_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Количество",
        validators=[MinValueValidator(Decimal("0.01"))]
    )

    class Meta:
        verbose_name = "Позиция расхода"
        verbose_name_plural = "Позиции расхода"
        indexes = [
            models.Index(fields=["stock_out"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"

    def clean(self):
        if not (self.stock_out and self.product):
            return
        if self.product.company_id != self.stock_out.company_id:
            raise ValidationError({"product": "Товар другой компании."})
        if self.stock_out.branch_id and self.product.branch_id not in (None, self.stock_out.branch_id):
            raise ValidationError({"product": "Товар другого филиала, чем документ расхода."})


# 🔄 Перемещение
class StockTransfer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_transfers", verbose_name="Компания"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="stock_transfers",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )
    document_number = models.CharField(max_length=50, verbose_name="№ документа")
    date = models.DateField(verbose_name="Дата")

    source_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="transfers_out", verbose_name="Из склада"
    )
    destination_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="transfers_in", verbose_name="В склад"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("company", "branch", "document_number"),
                name="uq_transfer_company_branch_docnum",
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "document_number"),
                name="uq_transfer_company_docnum_global",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "date"]),
            models.Index(fields=["company", "branch", "date"]),
            models.Index(fields=["company", "branch", "source_warehouse", "destination_warehouse"]),
        ]

    def clean(self):
        # разные склады
        if self.source_warehouse_id and self.destination_warehouse_id:
            if self.source_warehouse_id == self.destination_warehouse_id:
                raise ValidationError("Склады должны быть разными.")
        # company
        if self.source_warehouse and self.source_warehouse.company_id != self.company_id:
            raise ValidationError({"source_warehouse": "Источник другого компании."})
        if self.destination_warehouse and self.destination_warehouse.company_id != self.company_id:
            raise ValidationError({"destination_warehouse": "Приёмник другой компании."})
        # branch согласованность: оба склада глобальные или того же филиала, что документ
        if self.branch_id:
            if self.source_warehouse and self.source_warehouse.branch_id not in (None, self.branch_id):
                raise ValidationError({"source_warehouse": "Источник другого филиала."})
            if self.destination_warehouse and self.destination_warehouse.branch_id not in (None, self.branch_id):
                raise ValidationError({"destination_warehouse": "Приёмник другого филиала."})

    def __str__(self):
        return f"Перемещение {self.document_number} {self.source_warehouse} → {self.destination_warehouse}"


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(
        StockTransfer, on_delete=models.CASCADE, related_name="items", verbose_name="Документ перемещения"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="transfer_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Количество",
                                   validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        verbose_name = "Позиция перемещения"
        verbose_name_plural = "Позиции перемещения"
        indexes = [
            models.Index(fields=["transfer"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"

    def clean(self):
        if not (self.transfer and self.product):
            return
        if self.product.company_id != self.transfer.company_id:
            raise ValidationError({"product": "Товар другой компании."})
        if self.transfer.branch_id and self.product.branch_id not in (None, self.transfer.branch_id):
            raise ValidationError({"product": "Товар другого филиала, чем документ перемещения."})
