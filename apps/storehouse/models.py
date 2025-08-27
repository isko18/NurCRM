from django.db import models
from apps.users.models import Company
from apps.main.models import ProductBrand, ProductCategory
import uuid


# 📦 Склад
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="storehouse_warehouses", verbose_name="Компания"
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    address = models.CharField(max_length=500, verbose_name="Адрес", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.company})"


# 🚚 Поставщик
class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="suppliers", verbose_name="Компания"
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

    def __str__(self):
        return f"{self.name} ({self.phone})"



# 🛒 Товар
class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="storehouse_products", verbose_name="Компания"
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
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена закупки", default=0)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена продажи", default=0)
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        unique_together = ("company", "barcode")
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


# 📊 Остатки
class Stock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="stocks", verbose_name="Склад"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stocks", verbose_name="Товар"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Количество", default=0)

    class Meta:
        unique_together = ("warehouse", "product")
        verbose_name = "Остаток на складе"
        verbose_name_plural = "Остатки на складах"

    def __str__(self):
        return f"{self.product.name} — {self.quantity} {self.product.unit} (на {self.warehouse.name})"


# 📥 Приход
class StockIn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_ins", verbose_name="Компания"
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

    def __str__(self):
        return f"Приход {self.document_number} от {self.date}"


class StockInItem(models.Model):
    stock_in = models.ForeignKey(
        StockIn, on_delete=models.CASCADE, related_name="items", verbose_name="Документ прихода"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_in_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Количество")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена закупки")

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"


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

    def __str__(self):
        return f"Расход {self.document_number} от {self.date}"


class StockOutItem(models.Model):
    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE, related_name="items", verbose_name="Документ расхода"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_out_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Количество")

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"


# 🔄 Перемещение
class StockTransfer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_transfers", verbose_name="Компания"
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

    def clean(self):
        # не позволяем выбирать одинаковые склады
        if self.source_warehouse == self.destination_warehouse:
            from django.core.exceptions import ValidationError
            raise ValidationError("Склады должны быть разными.")

    def __str__(self):
        return f"Перемещение {self.document_number} {self.source_warehouse} → {self.destination_warehouse}"


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(
        StockTransfer, on_delete=models.CASCADE, related_name="items", verbose_name="Документ перемещения"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="transfer_items", verbose_name="Товар"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Количество")

    def __str__(self):
        return f"{self.product.name} × {self.quantity} {self.product.unit}"
