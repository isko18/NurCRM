from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal, ROUND_HALF_UP
from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import Sum, F
from mptt.models import MPTTModel, TreeForeignKey
import uuid, secrets

from apps.users.models import Company, User, Branch
from apps.consalting.models import ServicesConsalting
from apps.construction.models import Department

_Q2 = Decimal("0.01")
def _money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(_Q2, rounding=ROUND_HALF_UP)


# ==========================
# Contact
# ==========================
class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='contacts')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_contacts',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contacts')

    name = models.CharField(max_length=128)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    address = models.CharField(max_length=256)
    client_company = models.CharField(max_length=128)
    notes = models.TextField(blank=True, null=True)
    department = models.CharField(max_length=64, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Контакт'
        verbose_name_plural = 'Контакты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.client_company})"

    def clean(self):
        # branch ↔ company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # owner ↔ company
        owner_company_id = getattr(self.owner, "company_id", None)
        if owner_company_id and self.company_id and owner_company_id != self.company_id:
            raise ValidationError({"owner": "Сотрудник принадлежит другой компании."})


# ==========================
# Pipeline
# ==========================
class Pipeline(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='pipelines')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_pipelines',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pipelines')

    name = models.CharField(max_length=128)
    stages = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        owner_company_id = getattr(self.owner, "company_id", None)
        if owner_company_id and self.company_id and owner_company_id != self.company_id:
            raise ValidationError({"owner": "Сотрудник принадлежит другой компании."})


# ==========================
# Deal
# ==========================
class Deal(models.Model):
    STATUS_CHOICES = [
        ('lead', 'Лид'),
        ('prospect', 'Потенциальный клиент'),
        ('deal', 'Сделка в работе'),
        ('closed', 'Закрыта'),
        ('lost', 'Потеряна'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='deals')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_deals',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='deals')
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='deals')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_deals')

    title = models.CharField(max_length=255)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    stage = models.CharField(max_length=128)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
        ]

    def __str__(self):
        return f"{self.title} ({self.status})"

    def clean(self):
        # branch ↔ company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # связи — та же компания
        if self.pipeline_id and self.pipeline.company_id != self.company_id:
            raise ValidationError({"pipeline": "Воронка другой компании."})
        if self.contact_id and self.contact.company_id != self.company_id:
            raise ValidationError({"contact": "Контакт другой компании."})
        if self.assigned_to_id:
            assigned_company_id = getattr(self.assigned_to, "company_id", None)
            if assigned_company_id and assigned_company_id != self.company_id:
                raise ValidationError({"assigned_to": "Сотрудник другой компании."})
        # branch согласованность: дочерние — глобальные или того же филиала
        if self.branch_id:
            if self.pipeline and self.pipeline.branch_id not in (None, self.branch_id):
                raise ValidationError({"pipeline": "Воронка другого филиала."})
            if self.contact and self.contact.branch_id not in (None, self.branch_id):
                raise ValidationError({"contact": "Контакт другого филиала."})

    def save(self, *args, **kwargs):
        if not self.company_id:
            if self.pipeline_id:
                self.company_id = self.pipeline.company_id
            elif self.contact_id:
                self.company_id = self.contact.company_id
        # если pipeline/контакт филиальные — подставим их филиал при отсутствии
        if not self.branch_id:
            self.branch_id = (
                self.pipeline.branch_id or self.contact.branch_id
                if (self.pipeline_id or self.contact_id) else None
            )
        self.full_clean()
        return super().save(*args, **kwargs)


# ==========================
# Task
# ==========================
class Task(models.Model):
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('in_progress', 'В процессе'),
        ('done', 'Выполнена'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tasks')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_tasks',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_date = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'
        ordering = ['-due_date']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
        ]

    def __str__(self):
        return f"{self.title} — {self.status}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.deal_id and self.deal.company_id != self.company_id:
            raise ValidationError({"deal": "Сделка другой компании."})
        if self.assigned_to_id:
            assigned_company_id = getattr(self.assigned_to, "company_id", None)
            if assigned_company_id and assigned_company_id != self.company_id:
                raise ValidationError({"assigned_to": "Сотрудник другой компании."})
        if self.branch_id and self.deal_id and self.deal.branch_id not in (None, self.branch_id):
            raise ValidationError({"deal": "Сделка другого филиала."})

    def save(self, *args, **kwargs):
        if not self.company_id and self.deal_id:
            self.company_id = self.deal.company_id
        if not self.branch_id and self.deal_id:
            self.branch_id = self.deal.branch_id
        self.full_clean()
        return super().save(*args, **kwargs)


# ==========================
# Order / OrderItem
# ==========================
class Order(models.Model):
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('pending', 'В процессе'),
        ('completed', 'Завершён'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='orders')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_orders',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    order_number = models.CharField(max_length=50)
    customer_name = models.CharField(max_length=128)
    date_ordered = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    phone = models.CharField(max_length=32)
    department = models.CharField(max_length=64)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-date_ordered']
        indexes = [
            models.Index(fields=['company', 'date_ordered']),
            models.Index(fields=['company', 'branch', 'date_ordered']),
        ]

    def __str__(self):
        return f"{self.order_number} — {self.customer_name}"

    @property
    def total(self):
        return sum(item.total for item in self.items.all())

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='order_items', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_order_items',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ')
    product = models.ForeignKey("Product", on_delete=models.PROTECT, related_name='order_items', verbose_name='Товар')
    quantity = models.PositiveIntegerField(verbose_name='Количество')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена за единицу', editable=False)
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Итоговая сумма', editable=False)

    class Meta:
        verbose_name = 'Товар в заказе'
        verbose_name_plural = 'Товары в заказе'
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

    def clean(self):
        if self.order_id and self.company_id and self.order.company_id != self.company_id:
            raise ValidationError({"company": "Компания позиции должна совпадать с компанией заказа."})
        if self.order_id and self.branch_id is not None and self.order.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал позиции должен совпадать с филиалом заказа (или быть глобальным вместе с ним)."})
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})
        if self.quantity is not None and self.quantity < 1:
            raise ValidationError({"quantity": "Количество должно быть положительным."})

    def save(self, *args, **kwargs):
        if self.order_id:
            if not self.company_id:
                self.company_id = self.order.company_id
            if self.branch_id is None:
                self.branch_id = self.order.branch_id
        if not self.price:
            self.price = getattr(self.product, "price", None) or Decimal("0.00")
        self.total = (self.price or Decimal("0.00")) * Decimal(self.quantity or 0)
        self.full_clean()
        super().save(*args, **kwargs)


# ==========================
# Global Brand/Category/Product (без company/branch)
# ==========================
class GlobalBrand(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True, verbose_name='Название бренда')
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                            related_name='children', verbose_name='Родительский бренд')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Глобальный бренд'
        verbose_name_plural = 'Глобальные бренды'

    def __str__(self):
        return self.name


class GlobalCategory(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True, verbose_name='Название категории')
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                            related_name='children', verbose_name='Родительская категория')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Глобальная категория'
        verbose_name_plural = 'Глобальные категории'

    def __str__(self):
        return self.name


class GlobalProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=64, blank=True, null=True, unique=True)
    brand = models.ForeignKey(GlobalBrand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    category = models.ForeignKey(GlobalCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Глобальный товар"
        verbose_name_plural = "Глобальные товары"

    def __str__(self):
        return f"{self.name} ({self.barcode or 'без штрих-кода'})"


# ==========================
# ProductCategory / ProductBrand (компания/филиал)
# ==========================
class ProductCategory(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=128, verbose_name='Название категории')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='categories', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_categories',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                            related_name='children', verbose_name='Родительская категория')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Категория товара'
        verbose_name_plural = 'Категории товаров'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_crm_category_name_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_crm_category_name_global_per_company',
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


class ProductBrand(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=128, verbose_name='Название бренда')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='brands', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_brands',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                            related_name='children', verbose_name='Родительский бренд')

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_crm_brand_name_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_crm_brand_name_global_per_company',
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


# ==========================
# Product
# ==========================
class Product(models.Model):
    class Status(models.TextChoices):
        PENDING  = "pending",  "Ожидание"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отказ"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='products', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_products',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    client = models.ForeignKey("Client", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="products", verbose_name="Клиент")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    brand = models.ForeignKey(ProductBrand, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True)

    quantity = models.PositiveIntegerField(default=0)

    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Закупочная цена")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Розничная цена")

    status = models.CharField("Статус", max_length=16, choices=Status.choices, db_index=True, blank=True, null=True)

    item_make = models.ManyToManyField("ItemMake", blank=True, related_name="products", verbose_name="Единицы товара")
    date = models.DateTimeField(verbose_name="Дата", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        unique_together = (("company", "barcode"),)
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        for rel, name in [(self.brand, "brand"), (self.category, "category"), (self.client, "client")]:
            if rel and getattr(rel, "company_id", None) != self.company_id:
                raise ValidationError({name: "Объект принадлежит другой компании."})
            if self.branch_id and rel and getattr(rel, "branch_id", None) not in (None, self.branch_id):
                raise ValidationError({name: "Объект другого филиала."})


class ItemMake(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="item_makes", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_item_makes',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    name = models.CharField("Название", max_length=255)
    price = models.DecimalField("Цена", max_digits=10, decimal_places=2, default=0)
    unit = models.CharField("Единица измерения", max_length=50)
    quantity = models.PositiveIntegerField("Количество", default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Единица товара"
        verbose_name_plural = "Единицы товаров"
        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "branch", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ==========================
# Cart / CartItem / Sale / SaleItem / MobileScannerToken
# ==========================
class Cart(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CHECKED_OUT = "checked_out", "Завершена"
        ABANDONED = "abandoned", "Отменена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="carts", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_carts',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="carts", verbose_name="Пользователь")
    session_key = models.CharField(max_length=64, null=True, blank=True, verbose_name="Ключ сессии")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, verbose_name="Статус")

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма без скидок и налогов")
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма скидки")
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма налога")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Итого")
    order_discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                               verbose_name="Скидка на заказ (сумма)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["session_key"]),
        ]
        verbose_name = "Корзина"
        verbose_name_plural = "Корзины"

    def _calc_tax(self, taxable_base: Decimal) -> Decimal:
        TAX_RATE = Decimal("0.00")
        return taxable_base * TAX_RATE

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.user_id:
            user_company_id = getattr(self.user, "company_id", None)
            if user_company_id and user_company_id != self.company_id:
                raise ValidationError({"user": "Пользователь другой компании."})

    def recalc(self):
        subtotal = Decimal("0")
        line_discount_total = Decimal("0")
        for it in self.items.select_related("product"):
            qty = Decimal(it.quantity or 0)
            base_unit = getattr(it.product, "price", None) or (it.unit_price or Decimal("0"))
            line_base = base_unit * qty
            line_actual = (it.unit_price or 0) * qty
            subtotal += line_base
            diff = line_base - line_actual
            if diff > 0:
                line_discount_total += diff
        subtotal = _money(subtotal)
        line_discount_total = _money(line_discount_total)
        requested_extra = _money(self.order_discount_total or Decimal("0"))
        max_extra = max(Decimal("0"), subtotal - line_discount_total)
        extra_discount = min(requested_extra, max_extra)
        discount_total = _money(line_discount_total + extra_discount)
        taxable_base = subtotal - discount_total
        tax_total = self._calc_tax(taxable_base)
        self.subtotal = subtotal
        self.discount_total = discount_total
        self.tax_total = _money(tax_total)
        self.total = _money(self.subtotal - self.discount_total + self.tax_total)
        self.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="cart_items", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_cart_items',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", verbose_name="Корзина")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="cart_items", verbose_name="Товар")
    custom_name = models.CharField(max_length=255, blank=True, verbose_name="Название (кастомное)")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена за единицу")

    class Meta:
        unique_together = (("cart", "product"),)
        verbose_name = "Товар в корзине"
        verbose_name_plural = "Товары в корзине"
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]

    def __str__(self):
        base = getattr(self.product, "name", None) or (self.custom_name or "Позиция")
        return f"{base} x{self.quantity}"

    def clean(self):
        if self.cart_id and self.company_id and self.cart.company_id != self.company_id:
            raise ValidationError({"company": "Компания позиции должна совпадать с компанией корзины."})
        if self.cart_id and self.branch_id is not None and self.cart.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал позиции должен совпадать с филиалом корзины (или быть глобальным вместе с ней)."})
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})

    def save(self, *args, **kwargs):
        if self.cart_id:
            if not self.company_id:
                self.company_id = self.cart.company_id
            if self.branch_id is None:
                self.branch_id = self.cart.branch_id
        if self.unit_price is None:
            self.unit_price = (self.product.price if self.product else Decimal("0.00"))
        self.full_clean()
        super().save(*args, **kwargs)
        self.cart.recalc()


class Sale(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PAID = "paid", "Оплачен"
        CANCELED = "canceled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sales", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_sales',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="sales", verbose_name="Пользователь")
    client = models.ForeignKey("Client", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="sale", verbose_name="Клиент")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, verbose_name="Статус")
    doc_number = models.PositiveIntegerField("Номер документа", null=True, blank=True, db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма без скидок и налогов")
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма скидки")
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма налога")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Итого")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата оплаты")

    class Meta:
        verbose_name = "Продажа"
        verbose_name_plural = "Продажи"
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
        ]

    def __str__(self):
        return f"Продажа {self.id} ({self.get_status_display()})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.user_id:
            user_company_id = getattr(self.user, "company_id", None)
            if user_company_id and user_company_id != self.company_id:
                raise ValidationError({"user": "Пользователь другой компании."})
        if self.client_id and self.client.company_id != self.company_id:
            raise ValidationError({"client": "Клиент другой компании."})
        if self.branch_id and self.client_id and self.client.branch_id not in (None, self.branch_id):
            raise ValidationError({"client": "Клиент другого филиала."})

    def mark_paid(self):
        self.status = Sale.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "paid_at"])


class SaleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sale_items", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_sale_items',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items", verbose_name="Продажа")
    product = models.ForeignKey(Product, blank=True, null=True, on_delete=models.SET_NULL,
                                related_name="sale_items", verbose_name="Товар")
    name_snapshot = models.CharField(max_length=255, verbose_name="Название товара (снимок)")
    barcode_snapshot = models.CharField(max_length=64, null=True, blank=True, verbose_name="Штрихкод (снимок)")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена за единицу")
    quantity = models.PositiveIntegerField(verbose_name="Количество")

    class Meta:
        verbose_name = "Товар в продаже"
        verbose_name_plural = "Товары в продаже"
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]

    def __str__(self):
        return f"{self.name_snapshot} x{self.quantity}"

    def clean(self):
        if self.sale_id and self.company_id and self.sale.company_id != self.company_id:
            raise ValidationError({"company": "Компания позиции должна совпадать с компанией продажи."})
        if self.sale_id and self.branch_id is not None and self.sale.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал позиции должен совпадать с филиалом продажи (или быть глобальным вместе с ней)."})
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})
        if self.quantity is not None and self.quantity < 1:
            raise ValidationError({"quantity": "Количество должно быть положительным."})

    def save(self, *args, **kwargs):
        if not self.company_id and self.sale_id:
            self.company_id = self.sale.company_id
        if self.branch_id is None and self.sale_id:
            self.branch_id = self.sale.branch_id
        creating = self.pk is None
        if creating:
            if not self.name_snapshot and self.product:
                self.name_snapshot = self.product.name
            if not self.unit_price and self.product:
                self.unit_price = self.product.price
            if not self.barcode_snapshot and self.product:
                self.barcode_snapshot = self.product.barcode
        self.full_clean()
        super().save(*args, **kwargs)


class MobileScannerToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="mobile_tokens", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_mobile_tokens',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="mobile_tokens", verbose_name="Корзина")
    token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="Токен")
    expires_at = models.DateTimeField(verbose_name="Срок действия")

    class Meta:
        verbose_name = "Мобильный токен для сканера"
        verbose_name_plural = "Мобильные токены для сканера"
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]

    @classmethod
    def issue(cls, cart, ttl_minutes=10):
        return cls.objects.create(
            company=cart.company,
            branch=cart.branch,
            cart=cart,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    def is_valid(self):
        return timezone.now() <= self.expires_at

    def __str__(self):
        return f"Токен для корзины {self.cart_id} (действует до {self.expires_at})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.cart_id:
            if self.cart.company_id != self.company_id:
                raise ValidationError({'cart': 'Корзина другой компании.'})
            if self.branch_id and self.cart.branch_id not in (None, self.branch_id):
                raise ValidationError({'cart': 'Корзина другого филиала.'})


# ==========================
# Reviews / Notifications / Integrations / Analytics / Events
# ==========================
class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='reviews')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_reviews',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')

    rating = models.PositiveSmallIntegerField()
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.email} — {self.rating}★"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.user_id and getattr(self.user, "company_id", None) not in (None, self.company_id):
            raise ValidationError({'user': 'Пользователь другой компании.'})


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='notifications')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_notifications',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')

    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.email}: {self.message[:30]}..."

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class Integration(models.Model):
    TYPE_CHOICES = [
        ('telephony', 'Телефония'),
        ('messenger', 'Мессенджер'),
        ('1c', '1C'),
    ]
    STATUS_CHOICES = [
        ('active', 'Активна'),
        ('inactive', 'Неактивна'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='integrations')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_integrations',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    config = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='inactive')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Интеграция'
        verbose_name_plural = 'Интеграции'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'type']),
            models.Index(fields=['company', 'branch', 'type']),
        ]

    def __str__(self):
        return f"{self.type} — {self.status}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class Analytics(models.Model):
    TYPE_CHOICES = [
        ('sales', 'Продажи'),
        ('activity', 'Активность'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='analytics')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_analytics',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Аналитика'
        verbose_name_plural = 'Аналитика'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'type']),
            models.Index(fields=['company', 'branch', 'type']),
        ]

    def __str__(self):
        return f"{self.type} — {self.data.get('metric', '')}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='events')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_events',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    title = models.CharField(max_length=255)
    datetime = models.DateTimeField()
    participants = models.ManyToManyField(User, related_name='events')

    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Событие'
        verbose_name_plural = 'События'
        ordering = ['-datetime']
        indexes = [
            models.Index(fields=['company', 'datetime']),
            models.Index(fields=['company', 'branch', 'datetime']),
        ]

    def __str__(self):
        return f"{self.title} — {self.datetime.strftime('%Y-%m-%d %H:%M')}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ==========================
# Warehouse / WarehouseEvent (CRM-локальные)
# ==========================
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID склада')
    name = models.CharField(max_length=255, verbose_name='Название склада')
    location = models.CharField(max_length=255, blank=True, null=True, verbose_name='Местоположение')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='warehouses', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_warehouses',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class WarehouseEvent(models.Model):
    STATUS_CHOICES = [
        ('draf', 'Черновик'),
        ('conducted', 'Проведен'),
        ('cancelled', 'Отменен'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID события')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='events', verbose_name='Склад')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,related_name='warehouse_events', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_warehouse_events',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    responsible_person = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                           related_name='responsible_warehouse_events', verbose_name='Ответственное лицо')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name='Статус события')
    client_name = models.CharField(max_length=128, verbose_name='Имя клиента')
    title = models.CharField(max_length=255, verbose_name='Название события')
    description = models.TextField(blank=True, null=True, verbose_name='Описание события')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    event_date = models.DateTimeField(verbose_name='Дата события')
    participants = models.ManyToManyField(User, related_name='warehouse_events', verbose_name='Участники')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания события')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления события')

    class Meta:
        verbose_name = 'Складское событие'
        verbose_name_plural = 'Складские события'
        ordering = ['event_date']
        indexes = [
            models.Index(fields=['company', 'event_date']),
            models.Index(fields=['company', 'branch', 'event_date']),
        ]

    def __str__(self):
        return f"{self.title} — {self.event_date.strftime('%Y-%m-%d %H:%M')}"

    def clean(self):
        # branch/company согласованность
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                raise ValidationError({'warehouse': 'Склад другой компании.'})
            if self.branch_id and self.warehouse.branch_id not in (None, self.branch_id):
                raise ValidationError({'warehouse': 'Склад другого филиала.'})
        if self.responsible_person_id:
            rp_company_id = getattr(self.responsible_person, "company_id", None)
            if rp_company_id and rp_company_id != self.company_id:
                raise ValidationError({"responsible_person": "Ответственный из другой компании."})


# ==========================
# Client / ClientDeal / DealInstallment / Bids / SocialApplications
# ==========================
class Client(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"

    class StatusClient(models.TextChoices):
        CLIENT = "client", "клиент"
        SUPPLIERS = "suppliers", "Поставщики"
        IMPLEMENTERS = "implementers", "Реализаторы"
        CONTRACTOR = "contractor", "Подрядчик"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID клиента")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="clients", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_clients',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    type = models.CharField("Тип клиента", max_length=16, choices=StatusClient.choices,
                            default=StatusClient.CLIENT, null=True, blank=True)
    enterprise = models.CharField("Предприятие O", max_length=255, blank=True, null=True)
    full_name = models.CharField("ФИО", max_length=255)
    phone = models.CharField("Телефон", max_length=32)
    email = models.EmailField("Почта", blank=True)
    date = models.DateField("Дата", null=True, blank=True)
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.NEW)

    llc = models.CharField("Название компании", max_length=255, blank=True, null=True)
    inn = models.CharField("ИНН", max_length=32, blank=True, null=True)
    okpo = models.CharField("ОКПО", max_length=32, blank=True, null=True)
    score = models.CharField("Расчетный счет", max_length=64, blank=True, null=True)
    bik = models.CharField("БИК", max_length=32, blank=True, null=True)
    address = models.CharField("Адрес", max_length=255, blank=True, null=True)

    salesperson = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="clients_as_salesperson", verbose_name="Продавец")
    service = models.ForeignKey(ServicesConsalting, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="clients_using_service", verbose_name="Услуга")

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "phone"]),
            models.Index(fields=["company", "branch", "status"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.salesperson_id:
            sp_company_id = getattr(self.salesperson, "company_id", None)
            if sp_company_id and sp_company_id != self.company_id:
                raise ValidationError({'salesperson': 'Продавец другой компании.'})


class ClientDeal(models.Model):
    class Kind(models.TextChoices):
        AMOUNT = "amount", "Сумма договора"
        SALE = "sale", "Продажа"
        DEBT = "debt", "Долг"
        PREPAYMENT = "prepayment", "Предоплата"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="client_deals", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_client_deals',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="deals", verbose_name="Клиент")
    title = models.CharField("Название сделки", max_length=255)
    kind = models.CharField("Тип сделки", max_length=16, choices=Kind.choices, default=Kind.SALE)

    amount = models.DecimalField("Сумма договора", max_digits=12, decimal_places=2, default=0)
    prepayment = models.DecimalField("Предоплата", max_digits=12, decimal_places=2, default=0)

    debt_months = models.PositiveSmallIntegerField("Срок (мес.)", blank=True, null=True)
    first_due_date = models.DateField("Первая дата оплаты", blank=True, null=True)

    note = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Сделка"
        verbose_name_plural = "Сделки"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "client"]),
            models.Index(fields=["company", "branch", "kind"]),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.client_id and self.client.company_id != self.company_id:
            raise ValidationError({'client': 'Клиент другой компании.'})
        if self.branch_id and self.client_id and self.client.branch_id not in (None, self.branch_id):
            raise ValidationError({'client': 'Клиент другого филиала.'})

    # ===== вычисляемые поля для UI =====
    @property
    def debt_amount(self) -> Decimal:
        return (self.amount or Decimal("0")) - (self.prepayment or Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        return self.installments.filter(paid_on__isnull=False).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    @property
    def remaining_debt(self) -> Decimal:
        return (self.debt_amount - self.paid_total).quantize(Decimal("0.01"))

    @property
    def monthly_payment(self) -> Decimal:
        if not self.debt_months or self.debt_months == 0:
            return Decimal("0.00")
        return (self.debt_amount / Decimal(self.debt_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def rebuild_installments(self):
        if self.kind != ClientDeal.Kind.DEBT or not self.debt_months or self.debt_months == 0:
            self.installments.all().delete()
            return
        total = self.debt_amount
        if total <= 0:
            self.installments.all().delete()
            return
        start = self.first_due_date or (timezone.now().date() + relativedelta(months=+1))
        self.installments.all().delete()
        base = (total / Decimal(self.debt_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        paid = Decimal("0.00")
        items = []
        for i in range(1, self.debt_months + 1):
            amount_i = (total - paid) if i == self.debt_months else base
            paid += amount_i
            due = start + relativedelta(months=+(i - 1))
            items.append(DealInstallment(
                deal=self, number=i, due_date=due, amount=amount_i,
                balance_after=(total - paid).quantize(Decimal("0.01")),
            ))
        DealInstallment.objects.bulk_create(items)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.rebuild_installments()


class DealInstallment(models.Model):
    deal = models.ForeignKey(ClientDeal, on_delete=models.CASCADE, related_name="installments", verbose_name="Сделка")
    number = models.PositiveSmallIntegerField("№")
    due_date = models.DateField("Срок оплаты")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)
    balance_after = models.DecimalField("Остаток", max_digits=12, decimal_places=2)
    paid_on = models.DateField("Оплачен", blank=True, null=True)

    class Meta:
        verbose_name = "Платёж по графику"
        verbose_name_plural = "График платежей"
        ordering = ["deal", "number"]
        unique_together = (("deal", "number"),)


class Bid(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PROCESSING = "processing", "В обработке"
        REFUSAL = "refusal", "Отказ"
        THINKS = "thinks", "Думает"
        CONNECTED = "connected", "Подключено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=255, verbose_name="ФИО")
    phone = models.CharField(max_length=255, verbose_name="Номер телефона")
    text = models.TextField(verbose_name="Обращение")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    status = models.CharField("Тип сделки", max_length=16, choices=Status.choices, default=Status.NEW)

    def __str__(self):
        return f"{self.full_name} - {self.phone} - {self.text}"

    class Meta:
        verbose_name = "Заявка на подключение"
        verbose_name_plural = "Заявки на подключение"
        ordering = ["-created_at"]


class SocialApplications(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PROCESSING = "processing", "В обработке"
        CONNECTED = "connected", "Подключено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.CharField(max_length=255, verbose_name="Компания")
    text = models.TextField(verbose_name="Обращение")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    status = models.CharField("Тип сделки", max_length=16, choices=Status.choices, default=Status.NEW)

    def __str__(self):
        return f"{self.company} — {self.text[:30]}..."

    class Meta:
        verbose_name = "Заявка на соц. сети"
        verbose_name_plural = "Заявки на соц. сети"
        ordering = ["-created_at"]


# ==========================
# TransactionRecord
# ==========================
class TransactionRecord(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        APPROVED = 'approved', 'Подтверждена'
        CANCELLED = 'cancelled', 'Отменена'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='transaction_records', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_transaction_records',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    description = models.TextField(verbose_name="Обращение")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='transaction_records', verbose_name='Отдел')

    name = models.CharField('Наименование', max_length=255)
    amount = models.DecimalField('Сумма', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    status = models.CharField('Статус', max_length=16, choices=Status.choices, default=Status.NEW)
    date = models.DateField('Дата')

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Запись'
        verbose_name_plural = 'Записи'
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['company', 'date']),
            models.Index(fields=['company', 'branch', 'date']),
            models.Index(fields=['company', 'department', 'date']),
        ]

    def __str__(self):
        dep = f", отдел: {self.department.name}" if self.department_id else ""
        return f'{self.name} — {self.amount} ({self.get_status_display()}{dep})'

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.department_id and self.department.company_id != self.company_id:
            raise ValidationError({'department': 'Отдел принадлежит другой компании.'})
        if self.branch_id and self.department_id and self.department.branch_id not in (None, self.branch_id):
            raise ValidationError({'department': 'Отдел другого филиала.'})

    def save(self, *args, **kwargs):
        if self.department_id and not self.company_id:
            self.company_id = self.department.company_id
        if self.department_id and self.branch_id is None:
            self.branch_id = self.department.branch_id
        self.full_clean(exclude=None)
        super().save(*args, **kwargs)


# ==========================
# ContractorWork
# ==========================
class ContractorWork(models.Model):
    class ContractorType(models.TextChoices):
        LLC = "llc", "ОсОО / ООО"
        IP  = "ip",  "ИП"

    class Status(models.TextChoices):
        PROCESS = "process", "В процессе"
        COMPLETED  = "completed",  "Завершен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contractor_works", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_contractor_works',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="contractor_works", verbose_name="Отдел")

    title = models.CharField("Наименование", max_length=255)
    contractor_name = models.CharField("Имя подрядчика", max_length=255)
    contractor_phone = models.CharField("Телефон", max_length=32)
    contractor_entity_type = models.CharField("Тип юрлица", max_length=8, choices=ContractorType.choices, null=True, blank=True)
    contractor_entity_name = models.CharField("Название его ООО/ИП", max_length=255, null=True, blank=True)

    amount = models.DecimalField("Сумма договора", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    status = models.CharField("Статус", max_length=255, choices=Status.choices, null=True, blank=True)
    start_date = models.DateField("Дата начала", null=True, blank=True)
    end_date = models.DateField("Дата окончания", null=True, blank=True)
    planned_completion_date = models.DateField("Плановая дата завершения", null=True, blank=True)
    work_calendar_date = models.DateField("Дата календаря выполнения работ", null=True, blank=True)

    description = models.TextField("Описание", blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Подрядные работы"
        verbose_name_plural = "Подрядные работы"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "department"]),
            models.Index(fields=["company", "branch", "department"]),
            models.Index(fields=["company", "start_date"]),
            models.Index(fields=["company", "branch", "start_date"]),
        ]

    def __str__(self):
        return f"{self.title} — {self.contractor_name}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.department_id:
            if self.department.company_id != self.company_id:
                raise ValidationError({"department": "Отдел другой компании."})
            if self.branch_id and self.department.branch_id not in (None, self.branch_id):
                raise ValidationError({"department": "Отдел другого филиала."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Дата окончания не может быть раньше даты начала."})
        if self.planned_completion_date and self.start_date and self.planned_completion_date < self.start_date:
            raise ValidationError({"planned_completion_date": "Плановая дата завершения не может быть раньше начала."})

    @property
    def duration_days(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return None


# ==========================
# Debt / DebtPayment
# ==========================
class Debt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="debts", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_debts',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    name = models.CharField("Имя", max_length=255)
    phone = models.CharField("Телефон", max_length=32)
    amount = models.DecimalField("Сумма долга", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    due_date = models.DateTimeField(verbose_name="дата возвращения", null=True, blank=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Долг"
        verbose_name_plural = "Долги"
        ordering = ["-created_at"]
        unique_together = (("company", "phone"),)
        indexes = [
            models.Index(fields=["company", "phone"]),
            models.Index(fields=["company", "branch", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} — {self.phone} ({self.amount} c)"

    @property
    def paid_total(self) -> Decimal:
        return self.payments.aggregate(s=models.Sum("amount"))["s"] or Decimal("0")

    @property
    def balance(self) -> Decimal:
        return (self.amount - self.paid_total).quantize(Decimal("0.01"))

    def add_payment(self, amount: Decimal, paid_at=None, note: str = ""):
        payment = DebtPayment(
            debt=self, company=self.company, branch=self.branch, amount=amount,
            paid_at=paid_at or timezone.now().date(), note=note
        )
        payment.full_clean()
        payment.save()
        return payment

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class DebtPayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="debt_payments", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_debt_payments',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="payments", verbose_name="Долг")

    amount = models.DecimalField("Сумма оплаты", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    paid_at = models.DateField("Дата оплаты", default=timezone.localdate)
    note = models.CharField("Комментарий", max_length=255, blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Оплата долга"
        verbose_name_plural = "Оплаты долга"
        ordering = ["-paid_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "paid_at"]),
            models.Index(fields=["company", "branch", "paid_at"]),
            models.Index(fields=["debt", "paid_at"]),
        ]

    def __str__(self):
        return f"{self.amount} c от {self.paid_at} ({self.debt.name})"

    def clean(self):
        if self.debt and self.company_id and self.debt.company_id != self.company_id:
            raise ValidationError({"company": "Компания платежа должна совпадать с компанией долга."})
        if self.debt and self.branch_id is not None and self.debt.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал платежа должен совпадать с филиалом долга (или быть глобальным вместе с ним)."})
        if self.debt_id and self.amount:
            qs = self.debt.payments.exclude(pk=self.pk) if self.pk else self.debt.payments
            already = qs.aggregate(s=models.Sum("amount"))["s"] or Decimal("0")
            rest = (self.debt.amount - already)
            if self.amount > rest:
                raise ValidationError({"amount": f"Сумма оплаты превышает остаток долга ({rest} c)."})

    def save(self, *args, **kwargs):
        if self.debt_id:
            if not self.company_id:
                self.company_id = self.debt.company_id
            if self.branch_id is None:
                self.branch_id = self.debt.branch_id
        self.full_clean()
        super().save(*args, **kwargs)


# ==========================
# Object Items / Sales
# ==========================
class ObjectItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="object_items")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_object_items',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    name = models.CharField("Наименование", max_length=255)
    description = models.TextField("Описание", blank=True)
    price = models.DecimalField("Цена", max_digits=12, decimal_places=2)
    date = models.DateField("Дата", default=timezone.localdate)
    quantity = models.PositiveIntegerField("Количество", default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=['company', 'date']),
            models.Index(fields=['company', 'branch', 'date']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class ObjectSale(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        PAID = "paid", "Оплачена"
        CANCELED = "canceled", "Отменена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="object_sales", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_object_sales',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    client = models.ForeignKey("main.Client", blank=True, null=True, on_delete=models.SET_NULL,
                               related_name="object_sales", verbose_name="Клиент")

    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.NEW)
    sold_at = models.DateField("Дата продажи", default=timezone.localdate)
    note = models.CharField("Комментарий", max_length=255, blank=True)

    subtotal = models.DecimalField("Сумма", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["-sold_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "sold_at"]),
            models.Index(fields=["company", "branch", "sold_at"]),
            models.Index(fields=["company", "client"]),
        ]

    def __str__(self):
        return f"Продажа {self.id} — {self.get_status_display()}"

    def recalc(self):
        total = sum((i.unit_price * i.quantity for i in self.items.all()), Decimal("0"))
        self.subtotal = total
        self.save(update_fields=["subtotal"])

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


class ObjectSaleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(ObjectSale, on_delete=models.CASCADE, related_name="items", verbose_name="Продажа")
    name_snapshot = models.CharField("Наименование (снимок)", max_length=255)
    unit_price = models.DecimalField("Цена за единицу", max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField("Кол-во", validators=[MinValueValidator(1)])
    object_item = models.ForeignKey(ObjectItem, on_delete=models.PROTECT, related_name="sold_items", verbose_name="Объект")

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating:
            if not self.name_snapshot:
                self.name_snapshot = self.object_item.name
            if not self.unit_price:
                self.unit_price = self.object_item.price
        super().save(*args, **kwargs)
        if creating:
            self.object_item.quantity = max(0, self.object_item.quantity - self.quantity)
            self.object_item.save(update_fields=["quantity"])
        self.sale.recalc()


# ==========================
# ManufactureSubreal / Acceptance / ReturnFromAgent
# ==========================
class ManufactureSubreal(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        CLOSED = "closed", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="subreals")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_subreals',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subreals")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subreals_as_agent")
    product = models.ForeignKey("Product", on_delete=models.PROTECT, related_name="subreals")

    qty_transferred = models.PositiveIntegerField()
    qty_accepted = models.PositiveIntegerField(default=0)
    qty_returned = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Передача агенту"
        verbose_name_plural = "Передачи агентам"
        indexes = [
            models.Index(fields=["company", "agent", "product", "status"]),
            models.Index(fields=["company", "branch", "agent", "product", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        name = getattr(self.agent, "get_full_name", lambda: "")() or getattr(self.agent, "username", str(self.agent))
        return f"{name} · {self.product.name} · {self.qty_transferred}"

    @property
    def qty_remaining(self) -> int:
        return max(self.qty_transferred - self.qty_accepted, 0)

    @property
    def qty_on_agent(self) -> int:
        return max(self.qty_accepted - self.qty_returned, 0)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.agent_id and getattr(self.agent, "company_id", None) not in (None, self.company_id):
            raise ValidationError({"agent": "Агент принадлежит другой компании."})
        if self.product_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})
        if self.branch_id:
            if self.product and self.product.branch_id not in (None, self.branch_id):
                raise ValidationError({"product": "Товар другого филиала."})
        if self.qty_accepted > self.qty_transferred:
            raise ValidationError({"qty_accepted": "Принято не может превышать переданное."})
        if self.qty_returned > self.qty_accepted:
            raise ValidationError({"qty_returned": "Возвращено не может превышать принятое."})

    def try_close(self):
        if self.qty_remaining == 0 and self.status != self.Status.CLOSED:
            self.status = self.Status.CLOSED
            self.save(update_fields=["status"])


class Acceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="acceptances")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_acceptances',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    subreal = models.ForeignKey(ManufactureSubreal, on_delete=models.CASCADE, related_name="acceptances")
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="acceptances")
    qty = models.PositiveIntegerField()
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Приём по передаче"
        verbose_name_plural = "Приёмы по передаче"
        ordering = ["-accepted_at"]
        indexes = [
            models.Index(fields=["company", "accepted_at"]),
            models.Index(fields=["company", "branch", "accepted_at"]),
            models.Index(fields=["subreal"]),
        ]

    def clean(self):
        if self.subreal_id and self.company_id and self.subreal.company_id != self.company_id:
            raise ValidationError({"company": "Компания приёма должна совпадать с компанией передачи."})
        if self.subreal_id and self.branch_id is not None and self.subreal.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал приёма должен совпадать с филиалом передачи."})
        if self.subreal and self.qty > self.subreal.qty_remaining:
            raise ValidationError({"qty": f"Нельзя принять {self.qty}: доступно {self.subreal.qty_remaining}."})

    def save(self, *args, **kwargs):
        if self.subreal_id:
            if not self.company_id:
                self.company_id = self.subreal.company_id
            if self.branch_id is None:
                self.branch_id = self.subreal.branch_id
        self.full_clean()
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating:
            ManufactureSubreal.objects.filter(pk=self.subreal_id).update(qty_accepted=F("qty_accepted") + self.qty)
            self.subreal.refresh_from_db(fields=["qty_accepted", "qty_transferred", "status"])
            self.subreal.try_close()


class ReturnFromAgent(models.Model):
    class Status(models.TextChoices):
        PENDING  = "pending",  "Ожидает приёма"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отклонён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="returns")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_returns',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    subreal = models.ForeignKey(ManufactureSubreal, on_delete=models.CASCADE, related_name="returns")
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="returns")
    qty = models.PositiveIntegerField()
    returned_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="accepted_returns", null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Возврат от агента"
        verbose_name_plural = "Возвраты от агентов"
        ordering = ["-returned_at"]
        indexes = [
            models.Index(fields=["company", "returned_at"]),
            models.Index(fields=["company", "branch", "returned_at"]),
            models.Index(fields=["subreal"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        if self.subreal_id and self.company_id and self.subreal.company_id != self.company_id:
            raise ValidationError({"company": "Компания возврата должна совпадать с компанией передачи."})
        if self.subreal_id and self.branch_id is not None and self.subreal.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал возврата должен совпадать с филиалом передачи."})
        if self.qty < 1:
            raise ValidationError({"qty": "Минимум 1."})
        if self.subreal and self.status == self.Status.PENDING and self.qty > self.subreal.qty_on_agent:
            raise ValidationError({"qty": f"Нельзя вернуть {self.qty}: на руках {self.subreal.qty_on_agent}."})

    def save(self, *args, **kwargs):
        if self.subreal_id:
            if not self.company_id:
                self.company_id = self.subreal.company_id
            if self.branch_id is None:
                self.branch_id = self.subreal.branch_id
        self.full_clean()
        super().save(*args, **kwargs)

    @transaction.atomic
    def accept(self, by_user):
        if self.status != self.Status.PENDING:
            raise ValidationError("Возврат уже обработан.")
        locked_sub = ManufactureSubreal.objects.select_for_update().get(pk=self.subreal_id)
        if self.qty > locked_sub.qty_on_agent:
            raise ValidationError({"qty": f"Можно принять максимум {locked_sub.qty_on_agent}."})
        product = locked_sub.product
        type(product).objects.select_for_update().filter(pk=product.pk).update(quantity=F("quantity") + self.qty)
        ManufactureSubreal.objects.filter(pk=locked_sub.pk).update(qty_returned=F("qty_returned") + self.qty)
        self.status = self.Status.ACCEPTED
        self.accepted_by = by_user
        self.accepted_at = timezone.now()
        super().save(update_fields=["status", "accepted_by", "accepted_at"])
