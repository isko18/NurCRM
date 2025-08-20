from django.db import models
import uuid, secrets
from apps.users.models import User, Company
from django.conf import settings
from mptt.models import MPTTModel, TreeForeignKey
from django.db import models
from decimal import Decimal
from django.utils import timezone
from mptt.models import MPTTModel, TreeForeignKey


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


class GlobalCategory(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='global_categories', verbose_name='Компания')
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

class GlobalBrand(MPTTModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='global_brands', verbose_name='Компания')
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


class GlobalProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='global_products', verbose_name='Компания')
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=64, unique=True)
    brand = models.ForeignKey(GlobalBrand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    category = models.ForeignKey(GlobalCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Глобальный товар"
        verbose_name_plural = "Глобальные товары"

    def __str__(self):
        return f"{self.name} ({self.barcode})"



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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='products', verbose_name='Компания')
    name = models.CharField(max_length=255)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    brand = models.ForeignKey(ProductBrand, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'barcode')
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'

    def __str__(self):
        return self.name
    
    
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

    def recalc(self):
        items = self.items.all()
        self.subtotal = sum((i.quantity * i.unit_price for i in items), Decimal("0"))
        self.discount_total = Decimal("0")
        self.tax_total = Decimal("0")
        self.total = self.subtotal - self.discount_total + self.tax_total
        self.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])

    def __str__(self):
        return f"Корзина {self.id} ({self.get_status_display()})"


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="cart_items", verbose_name="Компания")
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", verbose_name="Корзина")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="cart_items", verbose_name="Товар")
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
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="sale_items", verbose_name="Товар")
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
    data = models.JSONField()  # Пример: {"metric": "total_sales", "value": 150000, "date": "2025-06-01"}

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
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('contacted', 'Связались'),
        ('interested', 'Заинтересован'),
        ('converted', 'Стал клиентом'),
        ('inactive', 'Неактивный'),
        ('lost', 'Потерян'),
        ('paid_for', 'Оплачено'),
        ('awaiting', 'Ожидает'),
        ('credit', 'Долг'),
        ('rejection', 'Отказ'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID клиента')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='clients', verbose_name='Компания')
    full_name = models.CharField(max_length=255, verbose_name='ФИО')
    phone = models.CharField(max_length=32, verbose_name='Номер телефона')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name='Статус клиента')
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    class Meta:
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'
        ordering = ['-created_at']
