from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, F
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.files.base import ContentFile
from PIL import Image
import io, uuid

from apps.users.models import Company, Branch


# ==========================
# Клиент кафе
# ==========================
class CafeClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='cafe_clients', related_query_name='cafe_client',
        verbose_name='Компания',
    )
    # NEW: глобальный или филиальный клиент
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name='cafe_clients', verbose_name='Филиал',
        null=True, blank=True, db_index=True
    )

    name = models.CharField('Имя', max_length=255, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True, db_index=True)
    notes = models.TextField('Заметки', blank=True)

    class Meta:
        verbose_name = 'Клиент кафе'
        verbose_name_plural = 'Клиенты кафе'
        constraints = [
            # телефон уникален в рамках филиала
            models.UniqueConstraint(
                fields=('branch', 'phone'),
                name='uniq_cafeclient_phone_per_branch',
                condition=Q(branch__isnull=False),
            ),
            # и отдельно — глобально в рамках компании
            models.UniqueConstraint(
                fields=('company', 'phone'),
                name='uniq_cafeclient_phone_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'phone']),
            models.Index(fields=['company', 'branch', 'phone']),
            models.Index(fields=['company', 'name']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.name or self.phone or f'Клиент {str(self.id)[:8]}'


# ==========================
# Зоны и столы
# ==========================
class Zone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='zones', verbose_name='Компания'
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='zones',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    title = models.CharField(max_length=255, verbose_name="Зона")

    class Meta:
        verbose_name = 'Зона'
        verbose_name_plural = 'Зоны'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'title'),
                name='uniq_zone_title_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'title'),
                name='uniq_zone_title_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'title']),
            models.Index(fields=['company', 'branch', 'title']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.title


class Table(models.Model):
    class Status(models.TextChoices):
        FREE = 'free', 'Свободен'
        BUSY = 'busy', 'Занят'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='tables', verbose_name='Компания'
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='tables',
        verbose_name='Филиал', null=True, blank=True, db_index=True
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
        constraints = [
            # номер уникален в зоне
            models.UniqueConstraint(fields=('zone', 'number'), name='uniq_table_number_per_zone'),
            # дополнительно: уникальность номера в филиале/глобально
            models.UniqueConstraint(
                fields=('branch', 'number'),
                name='uniq_table_number_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'number'),
                name='uniq_table_number_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'number']),
            models.Index(fields=['company', 'branch', 'number']),
            models.Index(fields=['zone']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.zone and self.zone.company_id != self.company_id:
            raise ValidationError({'zone': 'Зона принадлежит другой компании.'})
        if self.zone and (self.zone.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'zone': 'Зона принадлежит другому филиалу.'})

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
        Company, on_delete=models.CASCADE, related_name='bookings', verbose_name='Компания'
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='bookings',
        verbose_name='Филиал', null=True, blank=True, db_index=True
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
        'Статус', max_length=16, choices=Status.choices, default=Status.BOOKED, db_index=True
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Бронь'
        verbose_name_plural = 'Брони'
        ordering = ['-date', '-time']
        constraints = [
            # слот уникален для стола (company не нужен, table уникален сам по себе)
            models.UniqueConstraint(
                fields=['table', 'date', 'time'],
                name='uniq_booking_table_slot',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'date', 'time']),
            models.Index(fields=['company', 'branch', 'date', 'time']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['table', 'date', 'time']),
        ]

    def clean(self):
        # company согласованность
        if self.company_id:
            if self.table and self.table.company_id != self.company_id:
                raise ValidationError({'table': 'Стол принадлежит другой компании.'})

        # branch согласованность
        if self.branch_id:
            if self.table and self.table.branch_id not in (None, self.branch_id):
                raise ValidationError({'table': 'Стол принадлежит другому филиалу.'})
            if self.table and self.table.zone and self.table.zone.branch_id not in (None, self.branch_id):
                raise ValidationError({'table': 'Зона стола принадлежит другому филиалу.'})

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
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='cafe_warehouse',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    title = models.CharField(max_length=255, verbose_name="Название")
    unit = models.CharField(max_length=255, verbose_name="Ед. изм.")
    remainder = models.CharField(max_length=255, verbose_name="Остаток")
    minimum = models.CharField(max_length=255, verbose_name="Минимум")

    class Meta:
        verbose_name = 'Склад'
        verbose_name_plural = 'Склады'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'title'),
                name='uniq_warehouse_title_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'title'),
                name='uniq_warehouse_title_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'title']),
            models.Index(fields=['company', 'branch', 'title']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return f"{self.title} - осталось {self.remainder}"


class Purchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_purchases', verbose_name='Компания'
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='cafe_purchases',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    supplier = models.CharField(max_length=255, verbose_name="Поставщик")
    positions = models.CharField(max_length=255, verbose_name="Позиций")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')

    class Meta:
        verbose_name = 'Закупка'
        verbose_name_plural = 'Закупки'
        indexes = [
            models.Index(fields=['company']),
            models.Index(fields=['company', 'branch']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return f"{self.supplier} - cумма:{self.price}"


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="menu_categories", verbose_name="Компания"
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='menu_categories',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    title = models.CharField("Название категории", max_length=100)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'title'),
                name='uniq_category_title_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'title'),
                name='uniq_category_title_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'title']),
            models.Index(fields=['company', 'branch', 'title']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.title


class MenuItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="menu_items", verbose_name="Компания"
    )
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='menu_items',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    title = models.CharField("Название", max_length=255)
    category = models.ForeignKey(
        "Category", on_delete=models.CASCADE,
        related_name="items", verbose_name="Категория"
    )
    price = models.DecimalField("Цена", max_digits=10, decimal_places=2)
    is_active = models.BooleanField("Активно в продаже", default=True)

    image = models.ImageField("Изображение", upload_to="menu_items/", blank=True, null=True)

    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Позиция меню"
        verbose_name_plural = "Меню"
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'title'),
                name='uniq_menuitem_title_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'title'),
                name='uniq_menuitem_title_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'title']),
            models.Index(fields=['company', 'branch', 'title']),
            models.Index(fields=['category']),
            models.Index(fields=['company', 'is_active']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.category and self.category.company_id != self.company_id:
            raise ValidationError({'category': 'Категория принадлежит другой компании.'})
        if self.category and (self.category.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'category': 'Категория другого филиала.'})

    def __str__(self):
        return f"{self.title} ({self.category})"

    def save(self, *args, **kwargs):
        """
        При сохранении автоматически конвертируем картинку в WebP.
        """
        if self.image and hasattr(self.image, 'file'):
            img = Image.open(self.image)
            img = img.convert("RGB")
            filename = f"{uuid.uuid4().hex}.webp"
            buffer = io.BytesIO()
            img.save(buffer, format="WEBP", quality=80)
            buffer.seek(0)
            self.image.save(filename, ContentFile(buffer.read()), save=False)
        super().save(*args, **kwargs)


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
        indexes = [
            models.Index(fields=['menu_item']),
            models.Index(fields=['product']),
        ]

    def clean(self):
        if self.menu_item.company_id != self.product.company_id:
            raise ValidationError({'product': 'Склад из другой компании.'})
        if (self.menu_item.branch_id or None) != (self.product.branch_id or None):
            raise ValidationError({'product': 'Склад другого филиала.'})

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
    # NEW
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='cafe_orders',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    table = models.ForeignKey(
        Table, on_delete=models.PROTECT, related_name='orders', verbose_name='Стол'
    )
    client = models.ForeignKey(
        CafeClient, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders', verbose_name='Клиент',
    )
    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='served_orders', verbose_name='Официант'
    )
    guests = models.PositiveIntegerField('Количество гостей', default=1)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'branch', 'created_at']),
            models.Index(fields=['client', 'created_at']),
        ]

    def __str__(self):
        return f'Order {str(self.id)[:8]} — {self.table}'

    def clean(self):
        # согласованность компании
        if self.company_id:
            if self.table and self.table.company_id != self.company_id:
                raise ValidationError({'table': 'Стол принадлежит другой компании.'})
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({'client': 'Клиент принадлежит другой компании.'})
        # согласованность филиала
        if self.branch_id:
            if self.table and self.table.branch_id not in (None, self.branch_id):
                raise ValidationError({'table': 'Стол другого филиала.'})
            if self.client and self.client.branch_id not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # company синхронизируется с заказом в save()
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='cafe_order_items', verbose_name='Компания', editable=False,
    )
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ'
    )
    menu_item = models.ForeignKey(
        'MenuItem', on_delete=models.PROTECT, related_name='order_items', verbose_name='Позиция меню'
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

    def clean(self):
        # согласованность company/branch с заказом
        if self.order and self.menu_item:
            if self.order.company_id != self.menu_item.company_id:
                raise ValidationError({'menu_item': 'Позиция меню из другой компании.'})
            if (self.order.branch_id or None) != (self.menu_item.branch_id or None):
                raise ValidationError({'menu_item': 'Позиция меню другого филиала.'})

    def save(self, *args, **kwargs):
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
    # NEW: сохраняем филиал
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL,
        related_name='cafe_order_history', verbose_name='Филиал (ref)',
        null=True, blank=True
    )
    client = models.ForeignKey(
        CafeClient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_history', verbose_name='Клиент'
    )
    original_order_id = models.UUIDField(verbose_name='ID исходного заказа', unique=True)
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
            models.Index(fields=['company', 'branch', 'created_at']),
            models.Index(fields=['client', 'created_at']),
            models.Index(fields=['original_order_id']),
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

    oh = OrderHistory.objects.create(
        company=instance.company,
        branch=instance.branch,  # NEW
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
