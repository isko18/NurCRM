from django.db import models
from apps.users.models import Company
import uuid
from django.conf import settings

# Create your models here.
class Zone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='zone', verbose_name='Компания'
    )
    title = models.CharField(max_length=255, verbose_name="Зона")
    
    def __str__(self):
        return self.title
    
    class Meta:
        verbose_name = 'Зона'
        verbose_name_plural = 'Зоны'
        
        
class Table(models.Model):
    class Status(models.TextChoices):
        FREE = 'free', 'Свободен'
        BUSY = 'busy', 'Занят'
        
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='table', verbose_name='Компания'
    )
    zone = models.ForeignKey(
        Zone, on_delete=models.CASCADE, related_name="tables", verbose_name="Зона"
    )
    number = models.IntegerField(verbose_name="Номер стола")
    places = models.IntegerField(verbose_name="Кол-во мест")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.FREE, verbose_name='Статус'
    )
    
    def __str__(self):
        return f"Стол {self.number} (мест: {self.places})"

    
    class Meta:
        verbose_name = 'Стол'
        verbose_name_plural = 'Столы'


class Booking(models.Model):
    class Status(models.TextChoices):
        BOOKED = 'booked', 'Забронировано'
        ARRIVED = 'arrived', 'Пришли'
        NO_SHOW = 'no_show', 'Не пришли'
        CANCELLED = 'cancelled', 'Отменено'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='booking', verbose_name='Компания'
    )
    guest = models.CharField('Гость', max_length=255)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    date = models.DateField('Дата')
    time = models.TimeField('Время')
    guests = models.PositiveSmallIntegerField('Гостей', default=0)
    table = models.ForeignKey(
        Table, on_delete=models.PROTECT,
        related_name='bookings', verbose_name='Стол'
    )
    status = models.CharField(
        'Статус', max_length=16, choices=Status.choices, default=Status.BOOKED
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Бронь'
        verbose_name_plural = 'Брони'
        ordering = ['-date', '-time']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'table', 'date', 'time'],
                name='uniq_booking_table_slot',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'date', 'time']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self):
        return f'{self.date} {self.time} — {self.guest} ({self.table})'

    @property
    def start_at(self):
        from datetime import datetime
        return datetime.combine(self.date, self.time)
    
    
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_warehouse', verbose_name='Компания'
    )
    title = models.CharField(max_length=255, verbose_name="Название")
    unit = models.CharField(max_length=255, verbose_name="Ед. изм.")
    remainder = models.CharField(max_length=255, verbose_name="Остаток")
    minimum = models.CharField(max_length=255, verbose_name="Минимум")
    
    
    def __str__(self):
        return f"{self.title} - осталось {self.remainder}"
    
    class Meta:
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'
        
class Purchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_purchase', verbose_name='Компания'
    )
    supplier = models.CharField(max_length=255, verbose_name="Поставщик")
    positions = models.CharField(max_length=255, verbose_name="Позиций")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')
    
    def __str__(self):
        return f"{self.supplier} - cумма:{self.price}"

    class Meta:
        verbose_name = 'Закупка'
        verbose_name_plural = 'Закупки'
        
    
    
class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="menu_categories", verbose_name="Компания"
    )
    title = models.CharField("Название категории", max_length=100)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"

    def __str__(self):
        return self.title


class MenuItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="menu_items", verbose_name="Компания"
    )
    title = models.CharField("Название", max_length=255)
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="items", verbose_name="Категория"
    )
    price = models.DecimalField("Цена", max_digits=10, decimal_places=2)
    is_active = models.BooleanField("Активно в продаже", default=True)

    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Позиция меню"
        verbose_name_plural = "Меню"

    def __str__(self):
        return f"{self.title} ({self.category})"


class Ingredient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="ingredients", verbose_name="Блюдо"
    )
    product = models.ForeignKey(
        "Warehouse", on_delete=models.CASCADE, related_name="used_in", verbose_name="Товар со склада"
    )
    amount = models.DecimalField("Норма (в ед. товара)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Ингредиент"
        verbose_name_plural = "Ингредиенты"

    def __str__(self):
        return f"{self.product.title} ({self.amount} {self.product.unit})"
    
    
    
class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_orders', verbose_name='Компания'
    )
    table = models.ForeignKey(
        Table, on_delete=models.PROTECT, related_name='orders', verbose_name='Стол'
    )
    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # ✅ теперь это User
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='served_orders',
        verbose_name='Официант'
    )
    guests = models.PositiveIntegerField('Количество гостей', default=1)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        return f'Order {str(self.id)[:8]} — {self.table}'

class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ← добавили company (редактировать руками не нужно)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cafe_order_items',
        verbose_name='Компания',
        editable=False,
    )
    
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE,
        related_name='items',  # как уже исправляли
        verbose_name='Заказ'
    )
    menu_item = models.ForeignKey(
        'MenuItem', on_delete=models.PROTECT,
        related_name='order_items', verbose_name='Позиция меню'
    )
    quantity = models.PositiveIntegerField('Кол-во', default=1)

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'
        constraints = [
            models.UniqueConstraint(fields=['order', 'menu_item'], name='uniq_order_menuitem'),
        ]
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['order']),
        ]

    def save(self, *args, **kwargs):
        # всегда синхронизируем с заказом
        if self.order_id:
            if self.company_id and self.company_id != self.order.company_id:
                raise ValueError("company у позиции не совпадает с company заказа.")
            self.company = self.order.company
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.menu_item.title} × {self.quantity}'
