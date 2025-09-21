from django.db import models
import uuid, secrets
from apps.users.models import User, Company
from django.conf import settings
from mptt.models import MPTTModel, TreeForeignKey
from django.db import models
from decimal import Decimal
from django.utils import timezone
from django.core.validators import MinValueValidator
from apps.construction.models import Department
from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from decimal import Decimal, ROUND_HALF_UP

_Q2 = Decimal("0.01")
def _money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(_Q2, rounding=ROUND_HALF_UP)

class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='contacts')
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

    def __str__(self):
        return f"{self.name} ({self.client_company})"


class Pipeline(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='pipelines')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pipelines')

    name = models.CharField(max_length=128)
    stages = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


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

    def __str__(self):
        return f"{self.title} ({self.status})"


class Task(models.Model):
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('in_progress', 'В процессе'),
        ('done', 'Выполнена'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tasks')
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

    def __str__(self):
        return f"{self.title} — {self.status}"

class Order(models.Model):
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('pending', 'В процессе'),
        ('completed', 'Завершён'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='orders')

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

    def __str__(self):
        return f"{self.order_number} — {self.customer_name}"

    @property
    def total(self):
        return sum(item.total for item in self.items.all())

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

class GlobalBrand(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True, verbose_name='Название бренда')
    parent = TreeForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Родительский бренд'
    )

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
    parent = TreeForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Родительская категория'
    )

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
    brand = models.ForeignKey(
        GlobalBrand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    category = models.ForeignKey(
        GlobalCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Глобальный товар"
        verbose_name_plural = "Глобальные товары"

    def __str__(self):
        return f"{self.name} ({self.barcode or 'без штрих-кода'})"



class ProductCategory(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    name = models.CharField(max_length=128, verbose_name='Название категории')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='categories', verbose_name='Компания')
    parent = TreeForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Родительская категория'
    )

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        unique_together = ('name', 'company')
        verbose_name = 'Категория товара'
        verbose_name_plural = 'Категории товаров'

    def __str__(self):
        return self.name


class ProductBrand(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    name = models.CharField(max_length=128, verbose_name='Название бренда')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='brands', verbose_name='Компания')
    parent = TreeForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Родительский бренд'
    )

    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        unique_together = ('name', 'company')
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        PENDING  = "pending",  "Ожидание"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отказ"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='products',
        verbose_name='Компания'
    )
    client = models.ForeignKey(
        "Client",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="products",
        verbose_name="Клиент"
    )

    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    brand = models.ForeignKey(ProductBrand, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True)

    quantity = models.PositiveIntegerField(default=0)

    # 💰 цены
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Закупочная цена")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Розничная цена")

    # 🏷️ статус
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        db_index=True, 
        blank=True, null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'barcode')
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        indexes = [
            models.Index(fields=['company', 'status']),   # удобно для фильтров по статусу
        ]

    def __str__(self):
        return self.name

class ItemMake(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey("users.Company", on_delete=models.PROTECT)
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Товар"
    )

    name = models.CharField("Название", max_length=255)
    price = models.DecimalField("Цена", max_digits=10, decimal_places=2, default=0)
    unit = models.CharField("Единица измерения", max_length=50)  # шт, кг, л и т.д.
    quantity = models.PositiveIntegerField("Количество", default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Единица товара"
        verbose_name_plural = "Единицы товаров"
        indexes = [
            models.Index(fields=["company", "product"]),
        ]
        unique_together = ("company", "product", "name")

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"
    
    
class Cart(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CHECKED_OUT = "checked_out", "Завершена"
        ABANDONED = "abandoned", "Отменена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="carts",
        verbose_name="Компания"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="carts",
        verbose_name="Пользователь"
    )
    session_key = models.CharField(max_length=64, null=True, blank=True, verbose_name="Ключ сессии")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE,
        verbose_name="Статус"
    )

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма без скидок и налогов")
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма скидки")
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма налога")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Итого")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["session_key"]),
        ]
        verbose_name = "Корзина"
        verbose_name_plural = "Корзины"

    def _calc_tax(self, taxable_base: Decimal) -> Decimal:
        # если налогов нет — верните 0; иначе подставьте свою логику/ставку
        TAX_RATE = Decimal("0.00")
        return taxable_base * TAX_RATE

    def recalc(self):
        subtotal = Decimal("0")
        discount_total = Decimal("0")

        for it in self.items.select_related("product"):
            qty = Decimal(it.quantity or 0)

            # Базовая (прайсовая) цена товара; если по какой-то причине её нет, fallback на unit_price
            base_unit = getattr(it.product, "price", None)
            if base_unit is None:
                base_unit = it.unit_price or Decimal("0")

            line_base = base_unit * qty            # до скидок
            line_actual = (it.unit_price or 0) * qty  # после скидок

            subtotal += line_base
            diff = line_base - line_actual
            if diff > 0:
                discount_total += diff

        taxable_base = subtotal - discount_total
        tax_total = self._calc_tax(taxable_base)

        self.subtotal = _money(subtotal)
        self.discount_total = _money(discount_total)
        self.tax_total = _money(tax_total)
        self.total = _money(self.subtotal - self.discount_total + self.tax_total)

        self.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="cart_items", verbose_name="Компания")
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", verbose_name="Корзина")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="cart_items", verbose_name="Товар")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена за единицу")

    class Meta:
        unique_together = ("cart", "product")
        verbose_name = "Товар в корзине"
        verbose_name_plural = "Товары в корзине"

    def save(self, *args, **kwargs):
        if not self.unit_price:
            self.unit_price = self.product.price
        super().save(*args, **kwargs)
        self.cart.recalc()

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"



class Sale(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PAID = "paid", "Оплачен"
        CANCELED = "canceled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sales", verbose_name="Компания")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="sales",
        verbose_name="Пользователь"
    )
    client = models.ForeignKey(  
        "Client",
        on_delete=models.SET_NULL, 
        null=True,
        blank=True,
        related_name="sale",
        verbose_name="Клиент"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NEW,
        verbose_name="Статус"
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма без скидок и налогов")
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма скидки")
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Сумма налога")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Итого")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата оплаты")

    class Meta:
        verbose_name = "Продажа"
        verbose_name_plural = "Продажи"

    def mark_paid(self):
        self.status = Sale.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "paid_at"])

    def __str__(self):
        return f"Продажа {self.id} ({self.get_status_display()})"


class SaleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sale_items", verbose_name="Компания")
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items", verbose_name="Продажа")
    product = models.ForeignKey(Product,blank=True, null=True, on_delete=models.SET_NULL, related_name="sale_items", verbose_name="Товар")
    name_snapshot = models.CharField(max_length=255, verbose_name="Название товара (снимок)")
    barcode_snapshot = models.CharField(max_length=64, null=True, blank=True, verbose_name="Штрихкод (снимок)")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Цена за единицу")
    quantity = models.PositiveIntegerField(verbose_name="Количество")

    class Meta:
        verbose_name = "Товар в продаже"
        verbose_name_plural = "Товары в продаже"

    def __str__(self):
        return f"{self.name_snapshot} x{self.quantity}"


class MobileScannerToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="mobile_tokens", verbose_name="Компания")
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="mobile_tokens", verbose_name="Корзина")
    token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="Токен")
    expires_at = models.DateTimeField(verbose_name="Срок действия")

    class Meta:
        verbose_name = "Мобильный токен для сканера"
        verbose_name_plural = "Мобильные токены для сканера"

    @classmethod
    def issue(cls, cart, ttl_minutes=10):
        return cls.objects.create(
            company=cart.company,
            cart=cart,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    def is_valid(self):
        return timezone.now() <= self.expires_at

    def __str__(self):
        return f"Токен для корзины {self.cart_id} (действует до {self.expires_at})"
    
class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='order_items', verbose_name='Компания')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items', verbose_name='Товар')
    quantity = models.PositiveIntegerField(verbose_name='Количество')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена за единицу', editable=False)
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Итоговая сумма', editable=False)

    class Meta:
        verbose_name = 'Товар в заказе'
        verbose_name_plural = 'Товары в заказе'

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')

    rating = models.PositiveSmallIntegerField()
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} — {self.rating}★"


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='notifications')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')

    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email}: {self.message[:30]}..."


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

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    config = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='inactive')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Интеграция'
        verbose_name_plural = 'Интеграции'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.type} — {self.status}"

class Analytics(models.Model):
    TYPE_CHOICES = [
        ('sales', 'Продажи'),
        ('activity', 'Активность'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='analytics')

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    data = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Аналитика'
        verbose_name_plural = 'Аналитика'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.type} — {self.data.get('metric', '')}"


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='events')
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

    def __str__(self):
        return f"{self.title} — {self.datetime.strftime('%Y-%m-%d %H:%M')}" 
    
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID склада')
    name = models.CharField(max_length=255, verbose_name='Название склада')
    location = models.CharField(max_length=255, blank=True, null=True, verbose_name='Местоположение')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='warehouses', verbose_name='Компания')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'
        ordering = ['created_at']

    
class WarehouseEvent(models.Model):
    STATUS_CHOICES = [
        ('draf', 'Черновик'),
        ('conducted', 'Проведен'),
        ('cancelled', 'Отменен'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID события')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='events', verbose_name='Склад')
    responsible_person = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='responsible_warehouse_events', verbose_name='Ответственное лицо')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name='Статус события')
    client_name = models.CharField(max_length=128, verbose_name='Имя клиента')
    title = models.CharField(max_length=255, verbose_name='Название события')
    description = models.TextField(blank=True, null=True, verbose_name='Описание события')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    event_date = models.DateTimeField(verbose_name='Дата события')
    participants = models.ManyToManyField(User, related_name='warehouse_events', verbose_name='Участники')

    # Добавлены поля created_at и updated_at
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания события')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления события')

    def __str__(self):
        return f"{self.title} — {self.event_date.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = 'Складское событие'
        verbose_name_plural = 'Складские события'
        ordering = ['event_date']
          

class Client(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        
    class StatusClient(models.TextChoices):
        CLIENT = "client", "клиент"
        SUPPLIERS = "suppliers", "Поставщики"
        IMPLEMENTERS = "implementers", "Реализаторы"
        CONTRACTOR = "contractor", "Подрядчик"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID клиента")
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="clients", verbose_name="Компания"
    )
    type = models.CharField(
        "Тип клиента", max_length=16, choices=StatusClient.choices, default=StatusClient.CLIENT, null=True, blank=True)
    enterprise = models.CharField("Предприятие O", max_length=255, blank=True, null=True)
    full_name = models.CharField("ФИО", max_length=255)
    phone = models.CharField("Телефон", max_length=32)
    email = models.EmailField("Почта", blank=True)          # по желанию
    date = models.DateField("Дата", null=True, blank=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.NEW
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "phone"]),
            models.Index(fields=["company", "status"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"
    
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
    )
    client = models.ForeignKey(
        "Client",
        on_delete=models.CASCADE,
        related_name="deals",
        verbose_name="Клиент",
    )
    title = models.CharField("Название сделки", max_length=255)
    kind = models.CharField("Тип сделки", max_length=16, choices=Kind.choices, default=Kind.SALE)

 
    amount = models.DecimalField("Сумма договора", max_digits=12, decimal_places=2, default=0)  # как на карточке слева
    prepayment = models.DecimalField("Предоплата", max_digits=12, decimal_places=2, default=0)  # карточка по центру

    # --- параметры долга/рассрочки ---
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
            models.Index(fields=["company", "kind"]),
        ]

    # ===== вычисляемые поля для UI =====
    @property
    def debt_amount(self) -> Decimal:
        """Размер долга = сумма договора - предоплата."""
        return (self.amount or Decimal("0")) - (self.prepayment or Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        return self.installments.filter(paid_on__isnull=False).aggregate(
            s=Sum("amount")
        )["s"] or Decimal("0")

    @property
    def remaining_debt(self) -> Decimal:
        """Остаток долга для карточки справа."""
        return (self.debt_amount - self.paid_total).quantize(Decimal("0.01"))

    @property
    def monthly_payment(self) -> Decimal:
        """Ежемесячный платёж = долг / месяцев (для модалки)."""
        if not self.debt_months or self.debt_months == 0:
            return Decimal("0.00")
        return (self.debt_amount / Decimal(self.debt_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # утилита: пересобрать график
    def rebuild_installments(self):
        if self.kind != ClientDeal.Kind.DEBT or not self.debt_months or self.debt_months == 0:
            self.installments.all().delete()
            return

        total = self.debt_amount
        if total <= 0:
            self.installments.all().delete()
            return

        # стартовая дата: через месяц от создания, если не задано
        start = self.first_due_date or (timezone.now().date() + relativedelta(months=+1))

        self.installments.all().delete()

        base = (total / Decimal(self.debt_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        paid = Decimal("0.00")
        items = []

        for i in range(1, self.debt_months + 1):
            # чтобы сойтись до копейки — последний платёж = остаток
            amount_i = (total - paid) if i == self.debt_months else base
            paid += amount_i
            due = start + relativedelta(months=+(i - 1))
            items.append(DealInstallment(
                deal=self,
                number=i,
                due_date=due,
                amount=amount_i,
                balance_after=(total - paid).quantize(Decimal("0.01")),
            ))

        DealInstallment.objects.bulk_create(items)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # после каждого сохранения держим график в актуальном виде
        self.rebuild_installments()


class DealInstallment(models.Model):
    deal = models.ForeignKey(
        ClientDeal,
        on_delete=models.CASCADE,
        related_name="installments",
        verbose_name="Сделка",
    )
    number = models.PositiveSmallIntegerField("№")
    due_date = models.DateField("Срок оплаты")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)
    balance_after = models.DecimalField("Остаток", max_digits=12, decimal_places=2)
    paid_on = models.DateField("Оплачен", blank=True, null=True)

    class Meta:
        verbose_name = "Платёж по графику"
        verbose_name_plural = "График платежей"
        ordering = ["deal", "number"]
        unique_together = [("deal", "number")]
        
class Bid(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PROCESSING = "processing", "В обработке"
        REFUSAL = "refusal", "Отказ"
        THINKS = "thinks", "Думает"
        CONNECTED = "connected", "Подключено"
        
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=255,verbose_name="ФИО")
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
    company = models.CharField(max_length=255,verbose_name="Компания")
    text = models.TextField(verbose_name="Обращение",)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    status = models.CharField("Тип сделки", max_length=16, choices=Status.choices, default=Status.NEW)

    
    def __str__(self):
        return f"{self.full_name} - {self.phone} - {self.text}"

    class Meta:
        verbose_name = "Заявка на соц. сети"
        verbose_name_plural = "Заявки на соц. сети"
        ordering = ["-created_at"]
        
class TransactionRecord(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        APPROVED = 'approved', 'Подтверждена'
        CANCELLED = 'cancelled', 'Отменена'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='transaction_records', verbose_name='Компания'
    )

    description = models.TextField(verbose_name="Обращение",)

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,  # не блокируем удаление отдела
        null=True, blank=True,
        related_name='transaction_records',
        verbose_name='Отдел'
    )

    name = models.CharField('Наименование', max_length=255)
    amount = models.DecimalField(
        'Сумма', max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))]
    )
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
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'department', 'date']),  # ← удобно для отчётов по отделам
        ]

    def __str__(self):
        dep = f", отдел: {self.department.name}" if self.department_id else ""
        return f'{self.name} — {self.amount} ({self.get_status_display()}{dep})'

    # Согласованность company ↔ department
    def clean(self):
        if self.department_id and self.company_id and self.department.company_id != self.company_id:
            raise ValidationError({'department': 'Отдел принадлежит другой компании.'})

    def save(self, *args, **kwargs):
        # если отдел задан, но company ещё нет — подставим
        if self.department_id and not self.company_id:
            self.company_id = self.department.company_id
        self.full_clean(exclude=None)
        super().save(*args, **kwargs)


class ContractorWork(models.Model):
    class ContractorType(models.TextChoices):
        LLC = "llc", "ОсОО / ООО"
        IP  = "ip",  "ИП"
        
    class Status(models.TextChoices):
        PROCESS = "process", "В процессе"
        COMPLETED  = "completed",  "Завершен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contractor_works", verbose_name="Компания")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="contractor_works", verbose_name="Отдел")

    # основное
    title = models.CharField("Наименование", max_length=255)
    contractor_name = models.CharField("Имя подрядчика", max_length=255)
    contractor_phone = models.CharField("Телефон", max_length=32)
    contractor_entity_type = models.CharField(
        "Тип юрлица", max_length=8, choices=ContractorType.choices, null=True, blank=True
    )
    contractor_entity_name = models.CharField(
        "Название его ООО/ИП", max_length=255, null=True, blank=True
    )

    amount = models.DecimalField(
        "Сумма договора", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))]
    )

    status = models.CharField(
        "Статус", max_length=255, choices=Status.choices, null=True, blank=True
    )
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
            models.Index(fields=["company", "start_date"]),
            models.Index(fields=["company", "end_date"]),
        ]

    def __str__(self):
        return f"{self.title} — {self.contractor_name}"

    # простые проверки согласованности
    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Дата окончания не может быть раньше даты начала."})
        if self.planned_completion_date and self.start_date and self.planned_completion_date < self.start_date:
            raise ValidationError({"planned_completion_date": "Плановая дата завершения не может быть раньше начала."})

    @property
    def duration_days(self):
        """Длительность по факту (в днях), если заданы обе даты."""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return None


class Debt(models.Model):
    """Карточка долга одного человека (телефон уникален в компании)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="debts", verbose_name="Компания")
    name = models.CharField("Имя", max_length=255)
    phone = models.CharField("Телефон", max_length=32)
    amount = models.DecimalField("Сумма долга", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0"))])

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Долг"
        verbose_name_plural = "Долги"
        ordering = ["-created_at"]
        unique_together = (("company", "phone"),)   # телефон уникален в рамках компании
        indexes = [
            models.Index(fields=["company", "phone"]),
            models.Index(fields=["company", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} — {self.phone} ({self.amount} c)"

    # агрегаты для таблицы
    @property
    def paid_total(self) -> Decimal:
        return self.payments.aggregate(s=models.Sum("amount"))["s"] or Decimal("0")

    @property
    def balance(self) -> Decimal:
        """Остаток долга = сумма долга − оплачено."""
        return (self.amount - self.paid_total).quantize(Decimal("0.01"))

    # удобный хелпер для оплаты (можно дергать из сервисов/вьюх)
    def add_payment(self, amount: Decimal, paid_at=None, note: str = ""):
        payment = DebtPayment(debt=self, company=self.company, amount=amount,
                              paid_at=paid_at or timezone.now().date(), note=note)
        payment.full_clean()
        payment.save()
        return payment


class DebtPayment(models.Model):
    """Оплата долга (частичная/полная)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="debt_payments", verbose_name="Компания")
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="payments", verbose_name="Долг")

    amount = models.DecimalField("Сумма оплаты", max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    paid_at = models.DateField("Дата оплаты", default=timezone.localdate)
    note = models.CharField("Комментарий", max_length=255, blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Оплата долга"
        verbose_name_plural = "Оплаты долга"
        ordering = ["-paid_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "paid_at"]),
            models.Index(fields=["debt", "paid_at"]),
        ]

    def __str__(self):
        return f"{self.amount} c от {self.paid_at} ({self.debt.name})"

    # согласованность и запрет переплаты
    def clean(self):
        # компания у платежа = компании долга
        if self.debt and self.company_id and self.debt.company_id != self.company_id:
            raise ValidationError({"company": "Компания платежа должна совпадать с компанией долга."})

        # запрет переплаты
        if self.debt_id and self.amount:
            # если редактируют существующий платеж, исключим его из суммы
            qs = self.debt.payments.exclude(pk=self.pk) if self.pk else self.debt.payments
            already = qs.aggregate(s=models.Sum("amount"))["s"] or Decimal("0")
            rest = (self.debt.amount - already)
            if self.amount > rest:
                raise ValidationError({"amount": f"Сумма оплаты превышает остаток долга ({rest} c)."})

    def save(self, *args, **kwargs):
        if self.debt_id and not self.company_id:
            self.company_id = self.debt.company_id
        self.full_clean()
        super().save(*args, **kwargs)
        
        
class ObjectItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="object_items")
    name = models.CharField("Наименование", max_length=255)
    description = models.TextField("Описание", blank=True)
    price = models.DecimalField("Цена", max_digits=12, decimal_places=2)
    date = models.DateField("Дата", default=timezone.localdate)
    quantity = models.PositiveIntegerField("Количество", default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return self.name


class ObjectSale(models.Model):
    """Шапка продажи — клиент обязателен."""
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        PAID = "paid", "Оплачена"
        CANCELED = "canceled", "Отменена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="object_sales", verbose_name="Компания")
    client = models.ForeignKey("main.Client",blank=True, null=True, on_delete=models.SET_NULL, related_name="object_sales", verbose_name="Клиент")

    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.NEW)
    sold_at = models.DateField("Дата продажи", default=timezone.localdate)
    note = models.CharField("Комментарий", max_length=255, blank=True)

    subtotal = models.DecimalField("Сумма", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["-sold_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "sold_at"]),
            models.Index(fields=["company", "client"]),
        ]

    def __str__(self):
        return f"Продажа {self.id} — {self.get_status_display()}"

    def recalc(self):
        total = sum((i.unit_price * i.quantity for i in self.items.all()), Decimal("0"))
        self.subtotal = total
        self.save(update_fields=["subtotal"])


class ObjectSaleItem(models.Model):
    """Строка продажи с “снимком” названия и цены на момент продажи."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(ObjectSale, on_delete=models.CASCADE, related_name="items", verbose_name="Продажа")
    object_item = models.ForeignKey(ObjectItem, on_delete=models.PROTECT, related_name="sold_items", verbose_name="Объект")

    name_snapshot = models.CharField("Наименование (снимок)", max_length=255)
    unit_price = models.DecimalField("Цена за единицу", max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField("Кол-во", validators=[MinValueValidator(1)])

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating:
            # подставим снимок
            if not self.name_snapshot:
                self.name_snapshot = self.object_item.name
            if not self.unit_price:
                self.unit_price = self.object_item.price
        super().save(*args, **kwargs)
        # уменьшим остаток и пересчитаем сумму (упрощённая логика)
        if creating:
            self.object_item.quantity = max(0, self.object_item.quantity - self.quantity)
            self.object_item.save(update_fields=["quantity"])
        self.sale.recalc()