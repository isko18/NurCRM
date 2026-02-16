from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal, ROUND_HALF_UP
from dateutil.relativedelta import relativedelta
from django.db import transaction, connection
from django.db.models import Sum, F, Q, Max, IntegerField
from mptt.models import MPTTModel, TreeForeignKey
import uuid, secrets
from django.core.files.base import ContentFile
from PIL import Image
from django.db.models.functions import Cast
import io
import logging
import json

from apps.users.models import Company, User, Branch
from apps.consalting.models import ServicesConsalting
# from apps.construction.models import Department   # УДАЛЕНО: отделы больше не используются

_Q2 = Decimal("0.01")
def _money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(_Q2, rounding=ROUND_HALF_UP)


def product_image_upload_to(instance, filename: str) -> str:
    # всегда сохраняем в .webp с новым именем
    return f"products/{instance.product_id}/{uuid.uuid4().hex}.webp"


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


class PromoRule(models.Model):
    """
    Динамическое правило "подарка".
    Пример:
    - min_qty=20, gift_qty=1, inclusive=False  => если >20 шт -> 1 в подарок
    - min_qty=50, gift_qty=4, inclusive=True   => если >=50 шт -> 4 в подарок
    scope: либо конкретный product, либо бренд, либо категория, либо вообще все.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='promo_rules', verbose_name='Компания')
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='crm_promo_rules',
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )

    product  = models.ForeignKey("Product", on_delete=models.CASCADE, null=True, blank=True, related_name="promo_rules")
    brand    = models.ForeignKey("ProductBrand", on_delete=models.CASCADE, null=True, blank=True, related_name="promo_rules")
    category = models.ForeignKey("ProductCategory", on_delete=models.CASCADE, null=True, blank=True, related_name="promo_rules")

    title = models.CharField(max_length=128, blank=True, default="", verbose_name="Название правила")

    min_qty   = models.PositiveIntegerField(verbose_name="Порог количества")
    gift_qty  = models.PositiveIntegerField(verbose_name="Подарок (шт)")
    inclusive = models.BooleanField(
        default=False,
        verbose_name="Включительно (≥ вместо >). Если True, то условие qty ≥ min_qty. Если False, qty > min_qty."
    )

    priority = models.IntegerField(default=0, verbose_name="Приоритет")
    active_from = models.DateField(null=True, blank=True)
    active_to   = models.DateField(null=True, blank=True)
    is_active   = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Правило подарков"
        verbose_name_plural = "Правила подарков"
        ordering = ["-priority", "-min_qty", "-id"]
        indexes = [
            models.Index(fields=["company", "branch", "is_active"]),
            models.Index(fields=["company", "product", "min_qty"]),
            models.Index(fields=["company", "brand", "min_qty"]),
            models.Index(fields=["company", "category", "min_qty"]),
        ]
        constraints = [
            # Разрешаем не более одного scоpe одновременно
            # (гарантируем на уровне clean(), это просто инфо-коммент)
        ]

    def __str__(self):
        scope = self.product or self.brand or self.category or "все товары"
        sign = "≥" if self.inclusive else ">"
        return f"{self.title or 'Промо'}: {scope} — если {sign} {self.min_qty} → +{self.gift_qty}"

    def clean(self):
        # филиал должен относиться к компании
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

        # только один таргет: product ИЛИ brand ИЛИ category ИЛИ ни одного
        chosen = [self.product_id, self.brand_id, self.category_id]
        if sum(bool(x) for x in chosen) > 1:
            raise ValidationError("Укажите только product ИЛИ brand ИЛИ category (или ни одного).")

        # проверка компании у таргета
        for rel, name in [(self.product, "product"), (self.brand, "brand"), (self.category, "category")]:
            if rel:
                if getattr(rel, "company_id", None) != self.company_id:
                    raise ValidationError({name: "Объект принадлежит другой компании."})
                if self.branch_id and getattr(rel, "branch_id", None) not in (None, self.branch_id):
                    raise ValidationError({name: "Объект другого филиала."})

        if self.min_qty < 1:
            raise ValidationError({"min_qty": "Порог должен быть ≥ 1."})
        if self.gift_qty < 1:
            raise ValidationError({"gift_qty": "Подарок должен быть ≥ 1."})


# ==========================
# Product
# ==========================
class Product(models.Model):
    
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидание"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отказ"
    
    class Kind(models.TextChoices):
        PRODUCT = "product", "Товар"
        SERVICE = "service", "Услуга"
        BUNDLE = "bundle", "Комплект"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name="Компания",
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_products",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )

    kind = models.CharField(
        "Тип позиции",
        max_length=16,
        choices=Kind.choices,
        default=Kind.PRODUCT,
        db_index=True,
    )

    client = models.ForeignKey(
        "Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="Клиент",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Создал",
    )

    # ---- Код / артикул ----
    code = models.CharField(
        "Код товара",
        max_length=16,
        blank=True,
        db_index=True,
        help_text="Автогенерация в формате 0001 внутри компании",
    )
    
    article = models.CharField("Артикул", max_length=64, blank=True)

    name = models.CharField("Название", max_length=255)
    description = models.TextField("Описание", blank=True, null=True)
    barcode = models.CharField("Штрихкод", max_length=64, null=True, blank=True)

    brand = models.ForeignKey(
        ProductBrand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Бренд",
    )
    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Категория",
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
        "Количество/Остаток",
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
        decimal_places=4,
        default=0,
        help_text="Наценка в процентах к закупочной цене",
    )
    price = models.DecimalField(
        "Цена продажи",
        max_digits=10,
        decimal_places=3,
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

    item_make = models.ManyToManyField(
        "ItemMake",
        blank=True,
        related_name="products",
        verbose_name="Единицы товара",
    )

    date = models.DateTimeField("Дата", blank=True, null=True)

    expiration_date = models.DateField("Срок годности", null=True, blank=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["company", "plu"]),
            # Оптимизация для сканирования по штрих-коду
            models.Index(fields=["company", "barcode"], name="idx_product_company_barcode"),
        ]
        constraints = [
            # ✅ штрихкод уникален в рамках компании, только если задан и не пустой
            models.UniqueConstraint(
                fields=("company", "barcode"),
                condition=Q(barcode__isnull=False) & ~Q(barcode=""),
                name="uq_company_barcode_not_empty",
            ),
            # код товара уникален в рамках компании, если указан и не пустой
            models.UniqueConstraint(
                fields=("company", "code"),
                condition=Q(code__isnull=False) & ~Q(code=""),
                name="uq_company_code_not_empty",
            ),
            # ПЛУ уникален в рамках компании, если задан
            models.UniqueConstraint(
                fields=("company", "plu"),
                condition=Q(plu__isnull=False),
                name="uq_company_plu_not_null",
            ),
        ]

    def __str__(self):
        return self.name

    # ---------- Postgres advisory lock ----------
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
            Product.objects
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

        # Если цена задана вручную в конкретном код-пути (например create-manual),
        # не пересчитываем её из наценки, чтобы не появлялись «копейки» из-за округлений.
        if getattr(self, "_manual_price", False) and self.price is not None:
            self.price = Decimal(self.price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return

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
        # Инвалидация кэша при изменении barcode или plu
        old_barcode = None
        old_plu = None
        if self.pk:
            try:
                old_instance = Product.objects.get(pk=self.pk)
                old_barcode = old_instance.barcode
                old_plu = old_instance.plu
            except Product.DoesNotExist:
                pass
        
        self._recalc_price()
        with transaction.atomic():
            self._pg_lock_company()
            self._auto_generate_code()
            self._auto_generate_plu()
            super().save(*args, **kwargs)
            
            # Инвалидация кэша после сохранения
            from django.core.cache import cache
            if old_barcode and old_barcode != self.barcode:
                cache.delete(f"product_barcode:{self.company_id}:{old_barcode}")
            if self.barcode:
                cache.delete(f"product_barcode:{self.company_id}:{self.barcode}")
            if old_plu and old_plu != self.plu:
                cache.delete(f"product_plu:{self.company_id}:{old_plu}")
            if self.plu:
                cache.delete(f"product_plu:{self.company_id}:{self.plu}")
            
class ProductCharacteristics(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_characteristics",
        verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_product_characteristics",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )

    product = models.OneToOneField(
        "Product",
        on_delete=models.CASCADE,
        related_name="characteristics",
        verbose_name="Товар",
    )

    height_cm = models.DecimalField(
        "Высота, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    width_cm = models.DecimalField(
        "Ширина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    depth_cm = models.DecimalField(
        "Глубина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    factual_weight_kg = models.DecimalField(
        "Фактический вес, кг",
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
    )
    description = models.TextField(
        "Описание",
        blank=True,
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Характеристики товара"
        verbose_name_plural = "Характеристики товара"

    def __str__(self):
        return f"Характеристики: {self.product}"

    def clean(self):
        if self.product_id:
            # компания / филиал должны совпадать с товаром
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        # если не указали company/branch — подставляем из товара
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
        super().save(*args, **kwargs)


class ProductPackage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_packages",
        verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_product_packages",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )
    
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="packages",
        verbose_name="Товар",
    )

    name = models.CharField(
        "Упаковка",
        max_length=64,
        help_text="Например: коробка, пачка, блок, рулон",
    )

    quantity_in_package = models.DecimalField(
        "Количество в упаковке",
        max_digits=10,
        decimal_places=3,
        help_text="Сколько базовых единиц в одной упаковке",
    )

    unit = models.CharField(
        "Ед. изм.",
        max_length=32,
        blank=True,
        help_text="Если пусто — берём единицу товара",
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Упаковка товара"
        verbose_name_plural = "Упаковки товара"

    def __str__(self):
        return f"{self.name}: {self.quantity_in_package} {self.unit or self.product.unit}"

    def clean(self):
        if self.quantity_in_package is not None and self.quantity_in_package <= 0:
            raise ValidationError(
                {"quantity_in_package": "Количество в упаковке должно быть больше 0."}
            )

        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        # автоподстановка company/branch из товара, если не заданы
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
            # если unit не указали — наследуем от товара
            if not self.unit:
                self.unit = self.product.unit

        super().save(*args, **kwargs)

class ProductImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="product_images", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="crm_product_images",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
    )
    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="images", verbose_name="Товар")

    image = models.ImageField(upload_to=product_image_upload_to, null=True, blank=True, verbose_name="Изображение (WebP)")
    alt = models.CharField(max_length=255, blank=True, verbose_name="Alt-текст")
    is_primary = models.BooleanField(default=False, verbose_name="Основное изображение")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото товара"
        verbose_name_plural = "Фото товара"
        constraints = [
            # не более одного основного снимка на продукт
            models.UniqueConstraint(
                fields=("product",),
                condition=models.Q(is_primary=True),
                name="uq_primary_product_image",
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
        # company/branch должны совпадать с продуктом
        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания изображения должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id not in (None, self.branch_id):
                raise ValidationError({"branch": "Филиал изображения должен совпадать с филиалом товара (или быть глобальным вместе с ним)."})

    def save(self, *args, **kwargs):
        # ВАЖНО: уникальный constraint разрешает только одну is_primary=True на продукт.
        # Поэтому при попытке сохранить новую/обновлённую primary-картинку
        # нужно СНАЧАЛА снять primary со старой, иначе INSERT/UPDATE упадёт с IntegrityError.
        if self.product_id and self.is_primary:
            (type(self).objects
                .filter(product_id=self.product_id, is_primary=True)
                .exclude(pk=self.pk)
                .update(is_primary=False))

        # Подставим company/branch от продукта если не заданы
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id

        # Если загружен файл (любой формат) — преобразуем в WebP и перезапишем self.image
        if self.image and hasattr(self.image, "file"):
            try:
                self.image = self._convert_to_webp(self.image)
            except Exception as e:
                raise ValidationError({"image": f"Не удалось конвертировать в WebP: {e}"})

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        storage = self.image.storage if self.image else None
        name = self.image.name if self.image else None
        super().delete(*args, **kwargs)
        # удалим файл из хранилища
        if storage and name and storage.exists(name):
            storage.delete(name)

    @staticmethod
    def _convert_to_webp(field_file) -> ContentFile:
        """
        Принимает загруженный файл любого поддерживаемого PIL формата,
        возвращает ContentFile с webp и корректным именем.
        """
        field_file.seek(0)
        im = Image.open(field_file)

        # для WebP нужен RGB
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

        buf = io.BytesIO()
        # quality 80 / method 6 — хорошее качество и компрессия
        im.save(buf, format="WEBP", quality=80, method=6)
        buf.seek(0)

        content = ContentFile(buf.read())
        # новое имя с webp-расширением
        content.name = f"{uuid.uuid4().hex}.webp"
        return content


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
    quantity = models.DecimalField("Количество", max_digits=18, decimal_places=3, default=Decimal("0.000"))

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


class ProductRecipeItem(models.Model):
    """
    Рецепт готового товара: связь Product <-> ItemMake с нормой расхода.
    qty_per_unit — расход сырья на 1 единицу готового товара.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="recipe_items",
        verbose_name="Готовый товар",
    )
    item_make = models.ForeignKey(
        ItemMake,
        on_delete=models.PROTECT,
        related_name="recipe_usages",
        verbose_name="Сырьё",
    )
    qty_per_unit = models.DecimalField(
        "Расход на 1 ед. товара",
        max_digits=12,
        decimal_places=3,
        help_text="Количество сырья, необходимое для производства 1 единицы готового товара",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Позиция рецепта"
        verbose_name_plural = "Позиции рецептов"
        constraints = [
            models.UniqueConstraint(
                fields=("product", "item_make"),
                name="uq_product_recipe_item",
            ),
            models.CheckConstraint(
                check=models.Q(qty_per_unit__gt=0),
                name="ck_recipe_qty_per_unit_positive",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} <- {self.item_make.name} x{self.qty_per_unit}"


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
        Branch, on_delete=models.CASCADE, related_name="crm_carts",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="carts", verbose_name="Пользователь"
    )
    session_key = models.CharField(max_length=64, null=True, blank=True, verbose_name="Ключ сессии")

    shift = models.ForeignKey(
        "construction.CashShift",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="carts",
        db_index=True,
        verbose_name="Смена",
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, verbose_name="Статус")

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    order_discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["session_key"]),
            models.Index(fields=["shift", "status"]),
            models.Index(fields=["shift", "user", "status"]),
        ]
        constraints = [
            # 1) со сменой: одна активная на (shift,user)
            models.UniqueConstraint(
                fields=("shift", "user"),
                condition=Q(status="active") & Q(shift__isnull=False) & Q(user__isnull=False),
                name="uq_active_cart_per_shift_user",
            ),
            # 2) без смены и branch НЕ NULL
            models.UniqueConstraint(
                fields=("company", "branch", "user"),
                condition=Q(status="active") & Q(shift__isnull=True) & Q(branch__isnull=False) & Q(user__isnull=False),
                name="uq_active_cart_per_user_no_shift_branch",
            ),
            # 3) без смены и branch IS NULL
            models.UniqueConstraint(
                fields=("company", "user"),
                condition=Q(status="active") & Q(shift__isnull=True) & Q(branch__isnull=True) & Q(user__isnull=False),
                name="uq_active_cart_per_user_no_shift_global",
            ),
        ]
        verbose_name = "Корзина"
        verbose_name_plural = "Корзины"

    def _calc_tax(self, taxable_base: Decimal) -> Decimal:
        return Decimal("0.00")

    def clean(self):
        super().clean()

        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        if self.user_id and self.company_id:
            user_company_id = getattr(self.user, "company_id", None)
            if user_company_id and user_company_id != self.company_id:
                raise ValidationError({"user": "Пользователь другой компании."})

        if self.shift_id:
            if self.company_id and self.shift.company_id != self.company_id:
                raise ValidationError({"shift": "Смена другой компании."})
            if self.branch_id is not None and (self.shift.branch_id or None) != (self.branch_id or None):
                raise ValidationError({"shift": "Смена другого филиала."})
            if self.user_id and self.shift.cashier_id and self.user_id != self.shift.cashier_id:
                raise ValidationError({"user": "Корзина не принадлежит кассиру этой смены."})

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        touched = set()

        # shift есть → всё берём из смены
        if self.shift_id:
            if self.company_id != self.shift.company_id:
                self.company_id = self.shift.company_id
                touched.add("company")
            if (self.branch_id or None) != (self.shift.branch_id or None):
                self.branch_id = self.shift.branch_id
                touched.add("branch")
            if not self.user_id and self.shift.cashier_id:
                self.user_id = self.shift.cashier_id
                touched.add("user")
        else:
            # мягкий fallback (если вдруг создают без твоего mixin-а)
            if not self.company_id and self.user_id:
                u_company_id = getattr(self.user, "company_id", None)
                if u_company_id:
                    self.company_id = u_company_id
                    touched.add("company")
            if self.branch_id is None and self.user_id:
                u_branch_id = getattr(self.user, "branch_id", None)
                if u_branch_id:
                    self.branch_id = u_branch_id
                    touched.add("branch")

        # если нас сохраняют через update_fields=["shift"], нужно дописать принудительные поля тоже
        if update_fields is not None and touched:
            # updated_at — auto_now, но Django не обновит его без явного включения в update_fields
            kwargs["update_fields"] = list(set(update_fields) | touched | {"updated_at"})

        self.full_clean()
        return super().save(*args, **kwargs)

    def recalc(self):
        subtotal = Decimal("0")
        line_discount_total = Decimal("0")

        for it in self.items.select_related("product"):
            qty = Decimal(it.quantity or 0)
            base_unit = getattr(it.product, "price", None) or (it.unit_price or Decimal("0"))
            line_base = base_unit * qty
            line_actual = (it.unit_price or Decimal("0")) * qty
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="cart_items")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="crm_cart_items", null=True, blank=True, db_index=True)

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("main.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="cart_items")
    custom_name = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1.000"))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = (("cart", "product"),)

    def clean(self):
        if self.cart_id and self.company_id and self.cart.company_id != self.company_id:
            raise ValidationError({"company": "Компания позиции должна совпадать с компанией корзины."})
        if self.cart_id and self.branch_id is not None and self.cart.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал позиции должен совпадать с филиалом корзины."})
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})

        # ✅ запрет 0 и минуса
        if self.quantity is None or Decimal(self.quantity) <= 0:
            raise ValidationError({"quantity": "Количество должно быть > 0."})

    def save(self, *args, **kwargs):
        if self.cart_id:
            self.company_id = self.cart.company_id
            self.branch_id = self.cart.branch_id

        if self.unit_price is None:
            # Product.price может иметь 3 знака после запятой, а unit_price — 2.
            self.unit_price = self.product.price if self.product else Decimal("0")
        # На всякий случай нормализуем в денежный формат (2 знака)
        self.unit_price = _money(self.unit_price)

        self.full_clean()
        super().save(*args, **kwargs)
        self.cart.recalc()


class Sale(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PAID = "paid", "Оплачен"
        DEBT = "debt", "Долг"
        CANCELED = "canceled", "Отменён"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Наличные"
        TRANSFER = "transfer", "Перевод"
        DEBT = "debt", "Долг"
        MBANK = "mbank", "Мбанк"
        OPTIMA = "optima", "Оптима"
        OBANK = "obank", "Обанк"
        BAKAI = "bakai", "Бакай"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sales")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="crm_sales", null=True, blank=True, db_index=True)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")

    # смена ОПЦИОНАЛЬНО
    shift = models.ForeignKey(
        "construction.CashShift",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="sales",
        db_index=True,
        verbose_name="Смена",
    )

    # касса НУЖНА если shift нет
    cashbox = models.ForeignKey(
        "construction.Cashbox",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="sales",
        db_index=True,
        verbose_name="Касса",
    )

    client = models.ForeignKey("main.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="sale")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    doc_number = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    payment_method = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    cash_received = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "branch", "created_at"]),
            models.Index(fields=["shift", "created_at"]),
            models.Index(fields=["cashbox", "created_at"]),
        ]

    def clean(self):
        super().clean()

        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        if self.user_id and self.company_id:
            user_company_id = getattr(self.user, "company_id", None)
            if user_company_id and user_company_id != self.company_id:
                raise ValidationError({"user": "Пользователь другой компании."})

        if self.client_id and self.company_id and self.client.company_id != self.company_id:
            raise ValidationError({"client": "Клиент другой компании."})
        if self.branch_id and self.client_id and self.client.branch_id not in (None, self.branch_id):
            raise ValidationError({"client": "Клиент другого филиала."})

        # ✅ shift есть — строгие проверки + cashbox должен совпасть со сменой (если передан)
        if self.shift_id:
            if self.company_id and self.shift.company_id != self.company_id:
                raise ValidationError({"shift": "Смена другой компании."})
            if (self.branch_id or None) != (self.shift.branch_id or None):
                raise ValidationError({"shift": "Смена другого филиала."})
            if self.user_id and self.shift.cashier_id and self.user_id != self.shift.cashier_id:
                raise ValidationError({"user": "Продажа не принадлежит кассиру этой смены."})
            if self.cashbox_id and self.cashbox_id != self.shift.cashbox_id:
                raise ValidationError({"cashbox": "Касса продажи должна совпадать с кассой смены."})

        # ✅ shift НЕТ — требуем cashbox
        else:
            if not self.cashbox_id:
                raise ValidationError({"cashbox": "Укажите кассу (если смены нет)."})
            if self.company_id and self.cashbox.company_id != self.company_id:
                raise ValidationError({"cashbox": "Касса другой компании."})
            if (self.branch_id or None) != (self.cashbox.branch_id or None):
                # branch может быть None — тогда проверка ок только если и у кассы None
                raise ValidationError({"cashbox": "Касса другого филиала."})

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        touched = set()

        # shift есть → ЖЁСТКО всё берём из смены
        if self.shift_id:
            if self.company_id != self.shift.company_id:
                self.company_id = self.shift.company_id
                touched.add("company")
            if (self.branch_id or None) != (self.shift.branch_id or None):
                self.branch_id = self.shift.branch_id
                touched.add("branch")
            if self.cashbox_id != self.shift.cashbox_id:
                self.cashbox_id = self.shift.cashbox_id  # ← важно: всегда, не только если пусто
                touched.add("cashbox")
            if not self.user_id and self.shift.cashier_id:
                self.user_id = self.shift.cashier_id
                touched.add("user")

        # shift нет → если cashbox указан, можно подтянуть company/branch (если пустые)
        elif self.cashbox_id:
            if not self.company_id:
                self.company_id = self.cashbox.company_id
                touched.add("company")
            if self.branch_id is None:
                self.branch_id = self.cashbox.branch_id
                touched.add("branch")

        if update_fields is not None and touched:
            kwargs["update_fields"] = list(set(update_fields) | touched)

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def change(self) -> Decimal:
        if self.payment_method != self.PaymentMethod.CASH:
            return Decimal("0.00")
        diff = (self.cash_received or Decimal("0")) - (self.total or Decimal("0"))
        if diff <= 0:
            return Decimal("0.00")
        return diff.quantize(_Q2, rounding=ROUND_HALF_UP)

    def mark_paid(self, payment_method=None, cash_received=None):
        if payment_method is not None:
            self.payment_method = payment_method

        if cash_received is not None:
            if self.payment_method == self.PaymentMethod.CASH:
                self.cash_received = cash_received
            else:
                self.cash_received = Decimal("0.00")

        self.status = Sale.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "paid_at", "payment_method", "cash_received"])


class SaleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sale_items")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_sale_items",
        null=True,
        blank=True,
        db_index=True,
    )

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "main.Product",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="sale_items",
    )

    name_snapshot = models.CharField(max_length=255)
    barcode_snapshot = models.CharField(max_length=64, null=True, blank=True)

    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1.000"))

    # ✅ себестоимость единицы на момент продажи (для маржи)
    purchase_price_snapshot = models.DecimalField(
        "Себестоимость (снапшот)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )


    class Meta:
        indexes = [
            models.Index(fields=["company", "sale"]),
            models.Index(fields=["company", "branch", "sale"]),
            models.Index(fields=["company", "product"]),
        ]

    def clean(self):
        if self.sale_id and self.company_id and self.sale.company_id != self.company_id:
            raise ValidationError({"company": "Компания позиции должна совпадать с компанией продажи."})

        if self.sale_id and self.branch_id is not None and self.sale.branch_id not in (None, self.branch_id):
            raise ValidationError({"branch": "Филиал позиции должен совпадать с филиалом продажи."})

        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})

        if self.quantity is None or Decimal(str(self.quantity)) <= 0:
            raise ValidationError({"quantity": "Количество должно быть > 0."})

        if not self.product_id and not (self.name_snapshot or "").strip():
            raise ValidationError({"name_snapshot": "Укажите название позиции (если товар не выбран)."})

        # себестоимость не может быть отрицательной
        if self.purchase_price_snapshot is not None and Decimal(str(self.purchase_price_snapshot)) < 0:
            raise ValidationError({"purchase_price_snapshot": "Себестоимость не может быть отрицательной."})

    def save(self, *args, **kwargs):
        if self.sale_id:
            self.company_id = self.sale.company_id
            self.branch_id = self.sale.branch_id

        # снапшоты — только при создании
        if self.pk is None:
            if self.product_id:
                if not (self.name_snapshot or "").strip():
                    self.name_snapshot = self.product.name

                # ✅ НЕ "not self.unit_price", а именно None
                if self.unit_price is None:
                    # Product.price может иметь 3 знака после запятой, а unit_price — 2.
                    self.unit_price = self.product.price

                if not (self.barcode_snapshot or "").strip():
                    self.barcode_snapshot = self.product.barcode

                # ✅ ключевое для маржи
                if self.purchase_price_snapshot is None:
                    self.purchase_price_snapshot = self.product.purchase_price or Decimal("0.00")
            else:
                # если товар не выбран — себестоимость неизвестна
                if self.purchase_price_snapshot is None:
                    self.purchase_price_snapshot = Decimal("0.00")

        # нормализуем денежные поля перед валидацией (иначе падаем на 3-х знаках у Product.price)
        if self.unit_price is not None:
            self.unit_price = _money(self.unit_price)
        if self.purchase_price_snapshot is not None:
            self.purchase_price_snapshot = _money(self.purchase_price_snapshot)

        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def line_total(self) -> Decimal:
        return (Decimal(self.unit_price or 0) * Decimal(self.quantity or 0)).quantize(Decimal("0.01"))

    @property
    def line_cogs(self) -> Decimal:
        return (Decimal(self.purchase_price_snapshot or 0) * Decimal(self.quantity or 0)).quantize(Decimal("0.01"))

    @property
    def line_profit(self) -> Decimal:
        return (self.line_total - self.line_cogs).quantize(Decimal("0.01"))
    
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
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True, related_name='warehouse_events', verbose_name='Компания')
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

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="client_deals",
        verbose_name="Компания",
        db_index=True,
                null=True,
        blank=True,
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_client_deals",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="deals",
        verbose_name="Клиент",
        db_index=True,
    )

    title = models.CharField("Название сделки", max_length=255)
    kind = models.CharField(
        "Тип сделки",
        max_length=16,
        choices=Kind.choices,
        default=Kind.SALE,
        db_index=True,
    )

    amount = models.DecimalField("Сумма договора", max_digits=12, decimal_places=2, default=0)
    prepayment = models.DecimalField("Предоплата", max_digits=12, decimal_places=2, default=0)

    debt_months = models.PositiveSmallIntegerField("Срок (мес.)", blank=True, null=True)
    first_due_date = models.DateField("Первая дата оплаты", blank=True, null=True)

    auto_schedule = models.BooleanField(
        "Автоматический график",
        default=True,
        help_text="Если выключено — график не пересобирается автоматически.",
    )

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
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gte=0) & Q(prepayment__gte=0),
                name="clientdeal_amount_prepayment_non_negative",
            ),
            models.CheckConstraint(
                check=Q(prepayment__lte=F("amount")),
                name="clientdeal_prepayment_lte_amount",
            ),
        ]

    # ===== computed =====
    @property
    def debt_amount(self) -> Decimal:
        return (self.amount or Decimal("0")) - (self.prepayment or Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        s = self.installments.aggregate(s=Sum("paid_amount"))["s"] or Decimal("0")
        return s.quantize(Decimal("0.01"))

    @property
    def remaining_debt(self) -> Decimal:
        return (self.debt_amount - self.paid_total).quantize(Decimal("0.01"))

    @property
    def monthly_payment(self) -> Decimal:
        if not self.debt_months or self.debt_months <= 0:
            return Decimal("0.00")
        return (self.debt_amount / Decimal(self.debt_months)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    # ===== validation =====
    def clean(self):
        super().clean()

        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

        if self.client_id and self.client.company_id != self.company_id:
            raise ValidationError({"client": "Клиент другой компании."})

        # клиент может быть общий (branch=None)
        if self.branch_id and self.client_id and self.client.branch_id not in (None, self.branch_id):
            raise ValidationError({"client": "Клиент другого филиала."})

        a = self.amount or Decimal("0")
        p = self.prepayment or Decimal("0")

        if a < 0:
            raise ValidationError({"amount": "Сумма не может быть отрицательной."})
        if p < 0:
            raise ValidationError({"prepayment": "Предоплата не может быть отрицательной."})
        if p > a:
            raise ValidationError({"prepayment": "Предоплата не может превышать сумму договора."})

        # прод-аудит: если есть платежи — нельзя менять условия
        if self.pk:
            old = ClientDeal.objects.filter(pk=self.pk).values(
                "kind", "amount", "prepayment", "debt_months", "first_due_date"
            ).first()
            if old:
                has_payments = self.payments.exists()
                changed_terms = (
                    self.kind != old["kind"]
                    or (self.amount or Decimal("0")) != (old["amount"] or Decimal("0"))
                    or (self.prepayment or Decimal("0")) != (old["prepayment"] or Decimal("0"))
                    or (self.debt_months != old["debt_months"])
                    or (self.first_due_date != old["first_due_date"])
                )
                if has_payments and changed_terms:
                    raise ValidationError("Нельзя менять тип/суммы/срок/дату: по сделке уже есть платежи.")

        if self.kind == self.Kind.DEBT:
            if (a - p) <= 0:
                raise ValidationError({"prepayment": 'Для типа "Долг" сумма договора должна быть больше предоплаты.'})
            if not self.debt_months or self.debt_months <= 0:
                raise ValidationError({"debt_months": "Укажите срок (в месяцах) для рассрочки."})
        else:
            self.debt_months = None
            self.first_due_date = None
            self.auto_schedule = False

    # ===== schedule =====
    def rebuild_installments(self, force: bool = False):
        if self.kind != self.Kind.DEBT or not self.debt_months or self.debt_months <= 0:
            self.installments.all().delete()
            return

        total = self.debt_amount
        if total <= 0:
            self.installments.all().delete()
            return

        if not force and self.payments.exists():
            raise ValidationError("Нельзя пересобрать график: по сделке уже есть платежи.")

        start = self.first_due_date or (timezone.localdate() + relativedelta(months=+1))

        base = (total / Decimal(self.debt_months)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        paid = Decimal("0.00")
        items = []

        for i in range(1, self.debt_months + 1):
            amount_i = (total - paid) if i == self.debt_months else base
            paid += amount_i
            due = start + relativedelta(months=+(i - 1))

            items.append(
                DealInstallment(
                    company=self.company,
                    branch=self.branch,
                    deal=self,
                    number=i,
                    due_date=due,
                    amount=amount_i,
                    balance_after=(total - paid).quantize(Decimal("0.01")),
                )
            )

        with transaction.atomic():
            self.installments.all().delete()
            DealInstallment.objects.bulk_create(items)

    def save(self, *args, **kwargs):
        if self.kind != self.Kind.DEBT:
            self.debt_months = None
            self.first_due_date = None
            self.auto_schedule = False

        self.full_clean()
        super().save(*args, **kwargs)

        if self.kind != self.Kind.DEBT:
            self.installments.all().delete()
            return

        if self.auto_schedule:
            self.rebuild_installments()


class DealInstallment(models.Model):
    # ✅ теперь тоже UUID (как ты хочешь “всё на uuid”)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="deal_installments",
        verbose_name="Компания",
        db_index=True,
        null=True,
        blank=True,
        
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="deal_installments",
        null=True,
        blank=True,
        verbose_name="Филиал",
        db_index=True,
    )

    deal = models.ForeignKey(
        ClientDeal,
        on_delete=models.CASCADE,
        related_name="installments",
        verbose_name="Сделка",
        db_index=True,
    )

    number = models.PositiveSmallIntegerField("№")
    due_date = models.DateField("Срок оплаты")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)
    balance_after = models.DecimalField("Остаток", max_digits=12, decimal_places=2)

    paid_on = models.DateField("Оплачен", blank=True, null=True)
    paid_amount = models.DecimalField("Оплачено за период", max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Платёж по графику"
        verbose_name_plural = "График платежей"
        ordering = ["deal", "number"]
        indexes = [
            models.Index(fields=["company", "branch", "deal"]),
            models.Index(fields=["company", "deal", "number"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["deal", "number"], name="uniq_installment_deal_number"),
            models.CheckConstraint(check=Q(amount__gte=0) & Q(paid_amount__gte=0), name="installment_non_negative"),
            models.CheckConstraint(check=Q(paid_amount__lte=F("amount")), name="installment_paid_lte_amount"),
        ]

    def clean(self):
        super().clean()
        if self.deal_id and self.company_id and self.deal.company_id != self.company_id:
            raise ValidationError({"company": "Компания взноса должна совпадать с компанией сделки."})
        if self.deal_id and self.branch_id != self.deal.branch_id:
            raise ValidationError({"branch": "Филиал взноса должен совпадать с филиалом сделки (включая NULL)."})

    def save(self, *args, **kwargs):
        # авто-подтягиваем из сделки (админка/скрипты)
        if self.deal_id:
            self.company_id = self.deal.company_id
            self.branch_id = self.deal.branch_id

        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def remaining_for_period(self) -> Decimal:
        return (self.amount - (self.paid_amount or Decimal("0"))).quantize(Decimal("0.01"))


class DealPayment(models.Model):
    class Kind(models.TextChoices):
        PAY = "pay", "Оплата"
        REFUND = "refund", "Возврат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="deal_payments",
        verbose_name="Компания",
        db_index=True,
                null=True,
        blank=True,
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="deal_payments",
        null=True,
        blank=True,
        verbose_name="Филиал",
        db_index=True,
    )

    deal = models.ForeignKey(
        ClientDeal,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Сделка",
        db_index=True,
                null=True,
        blank=True,
    )

    installment = models.ForeignKey(
        DealInstallment,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Взнос",
        db_index=True,
                null=True,
        blank=True,
    )

    kind = models.CharField("Тип", max_length=16, choices=Kind.choices, default=Kind.PAY)
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)

    paid_date = models.DateField("Дата платежа")
    idempotency_key = models.UUIDField("Ключ идемпотентности", null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deal_payments",
        verbose_name="Кем создан",
    )

    note = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "branch", "deal", "created_at"]),
            models.Index(fields=["company", "installment", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="dealpayment_amount_gt_0"),
            models.UniqueConstraint(
                fields=["deal", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="uniq_deal_idempotency_key",
            ),
        ]

    def clean(self):
        super().clean()

        if self.deal_id and self.company_id and self.deal.company_id != self.company_id:
            raise ValidationError({"company": "Компания платежа должна совпадать с компанией сделки."})
        if self.deal_id and self.branch_id != self.deal.branch_id:
            raise ValidationError({"branch": "Филиал платежа должен совпадать с филиалом сделки (включая NULL)."})

        if self.installment_id and self.deal_id and self.installment.deal_id != self.deal_id:
            raise ValidationError({"installment": "Взнос не принадлежит этой сделке."})

        if self.installment_id and self.company_id and self.installment.company_id != self.company_id:
            raise ValidationError({"company": "Компания платежа должна совпадать с компанией взноса."})
        if self.installment_id and self.branch_id != self.installment.branch_id:
            raise ValidationError({"branch": "Филиал платежа должен совпадать с филиалом взноса (включая NULL)."})

    def save(self, *args, **kwargs):
        # авто-подтягиваем из сделки
        if self.deal_id:
            self.company_id = self.deal.company_id
            self.branch_id = self.deal.branch_id

        self.full_clean()
        super().save(*args, **kwargs)
            
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

    # УДАЛЕНО: department FK, теперь запись не привязана к отделу
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
        ]

    def __str__(self):
        return f'{self.name} — {self.amount} ({self.get_status_display()})'

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def save(self, *args, **kwargs):
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

    # УДАЛЕНО: department FK, теперь подрядные работы не привязаны к отделу

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
            models.Index(fields=["company", "start_date"]),
            models.Index(fields=["company", "branch", "start_date"]),
        ]

    def __str__(self):
        return f"{self.title} — {self.contractor_name}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
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
            raise ValidationError({"branch": "Филиал платежа должен совпадать с филиалом долга (или быть глобальным вместе с ней)."})
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

        if self.unit_price is not None:
            self.unit_price = _money(self.unit_price)
        super().save(*args, **kwargs)
        if creating:
            self.object_item.quantity = max(0, self.object_item.quantity - self.quantity)
            self.object_item.save(update_fields=["quantity"])
        self.sale.recalc()

class ManufactureSubreal(models.Model):
    class Status(models.TextChoices):
        OPEN   = "open",   "Открыта"
        CLOSED = "closed", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("users.Company", on_delete=models.CASCADE, related_name="subreals")
    branch = models.ForeignKey(
        "users.Branch", on_delete=models.CASCADE, related_name="crm_subreals",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subreals")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subreals_as_agent")
    product = models.ForeignKey("main.Product", on_delete=models.PROTECT, related_name="subreals")

    # Опциональный идемпотентный ключ на создание передачи
    external_ref = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    # Флаг «пилорама» — авто-приём при создании
    is_sawmill = models.BooleanField(
        default=False, db_index=True, help_text="Если True — авто-приём на весь остаток при создании."
    )

    qty_transferred = models.PositiveIntegerField()
    qty_accepted    = models.PositiveIntegerField(default=0)
    qty_returned    = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Передача агенту"
        verbose_name_plural = "Передачи агентам"
        indexes = [
            models.Index(fields=["company", "agent", "product", "status"]),
            models.Index(fields=["company", "branch", "agent", "product", "status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["company", "is_sawmill", "status"]),

        ]
        constraints = [
            # идемпотентность создания передачи (включается только когда external_ref не NULL)
            models.UniqueConstraint(
                fields=["company", "external_ref"],
                name="uniq_subreal_company_external_ref",
                condition=Q(external_ref__isnull=False),
            ),
        ]

    def __str__(self):
        agent_name = (
            getattr(self.agent, "get_full_name", lambda: "")() or
            getattr(self.agent, "username", None) or
            str(self.agent_id)
        )
        prod_name = getattr(self.product, "name", None) or str(self.product_id)
        return f"{agent_name} · {prod_name} · {self.qty_transferred}"

    @property
    def qty_remaining(self) -> int:
        return max((self.qty_transferred or 0) - (self.qty_accepted or 0), 0)

    @property
    def qty_on_agent(self) -> int:
        """
        Базовое свойство без учета продаж (для обратной совместимости).
        Для правильного расчета используйте get_qty_on_hand_with_sales().
        """
        return max((self.qty_accepted or 0) - (self.qty_returned or 0), 0)
    
    def get_qty_on_hand_with_sales(self, company_id=None, *, exclude_pending_return_id=None) -> int:
        """
        Вычисляет количество на руках с учетом продаж через AgentSaleAllocation.
        Если company_id не указан, используется self.company_id.

        Также учитывает "резерв" под возвраты со статусом PENDING:
          available = accepted - returned - sold(paid/debt) - pending_reserved

        exclude_pending_return_id:
          - полезно при approve конкретного возврата, чтобы не вычитать его самого из резерва.
        """
        company_id = company_id or self.company_id
        accepted = int(self.qty_accepted or 0)
        returned = int(self.qty_returned or 0)
        
        # Вычисляем проданное количество
        sold = (
            AgentSaleAllocation.objects
            .filter(
                subreal_id=self.pk,
                company_id=company_id,
                sale__status__in=[Sale.Status.PAID, Sale.Status.DEBT],
            )
            .aggregate(total=Sum("qty"))["total"] or 0
        )
        sold = int(sold)

        pending_qs = ReturnFromAgent.objects.filter(
            company_id=company_id,
            subreal_id=self.pk,
            status=ReturnFromAgent.Status.PENDING,
        )
        if exclude_pending_return_id:
            pending_qs = pending_qs.exclude(pk=exclude_pending_return_id)
        pending_reserved = int(pending_qs.aggregate(s=Sum("qty"))["s"] or 0)

        return max(accepted - returned - sold - pending_reserved, 0)

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})
        if self.agent_id and getattr(self.agent, "company_id", None) not in (None, self.company_id):
            raise ValidationError({"agent": "Агент принадлежит другой компании."})
        if self.product_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Товар принадлежит другой компании."})
        if self.branch_id and self.product and self.product.branch_id not in (None, self.branch_id):
            raise ValidationError({"product": "Товар другого филиала."})
        if (self.qty_accepted or 0) > (self.qty_transferred or 0):
            raise ValidationError({"qty_accepted": "Принято не может превышать переданное."})
        if (self.qty_returned or 0) > (self.qty_accepted or 0):
            raise ValidationError({"qty_returned": "Возвращено не может превышать принятое."})

    def try_close(self):
        if self.qty_remaining == 0 and self.status != self.Status.CLOSED:
            self.status = self.Status.CLOSED
            self.save(update_fields=["status"])

    @transaction.atomic
    def auto_accept_if_needed(self, by_user):
        """
        Для is_sawmill=True: принять весь остаток сразу один раз.
        Идемпотентно и без конфликтов.
        """
        if not self.is_sawmill:
            return

        # 1. Жёстко лочим ТОЛЬКО сам subreal, без join'ов
        locked = (
            type(self).objects
            .select_related(None)        # убираем join'ы
            .select_for_update()
            .get(pk=self.pk)
        )

        remaining = locked.qty_remaining
        if remaining <= 0 or locked.status != locked.Status.OPEN:
            return

        # 2. Нам ещё нужны company и branch для Acceptance.
        #    Они уже есть на self (или можем рефрешнуть locked с нужными связями без FOR UPDATE).
        #    Сейчас проще так: возьмём company/branch с self, они не меняются в процессе.
        company = self.company
        branch = self.branch

        Acceptance.objects.create(
            company=company,
            branch=branch,
            subreal=locked,
            accepted_by=by_user,
            qty=remaining,
            accepted_at=timezone.now(),
        )
        # Остальное (qty_accepted, try_close) сделает Acceptance.save()


class Acceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("users.Company", on_delete=models.CASCADE, related_name="acceptances")
    branch = models.ForeignKey(
        "users.Branch", on_delete=models.CASCADE, related_name="crm_acceptances",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
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
        if (self.qty or 0) < 1:
            raise ValidationError({"qty": "Минимум 1."})
        # запретим приём в закрытую передачу на уровне модели
        if self.subreal and self.subreal.status != ManufactureSubreal.Status.OPEN:
            raise ValidationError({"subreal": "Передача уже закрыта."})
        if self.subreal and (self.qty or 0) > self.subreal.qty_remaining:
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
    company = models.ForeignKey("users.Company", on_delete=models.CASCADE, related_name="returns")
    branch = models.ForeignKey(
        "users.Branch", on_delete=models.CASCADE, related_name="crm_returns",
        null=True, blank=True, db_index=True, verbose_name="Филиал"
    )
    subreal = models.ForeignKey(ManufactureSubreal, on_delete=models.CASCADE, related_name="returns")
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="returns")
    qty = models.PositiveIntegerField()
    returned_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="accepted_returns", null=True, blank=True
    )
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
        if (self.qty or 0) < 1:
            raise ValidationError({"qty": "Минимум 1."})
        if self.subreal and self.status == self.Status.PENDING:
            qty_on_hand = self.subreal.get_qty_on_hand_with_sales(
                company_id=self.company_id,
                exclude_pending_return_id=self.pk,  # при апдейте не считаем резерв "самого себя"
            )
            if (self.qty or 0) > qty_on_hand:
                raise ValidationError({"qty": f"Нельзя вернуть {self.qty}: на руках {qty_on_hand}."})

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
        qty_on_hand = locked_sub.get_qty_on_hand_with_sales(
            company_id=self.company_id,
            exclude_pending_return_id=self.pk,
        )
        if self.qty > qty_on_hand:
            # расширенный ответ, чтобы было понятно почему 0
            accepted = int(locked_sub.qty_accepted or 0)
            returned = int(locked_sub.qty_returned or 0)
            sold = (
                AgentSaleAllocation.objects
                .filter(
                    subreal_id=locked_sub.pk,
                    company_id=self.company_id,
                    sale__status__in=[Sale.Status.PAID, Sale.Status.DEBT],
                )
                .aggregate(total=Sum("qty"))["total"] or 0
            )
            sold = int(sold)
            pending_reserved_other = int(
                ReturnFromAgent.objects.filter(
                    company_id=self.company_id,
                    subreal_id=locked_sub.pk,
                    status=ReturnFromAgent.Status.PENDING,
                )
                .exclude(pk=self.pk)
                .aggregate(s=Sum("qty"))["s"] or 0
            )
            debug_payload = {
                "qty_accepted": accepted,
                "qty_returned": returned,
                "sold_paid_debt": sold,
                "pending_reserved_other": pending_reserved_other,
                "available_now": qty_on_hand,
            }
            raise ValidationError(
                {
                    "qty": [f"Можно принять максимум {qty_on_hand}."],
                    # Django ValidationError ожидает строку/список строк, не dict
                    "debug": [json.dumps(debug_payload, ensure_ascii=False, default=str)],
                }
            )
        product = locked_sub.product
        type(product).objects.select_for_update().filter(pk=product.pk).update(quantity=F("quantity") + self.qty)
        prod_model = type(product)
        prod_id = product.pk

        def _send_webhook():
            from apps.main.services.webhooks import send_product_webhook

            try:
                prod = prod_model.objects.get(pk=prod_id)
                send_product_webhook(prod, "product.updated")
            except Exception:
                logging.getLogger("crm.webhooks").error(
                    "Failed to send product.updated webhook after return accept. product_id=%s",
                    prod_id,
                    exc_info=True,
                )

        try:
            transaction.on_commit(_send_webhook)
        except Exception:
            _send_webhook()
        ManufactureSubreal.objects.filter(pk=locked_sub.pk).update(qty_returned=F("qty_returned") + self.qty)
        self.status = self.Status.ACCEPTED
        self.accepted_by = by_user
        self.accepted_at = timezone.now()
        super().save(update_fields=["status", "accepted_by", "accepted_at"])

    @transaction.atomic
    def reject(self, by_user):
        """
        Владелец/админ отклонил возврат.
        Товар на склад не возвращаем.
        """
        if self.status != self.Status.PENDING:
            raise ValidationError("Возврат уже обработан.")
        self.status = self.Status.REJECTED
        self.accepted_by = by_user
        self.accepted_at = timezone.now()
        super().save(update_fields=["status", "accepted_by", "accepted_at"])


class AgentSaleAllocation(models.Model):
    company   = models.ForeignKey("users.Company", on_delete=models.CASCADE)
    agent     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=True)
    subreal   = models.ForeignKey(ManufactureSubreal, on_delete=models.CASCADE, related_name="sale_allocations", db_index=True)
    sale      = models.ForeignKey("main.Sale", on_delete=models.CASCADE, related_name="agent_allocations")
    sale_item = models.ForeignKey("main.SaleItem", on_delete=models.CASCADE, related_name="agent_allocations")
    product   = models.ForeignKey("main.Product", on_delete=models.PROTECT)
    qty       = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["agent", "product"]),
            models.Index(fields=["subreal", "product"]),
            models.Index(fields=["sale", "product", "subreal"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sale_item", "subreal"],
                name="uniq_allocation_saleitem_subreal",
            ),
        ]


class AgentRequestCart(models.Model):
    """
    Заявка агента на получение товара.
    Агент сначала копит товары в статусе draft, потом отправляет (submitted),
    владелец подтверждает (approved), после чего товар списывается со склада
    и создаются передачи (ManufactureSubreal) на агента.
    """

    class Status(models.TextChoices):
        DRAFT     = "draft", "Черновик"
        SUBMITTED = "submitted", "Отправлено владельцу"
        APPROVED  = "approved", "Одобрено и выдано"
        REJECTED  = "rejected", "Отклонено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="agent_carts", verbose_name="Компания")
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="crm_agent_carts",
        null=True, blank=True, db_index=True, verbose_name='Филиал'
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="agent_request_carts",
        verbose_name="Агент",
        help_text="Кому выдаём товар",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="agent_request_carts",
        verbose_name="Клиент"
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    note = models.CharField(max_length=255, blank=True, verbose_name="Комментарий агента")

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="approved_agent_carts",
        verbose_name="Кем одобрено"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Заявка агента на товар"
        verbose_name_plural = "Заявки агентов на товар"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["agent", "status"]),
        ]

    def __str__(self):
        return f"Заявка {self.id} от {getattr(self.agent,'username',self.agent_id)} [{self.get_status_display()}]"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.agent_id and getattr(self.agent, "company_id", None) not in (None, self.company_id):
            raise ValidationError({'agent': 'Агент принадлежит другой компании.'})
        if self.client_id and self.client.company_id != self.company_id:
            raise ValidationError({'client': 'Клиент другой компании.'})
        if self.branch_id and self.client_id and self.client.branch_id not in (None, self.branch_id):
            raise ValidationError({'client': 'Клиент другого филиала.'})

    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    def _recalc_gifts_for_items(self):
        """
        Пересчитать подарки для всех позиций.
        Вызывается при submit() — фиксируем gift_quantity и total_quantity.
        """
        for it in self.items.select_related("product"):
            base_qty = int(it.quantity_requested or 0)
            from apps.utils import compute_gift_qty
            gift_qty = compute_gift_qty(
                product=it.product,
                qty=base_qty,
                company=self.company,
                branch=self.branch,
            )
            it.gift_quantity = gift_qty
            it.total_quantity = base_qty + gift_qty
            # price_snapshot хранится с 2 знаками после запятой (денежный формат),
            # а Product.price может быть с 3 знаками -> округляем.
            if not it.price_snapshot:
                it.price_snapshot = _money(it.product.price if it.product else Decimal("0"))
            else:
                it.price_snapshot = _money(it.price_snapshot)
            it.save(update_fields=["gift_quantity", "total_quantity", "price_snapshot", "updated_at"])

    @transaction.atomic
    def submit(self):
        """
        Агент нажал 'Отправить запрос'.
        После этого менять корзину нельзя.
        """
        if self.status != self.Status.DRAFT:
            raise ValidationError("Можно отправить только черновик.")
        if not self.items.exists():
            raise ValidationError("Нельзя отправить пустую заявку.")
        # фиксируем подарки
        self._recalc_gifts_for_items()
        self.status = self.Status.SUBMITTED
        self.submitted_at = timezone.now()
        self.full_clean()
        self.save(update_fields=["status", "submitted_at", "updated_at"])

    @transaction.atomic
    def approve(self, by_user):
        """
        Владелец/админ подтверждает заявку.
        Мы списываем товар со склада, создаём передачи (ManufactureSubreal) на агента,
        и привязываем каждую позицию к созданной передаче.
        """
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Можно одобрить только заявку в статусе 'submitted'.")

        # пересчёт подарков, чтобы qty/подарок/итого были зафиксированы
        self._recalc_gifts_for_items()

        for it in self.items.select_related("product"):
            prod = it.product
            need_qty = int(it.total_quantity or 0)
            if need_qty <= 0:
                continue  # пустышка - пропускаем

            # 💡 безопасная блокировка конкретного продукта без join'ов
            locked_qs = (
                type(prod).objects
                .select_related(None)     # ВАЖНО: убираем автоджойны
                .select_for_update()
                .filter(pk=prod.pk)
            )

            current_qty = locked_qs.values_list("quantity", flat=True).first() or 0
            if current_qty < need_qty:
                raise ValidationError({
                    "items": f"Недостаточно на складе для {prod.name}: нужно {need_qty}, доступно {current_qty}."
                })

            # списываем со склада
            locked_qs.update(quantity=F("quantity") - need_qty)

            prod_model = type(prod)
            prod_id = prod.pk

            def _send_webhook():
                from apps.main.services.webhooks import send_product_webhook

                try:
                    p = prod_model.objects.get(pk=prod_id)
                    send_product_webhook(p, "product.updated")
                except Exception:
                    logging.getLogger("crm.webhooks").error(
                        "Failed to send product.updated webhook after agent request approve. product_id=%s",
                        prod_id,
                        exc_info=True,
                    )

            try:
                transaction.on_commit(_send_webhook)
            except Exception:
                _send_webhook()

            # создаём передачу агенту
            sub = ManufactureSubreal.objects.create(
                company=self.company,
                branch=self.branch,
                user=by_user,        # кто выдал
                agent=self.agent,    # кто получил
                product=prod,
                qty_transferred=need_qty,
                is_sawmill=True,     # сразу считаем, что он взял в руки
            )

            # авто-принять (это поднимет qty_accepted и может закрыть передачу)
            sub.auto_accept_if_needed(by_user)

            # привязываем позицию к созданной передаче
            it.subreal = sub
            it.save(update_fields=["subreal", "updated_at"])

        self.status = self.Status.APPROVED
        self.approved_at = timezone.now()
        self.approved_by = by_user
        self.full_clean()
        self.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])

    @transaction.atomic
    def reject(self, by_user):
        """
        Владелец/админ отклонил.
        Товар не списываем.
        """
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Можно отклонить только заявку в статусе 'submitted'.")
        self.status = self.Status.REJECTED
        self.approved_at = timezone.now()
        self.approved_by = by_user
        self.full_clean()
        self.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])


class AgentRequestItem(models.Model):
    """
    Строка внутри AgentRequestCart.
    В черновике агент просто накидывает product + quantity_requested.
    Когда заявка отправляется (submit), мы фиксируем:
      - gift_quantity (сколько бесплатно),
      - total_quantity (итого нужно выдать агенту),
      - price_snapshot (цена на момент заявки).
    При approve мы создаём ManufactureSubreal и кладём ссылку сюда.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(
        AgentRequestCart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Заявка"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="agent_request_items",
        verbose_name="Товар"
    )

    quantity_requested = models.PositiveIntegerField(verbose_name="Запрошено (шт)")
    gift_quantity = models.PositiveIntegerField(default=0, verbose_name="Подарок (шт)")
    total_quantity = models.PositiveIntegerField(default=0, verbose_name="Итого выдать (шт)")

    price_snapshot = models.DecimalField(
        "Цена на момент заявки",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="фиксируется при отправке"
    )

    subreal = models.ForeignKey(
        ManufactureSubreal,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="request_items",
        verbose_name="Передача агенту"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Позиция заявки агента"
        verbose_name_plural = "Позиции заявки агента"
        indexes = [
            models.Index(fields=["cart"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} x {self.quantity_requested}"

    def clean(self):
        # нельзя чужую компанию/филиал
        if self.cart_id and self.product_id:
            if self.product.company_id != self.cart.company_id:
                raise ValidationError({"product": "Товар другой компании."})
            if self.cart.branch_id and self.product.branch_id not in (None, self.cart.branch_id):
                raise ValidationError({"product": "Товар другого филиала."})
        if self.quantity_requested < 1:
            raise ValidationError({"quantity_requested": "Количество должно быть ≥ 1."})

        # если корзина уже SUBMITTED/APPROVED/REJECTED — менять нельзя
        if self.cart_id and self.cart.status != AgentRequestCart.Status.DRAFT:
            raise ValidationError("Нельзя редактировать позиции не в черновике.")

    def save(self, *args, **kwargs):
        """
        Правила:
        - Пока корзина DRAFT:
            можно свободно создавать/редактировать строку (агент наполняет заявку).
        - Когда корзина уже SUBMITTED:
            агент руками редактировать не может,
            но backend при submit()/approve() может:
            * зафиксировать gift_quantity / total_quantity / price_snapshot
            * проставить ссылку subreal после фактической выдачи
            Эти апдейты приходят с явным update_fields.
        - Когда корзина APPROVED или REJECTED:
            больше никаких изменений.
        """

        if not self.cart_id:
            raise ValidationError("Строка без cart не сохраняется.")

        cart_status = self.cart.status
        creating = self._state.adding  # True если это новая позиция (INSERT)

        # ===== 1. Корзина ещё черновик -> полный доступ
        if cart_status == AgentRequestCart.Status.DRAFT:
            # зафиксируем price_snapshot если не задан
            if not self.price_snapshot:
                # Product.price может иметь 3 знака после запятой, а снапшот — 2.
                self.price_snapshot = _money(self.product.price if self.product_id else Decimal("0"))
            else:
                self.price_snapshot = _money(self.price_snapshot)
            # обычная валидация
            self.full_clean()
            return super().save(*args, **kwargs)

        # ===== 2. Корзина SUBMITTED -> только служебные апдейты бекэнда
        if cart_status == AgentRequestCart.Status.SUBMITTED:
            # агент не может добавлять новые позиции
            if creating:
                raise ValidationError("Нельзя добавлять позиции после отправки заявки.")

            allowed_fields = kwargs.get("update_fields")

            if allowed_fields:
                allowed_fields = set(allowed_fields)

                # набор полей, которые мы разрешаем менять после сабмита:
                allowed_service_fields = {
                    "subreal",          # линковка позиции к созданной передаче
                    "updated_at",
                    "gift_quantity",    # фиксируем подарок
                    "total_quantity",   # фиксируем итоговую выдачу
                    "price_snapshot",   # фиксируем цену на момент заявки
                }

                # если ВСЕ поля, которые хотят сохранить — из разрешённого списка,
                # то даём сохранить без full_clean (чтобы не упасть на статусе).
                if allowed_fields.issubset(allowed_service_fields):
                    # если апдейтим price_snapshot после submit — приводим к денежному формату (2 знака)
                    if "price_snapshot" in allowed_fields:
                        self.price_snapshot = _money(self.price_snapshot)
                    return super().save(*args, **kwargs)

                # кто-то пытается поменять product, quantity_requested и т.д.
                raise ValidationError("Редактирование позиций после отправки запрещено.")

            # если update_fields не задан (т.е. кто-то делает .save() без ограничений) — не даём
            raise ValidationError("Редактирование позиций после отправки запрещено.")

        # ===== 3. Корзина APPROVED / REJECTED -> вообще нельзя трогать
        if cart_status in (AgentRequestCart.Status.APPROVED, AgentRequestCart.Status.REJECTED):
            raise ValidationError("Заявка уже обработана. Изменения позиций запрещены.")

        # safety net
        raise ValidationError(f"Нельзя сохранить позицию при статусе {cart_status!r}.")
