from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from PIL import Image, ImageOps
import io, uuid
from decimal import Decimal
from django.db import IntegrityError

from apps.users.models import Company, Branch


# ==========================
# Задача кухни + уведомления
# ==========================
class Kitchen(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="kitchens", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name="kitchens", verbose_name="Филиал",
        null=True, blank=True, db_index=True
    )

    title = models.CharField("Кухня", max_length=255)
    number = models.PositiveIntegerField("Номер", validators=[MinValueValidator(1)], db_index=True)

    class Meta:
        verbose_name = "Кухня"
        verbose_name_plural = "Кухни"
        constraints = [
            models.UniqueConstraint(
                fields=("branch", "number"),
                name="uniq_kitchen_number_per_branch",
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "number"),
                name="uniq_kitchen_number_global_per_company",
                condition=Q(branch__isnull=True),
            ),
            models.UniqueConstraint(
                fields=("branch", "title"),
                name="uniq_kitchen_title_per_branch",
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("company", "title"),
                name="uniq_kitchen_title_global_per_company",
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "number"]),
            models.Index(fields=["company", "branch", "number"]),
            models.Index(fields=["company", "title"]),
        ]
        ordering = ["number", "title"]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал принадлежит другой компании."})

    def __str__(self):
        return f"{self.number}. {self.title}"

class KitchenTask(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'В ожидании'
        IN_PROGRESS = 'in_progress', 'В работе'
        READY = 'ready', 'Готово'
        CANCELLED = 'cancelled', 'Отменено'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='kitchen_tasks', verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='kitchen_tasks',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='kitchen_tasks', verbose_name='Заказ')
    order_item = models.ForeignKey('OrderItem', on_delete=models.CASCADE, related_name='kitchen_tasks', verbose_name='Позиция заказа')
    menu_item = models.ForeignKey('MenuItem', on_delete=models.PROTECT, related_name='kitchen_tasks', verbose_name='Позиция меню')

    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='kitchen_waiter_tasks', verbose_name='Официант (ref)'
    )
    cook = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='kitchen_cook_tasks', verbose_name='Повар'
    )

    # если у OrderItem.quantity > 1 — создаём столько задач, нумеруем:
    unit_index = models.PositiveSmallIntegerField('Номер порции', default=1, validators=[MinValueValidator(1)])

    status = models.CharField('Статус', max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    started_at = models.DateTimeField('Взято в работу', null=True, blank=True)
    finished_at = models.DateTimeField('Готово', null=True, blank=True)

    class Meta:
        verbose_name = 'Задача кухни'
        verbose_name_plural = 'Задачи кухни'
        constraints = [
            models.UniqueConstraint(fields=['order_item', 'unit_index'], name='uniq_kitchen_task_per_unit'),
        ]
        indexes = [
            models.Index(fields=['company', 'branch', 'status', 'created_at']),
            models.Index(fields=['cook', 'status']),
            models.Index(fields=['waiter']),
            models.Index(fields=['order']),
        ]

    def clean(self):
        # согласованность принадлежности
        if (
            self.order.company_id != self.company_id
            or self.order_item.company_id != self.company_id
            or self.menu_item.company_id != self.company_id
        ):
            raise ValidationError('Несогласованные company у KitchenTask.')
        if (self.order.branch_id or None) != (self.branch_id or None):
            raise ValidationError('Несогласованный branch у KitchenTask.')
        if self.order_item.order_id != self.order_id or self.order_item.menu_item_id != self.menu_item_id:
            raise ValidationError('order_item не соответствует order/menu_item.')

    def __str__(self):
        return f'[{self.get_status_display()}] {self.menu_item.title} (стол {self.order.table.number})'


class NotificationCafe(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='cafe_notifications',            # было: 'notifications'
        related_query_name='cafe_notification',
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cafe_notifications',            # безопасно дать своё имя
        related_query_name='cafe_notification',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cafe_notifications',            # было: 'notifications'
        related_query_name='cafe_notification',
    )

    type = models.CharField(max_length=64, default='kitchen_ready')
    message = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['recipient', 'is_read', 'created_at']),
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        return f'Notify -> {self.recipient_id}: {self.message}'

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
    # глобальный или филиальный клиент
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
            # телефон уникален в рамках филиала (кроме пустых значений)
            models.UniqueConstraint(
                fields=('branch', 'phone'),
                name='uniq_cafeclient_phone_per_branch',
                condition=Q(branch__isnull=False) & ~Q(phone=''),
            ),
            # и отдельно — глобально в рамках компании (кроме пустых значений)
            models.UniqueConstraint(
                fields=('company', 'phone'),
                name='uniq_cafeclient_phone_global_per_company',
                condition=Q(branch__isnull=True) & ~Q(phone=''),
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
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='tables',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    zone = models.ForeignKey(
        Zone, on_delete=models.CASCADE, related_name="tables", verbose_name="Зона"
    )
    number = models.IntegerField(verbose_name="Номер стола")
    places = models.PositiveSmallIntegerField(verbose_name="Кол-во мест", validators=[MinValueValidator(1)])
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.FREE, verbose_name='Статус'
    )

    class Meta:
        verbose_name = 'Стол'
        verbose_name_plural = 'Столы'
        constraints = [
            models.UniqueConstraint(fields=('zone', 'number'), name='uniq_table_number_per_zone'),
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
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='bookings',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    guest = models.CharField('Гость', max_length=255)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    date = models.DateField('Дата')
    time = models.TimeField('Время')
    guests = models.PositiveSmallIntegerField('Гостей', default=1, validators=[MinValueValidator(1)])
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
            models.UniqueConstraint(fields=['table', 'date', 'time'], name='uniq_booking_table_slot'),
        ]
        indexes = [
            models.Index(fields=['company', 'date', 'time']),
            models.Index(fields=['company', 'branch', 'date', 'time']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['table', 'date', 'time']),
        ]

    def clean(self):
        if self.company_id:
            if self.table and self.table.company_id != self.company_id:
                raise ValidationError({'table': 'Стол принадлежит другой компании.'})
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
        dt = datetime.combine(self.date, self.time)
        return timezone.make_aware(dt, timezone.get_current_timezone())


# ==========================
# Склад, закупки, меню
# ==========================
class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_warehouse', verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='cafe_warehouse',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    title = models.CharField(max_length=255, verbose_name="Название")
    unit = models.CharField(max_length=255, verbose_name="Ед. изм.")
    remainder = models.CharField(max_length=255, verbose_name="Остаток")
    minimum = models.CharField(max_length=255, verbose_name="Минимум")
    unit_price = models.DecimalField(
        "Цена за единицу", max_digits=12, decimal_places=2,
        default=Decimal("0.00"),
        help_text="Закупочная цена за единицу измерения (для расчета себестоимости блюд)"
    )

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
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='cafe_purchases',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    supplier = models.CharField(max_length=255, verbose_name="Поставщик")
    positions = models.CharField(max_length=255, verbose_name="Позиций")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')
    created_at = models.DateTimeField('Создано', auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True, null=True, blank=True)
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
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='menu_items',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    kitchen = models.ForeignKey(
        "Kitchen",
        on_delete=models.PROTECT,
        related_name="menu_items",
        verbose_name="Кухня",
        null=True, blank=True,
    )
    title = models.CharField("Название", max_length=255)
    category = models.ForeignKey(
        "Category", on_delete=models.CASCADE,
        related_name="items", verbose_name="Категория"
    )
    price = models.DecimalField("Цена продажи", max_digits=10, decimal_places=2)
    is_active = models.BooleanField("Активно в продаже", default=True)

    # Себестоимость и расходы
    cost_price = models.DecimalField(
        "Себестоимость", max_digits=12, decimal_places=2,
        default=Decimal("0.00"),
        help_text="Автоматически рассчитывается из ингредиентов + прочие расходы"
    )
    vat_percent = models.DecimalField(
        "НДС (%)", max_digits=5, decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Процент налога на добавленную стоимость"
    )
    other_expenses = models.DecimalField(
        "Прочие расходы", max_digits=12, decimal_places=2,
        default=Decimal("0.00"),
        help_text="Дополнительные расходы на блюдо (упаковка, доставка и т.д.)"
    )

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
            models.Index(fields=["kitchen"]),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.category and self.category.company_id != self.company_id:
            raise ValidationError({'category': 'Категория принадлежит другой компании.'})
        if self.category and (self.category.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'category': 'Категория другого филиала.'})

        # НОВОЕ: проверка кухни
        if self.kitchen:
            if self.kitchen.company_id != self.company_id:
                raise ValidationError({"kitchen": "Кухня принадлежит другой компании."})
            if (self.kitchen.branch_id or None) != (self.branch_id or None):
                raise ValidationError({"kitchen": "Кухня другого филиала."})

    def __str__(self):
        return f"{self.title} ({self.category})"

    def recalc_cost_price(self):
        """
        Пересчитать себестоимость блюда на основе ингредиентов и прочих расходов.
        Себестоимость = сумма(количество ингредиента * цена за единицу) + прочие расходы
        """
        ingredients_cost = Decimal("0.00")
        for ingredient in self.ingredients.select_related('product').all():
            unit_price = ingredient.product.unit_price or Decimal("0.00")
            amount = ingredient.amount or Decimal("0.00")
            ingredients_cost += unit_price * amount
        
        self.cost_price = ingredients_cost + (self.other_expenses or Decimal("0.00"))
        return self.cost_price

    @property
    def vat_amount(self):
        """Сумма НДС от цены продажи"""
        if not self.vat_percent or not self.price:
            return Decimal("0.00")
        return (self.price * self.vat_percent / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def profit(self):
        """Прибыль = Цена продажи - Себестоимость - НДС"""
        return (self.price or Decimal("0.00")) - (self.cost_price or Decimal("0.00")) - self.vat_amount

    @property
    def margin_percent(self):
        """Маржа в процентах = (Прибыль / Цена) * 100"""
        if not self.price or self.price == 0:
            return Decimal("0.00")
        return ((self.profit / self.price) * Decimal("100")).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        """
        Автоконвертация изображения в WebP с учётом EXIF-ориентации.
        Конвертируем только если:
        - есть image
        - или объект новый
        - или файл изображения изменился
        """
        should_convert = False

        if self.image and hasattr(self.image, "file"):
            if not self.pk:
                should_convert = True
            else:
                try:
                    old = MenuItem.objects.only("image").get(pk=self.pk)
                    old_name = getattr(old.image, "name", "") or ""
                    new_name = getattr(self.image, "name", "") or ""
                    # если имя/путь изменились — считаем, что файл новый
                    should_convert = old_name != new_name
                except MenuItem.DoesNotExist:
                    should_convert = True

        if should_convert:
            img = Image.open(self.image)
            img = ImageOps.exif_transpose(img).convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="WEBP", quality=80, method=6)
            buffer.seek(0)
            filename = f"{uuid.uuid4().hex}.webp"
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
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        CLOSED = "closed", "Закрыт"
        CANCELLED = "cancelled", "Отменен"
        
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_orders', verbose_name='Компания'
    )
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
    guests = models.PositiveIntegerField('Количество гостей', default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )

    is_paid = models.BooleanField("Оплачен", default=False, db_index=True)
    paid_at = models.DateTimeField("Оплачен в", null=True, blank=True)
    payment_method = models.CharField("Способ оплаты", max_length=32, blank=True, default="")

    total_amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField("Скидка", max_digits=12, decimal_places=2, default=Decimal("0"))

    updated_at = models.DateTimeField("Обновлено", auto_now=True)

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
    
    def recalc_total(self):
        total = Decimal("0")
        for it in self.items.select_related("menu_item").all():
            total += (it.menu_item.price or Decimal("0")) * Decimal(it.quantity or 0)
        self.total_amount = total
        return total
    
    def clean(self):
        if self.company_id:
            if self.table and self.table.company_id != self.company_id:
                raise ValidationError({'table': 'Стол принадлежит другой компании.'})
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({'client': 'Клиент принадлежит другой компании.'})
        if self.branch_id:
            if self.table and self.table.branch_id not in (None, self.branch_id):
                raise ValidationError({'table': 'Стол другого филиала.'})
            if self.client and self.client.branch_id not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент другого филиала.'})


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    quantity = models.PositiveIntegerField('Кол-во', default=1, validators=[MinValueValidator(1)])

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
    STATUS_CHOICES = [
        ("open", "Открыт"),
        ("closed", "Закрыт"),
        ("cancelled", "Отменен"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='cafe_order_history', verbose_name='Компания'
    )
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
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="closed", db_index=True)
    is_paid = models.BooleanField(default=False, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=32, blank=True, default="")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

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
# Сигналы: архив + синхронизация задач кухни
# ==========================
@receiver(pre_delete, sender=Order)
def archive_order_before_delete(sender, instance: Order, **kwargs):
    with transaction.atomic():
        # если уже архив есть — просто выходим
        if OrderHistory.objects.filter(original_order_id=instance.id).exists():
            return

        waiter_label = ""
        if instance.waiter_id:
            full = getattr(instance.waiter, "get_full_name", lambda: "")() or ""
            email = getattr(instance.waiter, "email", "") or ""
            waiter_label = full or email or str(instance.waiter_id)

        try:
            oh = OrderHistory.objects.create(
                company=instance.company,
                branch=instance.branch,
                client=instance.client,
                original_order_id=instance.id,
                table=instance.table,
                table_number=(instance.table.number if instance.table_id else None),
                waiter=instance.waiter,
                waiter_label=waiter_label,
                guests=instance.guests,
                created_at=instance.created_at,
                archived_at=timezone.now(),
                status=instance.status,
                is_paid=instance.is_paid,
                paid_at=instance.paid_at,
                payment_method=instance.payment_method,
                total_amount=instance.total_amount,
                discount_amount=instance.discount_amount,
            )
        except IntegrityError:
            return

        items = [
            OrderItemHistory(
                order_history=oh,
                menu_item=it.menu_item,
                menu_item_title=it.menu_item.title,
                menu_item_price=it.menu_item.price,
                quantity=it.quantity,
            )
            for it in instance.items.select_related("menu_item")
        ]
        if items:
            OrderItemHistory.objects.bulk_create(items)



@receiver(post_save, sender=OrderItem)
def ensure_kitchen_tasks_for_order_item(sender, instance: "OrderItem", created, **kwargs):
    """
    Гарантируем наличие unit_index=1..quantity для KitchenTask.
    - Создаём отсутствующие unit_index (не по count).
    - При уменьшении quantity удаляем лишние только в PENDING.
    """
    need = int(instance.quantity or 0)
    if need <= 0:
        return

    qs = KitchenTask.objects.filter(order_item=instance).only("id", "unit_index", "status")
    existing = set(qs.values_list("unit_index", flat=True))

    missing = [idx for idx in range(1, need + 1) if idx not in existing]
    if missing:
        to_create = [
            KitchenTask(
                company=instance.company,
                branch=instance.order.branch,
                order=instance.order,
                order_item=instance,
                menu_item=instance.menu_item,
                waiter=instance.order.waiter,
                unit_index=idx,
            )
            for idx in missing
        ]
        # atomic + ignore_conflicts: переживаем гонки
        with transaction.atomic():
            KitchenTask.objects.bulk_create(to_create, ignore_conflicts=True)

    # если quantity уменьшили — удаляем лишние PENDING
    KitchenTask.objects.filter(
        order_item=instance,
        unit_index__gt=need,
        status=KitchenTask.Status.PENDING,
    ).delete()


class InventorySession(models.Model):
    """
    Акт инвентаризации склада в рамках компании/филиала.
    Содержит список замеров по отдельным позициям Warehouse.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="cafe_inventory_sessions", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_inventory_sessions",
        verbose_name="Филиал",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_inventory_created",
        verbose_name="Создан пользователем",
    )
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    confirmed_at = models.DateTimeField("Подтверждено", null=True, blank=True)
    is_confirmed = models.BooleanField("Подтвержден", default=False, db_index=True)

    class Meta:
        verbose_name = "Инвентаризация"
        verbose_name_plural = "Инвентаризации"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "branch", "is_confirmed"]),
        ]

    def __str__(self):
        who = f" / {self.branch}" if self.branch_id else " / GLOBAL"
        return f"Инвентаризация{who} ({self.created_at:%Y-%m-%d %H:%M})"

    def confirm(self, user=None):
        """
        Применяет фактические остатки к Warehouse.remainder.
        ВНИМАНИЕ: у вас remainder=CharField, поэтому пишем строкой Decimal.
        """
        if self.is_confirmed:
            return
        for item in self.items.select_related("product"):
            # фиксируем актуальный остаток в карточке товара
            # переводим Decimal -> строка (без форматирования единиц)
            item.product.remainder = str(item.actual_qty)
            item.product.save(update_fields=["remainder"])
        self.is_confirmed = True
        self.confirmed_at = timezone.now()
        self.save(update_fields=["is_confirmed", "confirmed_at"])


class InventoryItem(models.Model):
    """
    Строка инвентаризации: одна позиция склада (Warehouse).
    expected_qty — учетный остаток на момент замера
    actual_qty — фактический остаток
    difference = actual - expected (считается автоматически)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        InventorySession, on_delete=models.CASCADE,
        related_name="items", verbose_name="Сессия",
    )
    product = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE,
        related_name="inventory_items", verbose_name="Товар",
    )
    expected_qty = models.DecimalField("Ожидалось", max_digits=12, decimal_places=3)
    actual_qty = models.DecimalField("Факт", max_digits=12, decimal_places=3)
    difference = models.DecimalField("Разница", max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "Строка инвентаризации"
        verbose_name_plural = "Строки инвентаризации"
        constraints = [
            models.UniqueConstraint(fields=["session", "product"], name="uniq_inventory_line_per_session_product"),
        ]
        indexes = [
            models.Index(fields=["session"]),
            models.Index(fields=["product"]),
        ]

    def save(self, *args, **kwargs):
        self.difference = (self.actual_qty or Decimal("0")) - (self.expected_qty or Decimal("0"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.title}: {self.expected_qty} -> {self.actual_qty} ({self.difference})"


# ==========================
# ИНВЕНТАРИЗАЦИЯ ОБОРУДОВАНИЯ
# ==========================
class Equipment(models.Model):
    """
    Единица оборудования (кофемашина, холодильник, ноутбук и т.п.)
    """
    class Condition(models.TextChoices):
        GOOD = "good", "Исправно"
        REPAIR = "repair", "На ремонте"
        BROKEN = "broken", "Списано"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="cafe_equipments", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_equipments", verbose_name="Филиал",
    )
    title = models.CharField("Название", max_length=255)
    serial_number = models.CharField("Серийный номер", max_length=255, blank=True)
    category = models.CharField("Категория", max_length=100, blank=True)
    purchase_date = models.DateField("Дата покупки", null=True, blank=True)
    price = models.DecimalField("Цена", max_digits=12, decimal_places=2, null=True, blank=True)
    condition = models.CharField("Состояние", max_length=16, choices=Condition.choices, default=Condition.GOOD)
    is_active = models.BooleanField("Активно", default=True)
    notes = models.TextField("Заметки", blank=True)

    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        indexes = [
            models.Index(fields=["company", "branch"]),
            models.Index(fields=["company", "category"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        sn = f" / SN:{self.serial_number}" if self.serial_number else ""
        return f"{self.title}{sn}"


class EquipmentInventorySession(models.Model):
    """
    Акт инвентаризации оборудования в компании/филиале.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="cafe_equipment_inventory_sessions", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_equipment_inventory_sessions", verbose_name="Филиал",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_equipment_inventory_created", verbose_name="Создан пользователем",
    )
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    confirmed_at = models.DateTimeField("Подтверждено", null=True, blank=True)
    is_confirmed = models.BooleanField("Подтвержден", default=False, db_index=True)

    class Meta:
        verbose_name = "Инвентаризация оборудования"
        verbose_name_plural = "Инвентаризации оборудования"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "branch", "is_confirmed"]),
        ]

    def __str__(self):
        who = f" / {self.branch}" if self.branch_id else " / GLOBAL"
        return f"Инв. оборудования{who} ({self.created_at:%Y-%m-%d %H:%M})"

    def confirm(self, user=None):
        """
        Применяет состояние оборудования из строк акта.
        """
        if self.is_confirmed:
            return
        for item in self.items.select_related("equipment"):
            eq = item.equipment
            eq.condition = item.condition
            if not item.is_present:
                eq.is_active = False
            eq.save(update_fields=["condition", "is_active"])
        self.is_confirmed = True
        self.confirmed_at = timezone.now()
        self.save(update_fields=["is_confirmed", "confirmed_at"])


class EquipmentInventoryItem(models.Model):
    """
    Строка инвентаризации оборудования.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        EquipmentInventorySession, on_delete=models.CASCADE,
        related_name="items", verbose_name="Сессия",
    )
    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE,
        related_name="inventory_items", verbose_name="Оборудование",
    )
    is_present = models.BooleanField("В наличии", default=True)
    condition = models.CharField("Состояние", max_length=16, choices=Equipment.Condition.choices, default=Equipment.Condition.GOOD)
    notes = models.TextField("Заметки", blank=True)

    class Meta:
        verbose_name = "Строка инв. оборудования"
        verbose_name_plural = "Строки инв. оборудования"
        constraints = [
            models.UniqueConstraint(fields=["session", "equipment"], name="uniq_eq_inventory_line_per_session_equipment"),
        ]
        indexes = [
            models.Index(fields=["session"]),
            models.Index(fields=["equipment"]),
        ]

    def __str__(self):
        return f"{self.equipment} — {self.get_condition_display()} ({'есть' if self.is_present else 'нет'})"