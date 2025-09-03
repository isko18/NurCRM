from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from apps.users.models import Company
import uuid


# ==========================
# Клиент кафе
# ==========================
class CafeClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cafe_clients',
        related_query_name='cafe_client',
        verbose_name='Компания',
    )
    name = models.CharField('Имя', max_length=255, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    notes = models.TextField('Заметки', blank=True)

    class Meta:
        verbose_name = 'Клиент кафе'
        verbose_name_plural = 'Клиенты кафе'
        unique_together = (('company', 'phone'),)
        indexes = [
            models.Index(fields=['company', 'phone']),
            models.Index(fields=['company', 'name']),
        ]

    def __str__(self):
        return self.name or self.phone or f'Клиент {str(self.id)[:8]}'


# ==========================
# Зоны и столы
# ==========================
class Zone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='zone', verbose_name='Компания'
    )
    title = models.CharField(max_length=255, verbose_name="Зона")

    class Meta:
        verbose_name = 'Зона'
        verbose_name_plural = 'Зоны'

    def __str__(self):
        return self.title


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

    class Meta:
        verbose_name = 'Стол'
        verbose_name_plural = 'Столы'

    def __str__(self):
        return f"Стол {self.number} (мест: {self.places})"


# ==========================
# Бронирования столов
# ==========================
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


# ==========================
# Склад, закупки, меню
# ==========================
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_warehouse', verbose_name='Компания'
    )
    title = models.CharField(max_length=255, verbose_name="Название")
    unit = models.CharField(max_length=255, verbose_name="Ед. изм.")
    remainder = models.CharField(max_length=255, verbose_name="Остаток")
    minimum = models.CharField(max_length=255, verbose_name="Минимум")

    class Meta:
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'

    def __str__(self):
        return f"{self.title} - осталось {self.remainder}"


class Purchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_purchase', verbose_name='Компания'
    )
    supplier = models.CharField(max_length=255, verbose_name="Поставщик")
    positions = models.CharField(max_length=255, verbose_name="Позиций")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')

    class Meta:
        verbose_name = 'Закупка'
        verbose_name_plural = 'Закупки'

    def __str__(self):
        return f"{self.supplier} - cумма:{self.price}"


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


# ==========================
# Заказы
# ==========================
class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_orders', verbose_name='Компания'
    )
    table = models.ForeignKey(
        Table, on_delete=models.PROTECT, related_name='orders', verbose_name='Стол'
    )
    client = models.ForeignKey(
        CafeClient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        verbose_name='Клиент',
    )
    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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
            models.Index(fields=['client', 'created_at']),
        ]

    def __str__(self):
        return f'Order {str(self.id)[:8]} — {self.table}'

    def clean(self):
        # согласованность компании у связанных сущностей
        if self.company_id:
            if self.table and self.table.company_id != self.company_id:
                raise ValidationError({'table': 'Стол принадлежит другой компании.'})
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({'client': 'Клиент принадлежит другой компании.'})


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # company синхронизируется с заказом в save()
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cafe_order_items',
        verbose_name='Компания',
        editable=False,
    )
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE,
        related_name='items', verbose_name='Заказ'
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


# ==========================
# Архив заказов (сохраняем историю при удалении)
# ==========================
class OrderHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_order_history', verbose_name='Компания'
    )
    client = models.ForeignKey(
        CafeClient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_history', verbose_name='Клиент'
    )
    original_order_id = models.UUIDField(verbose_name='ID исходного заказа')
    table = models.ForeignKey(
        Table, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_history', verbose_name='Стол (ref)'
    )
    table_number = models.IntegerField(null=True, blank=True, verbose_name='Номер стола (снапшот)')
    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='served_orders_history', verbose_name='Официант (ref)'
    )
    waiter_label = models.CharField('Метка официанта', max_length=255, blank=True)
    guests = models.PositiveIntegerField('Гостей', default=1)
    created_at = models.DateTimeField('Создано (в заказе)')
    archived_at = models.DateTimeField('Архивировано', auto_now_add=True)

    class Meta:
        verbose_name = 'Архив заказа'
        verbose_name_plural = 'Архив заказов'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['client', 'created_at']),
            models.Index(fields=['original_order_id']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['original_order_id'], name='uniq_orderhistory_original'),
        ]

    def __str__(self):
        return f'OrderHistory {str(self.original_order_id)[:8]} — клиент: {self.client or "—"}'


class OrderItemHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_history = models.ForeignKey(
        OrderHistory, on_delete=models.CASCADE, related_name='items', verbose_name='Архив заказа'
    )
    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archived_items', verbose_name='Позиция (ref)'
    )
    menu_item_title = models.CharField('Название позиции (снапшот)', max_length=255)
    menu_item_price = models.DecimalField('Цена (снапшот)', max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField('Кол-во', default=1)

    class Meta:
        verbose_name = 'Архив позиции заказа'
        verbose_name_plural = 'Архив позиций заказа'
        indexes = [models.Index(fields=['order_history'])]

    def __str__(self):
        return f'{self.menu_item_title} × {self.quantity}'


# ==========================
# Сигнал: архивируем заказ перед удалением
# ==========================
@receiver(pre_delete, sender=Order)
def archive_order_before_delete(sender, instance: Order, **kwargs):
    # метка официанта
    waiter_label = ''
    if instance.waiter_id:
        full = getattr(instance.waiter, 'get_full_name', lambda: '')() or ''
        email = getattr(instance.waiter, 'email', '') or ''
        waiter_label = full or email or str(instance.waiter_id)

    # шапка архива
    oh = OrderHistory.objects.create(
        company=instance.company,
        client=instance.client,
        original_order_id=instance.id,
        table=instance.table,
        table_number=(instance.table.number if instance.table_id else None),
        waiter=instance.waiter,
        waiter_label=waiter_label,
        guests=instance.guests,
        created_at=instance.created_at,
        archived_at=timezone.now(),
    )

    # позиции архива (снапшот названия и цены)
    items = []
    for it in instance.items.select_related('menu_item'):
        items.append(OrderItemHistory(
            order_history=oh,
            menu_item=it.menu_item,
            menu_item_title=it.menu_item.title,
            menu_item_price=it.menu_item.price,
            quantity=it.quantity,
        ))
    if items:
        OrderItemHistory.objects.bulk_create(items)
