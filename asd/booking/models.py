import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Q, F
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone


# ===========================
# Общие примечания по филиалам:
# - branch = NULL → глобальная запись в рамках company
# - branch != NULL → запись привязана к конкретному филиалу этой же company
# - уникальности: (branch, name) ИЛИ (company, name) при branch=NULL
# ===========================

# ---------- BookingClient ----------
class BookingClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_clients',
        related_query_name='booking_client',
        verbose_name='Компания',
    )
    # NEW: клиент может быть глобальным или филиальным
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='booking_clients',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    phone = models.CharField(max_length=255, verbose_name="Номер телефона", db_index=True)
    name = models.CharField(max_length=255, verbose_name="Имя")
    text = models.TextField(verbose_name="Заметки", blank=True)

    class Meta:
        verbose_name = 'Клиент (бронь)'
        verbose_name_plural = 'Клиенты (бронь)'
        constraints = [
            # телефон уникален в рамках филиала
            models.UniqueConstraint(
                fields=('branch', 'phone'),
                name='uniq_bookingclient_phone_per_branch',
                condition=Q(branch__isnull=False),
            ),
            # и отдельно — глобально в рамках компании
            models.UniqueConstraint(
                fields=('company', 'phone'),
                name='uniq_bookingclient_phone_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'phone']),
            models.Index(fields=['company', 'branch', 'phone']),
        ]

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.name or self.phone


# ---------- Hotel ----------
class Hotel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='hotels',
        verbose_name='Компания',
    )
    # NEW
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='hotels',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField(max_length=200)
    capacity = models.IntegerField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Отель'
        verbose_name_plural = 'Отели'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_hotel_name_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_hotel_name_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.name


# ---------- Bed ----------
class Bed(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='beds',
        verbose_name='Компания',
    )
    # NEW
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='beds',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField(max_length=200)
    capacity = models.IntegerField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Койко-место'
        verbose_name_plural = 'Койко-места'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_bed_name_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_bed_name_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.name


# ---------- ConferenceRoom ----------
class ConferenceRoom(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='rooms',
        verbose_name='Компания',
    )
    # NEW
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='rooms',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField(max_length=100)
    capacity = models.IntegerField()
    location = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Переговорная'
        verbose_name_plural = 'Переговорные'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_room_name_per_branch',
                condition=Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_room_name_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    def __str__(self):
        return self.name


# ---------- Booking ----------
class Booking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_bookings',        # <— уникально для приложения booking
        related_query_name='booking_booking',   # <— тоже уникально
        verbose_name='Компания',
    )
    # если у тебя уже добавлен branch в booking.Booking — ПРАВИМ ТАК ЖЕ:
    branch = models.ForeignKey(
        'users.Branch',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='booking_bookings',        # <— уникально
        related_query_name='booking_booking',   # <— уникально
        verbose_name='Филиал',
        db_index=True,
    )

    hotel = models.ForeignKey('Hotel', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    room = models.ForeignKey('ConferenceRoom', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    bed = models.ForeignKey('Bed', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    client = models.ForeignKey(
        'BookingClient',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bookings',
        verbose_name='Клиент',
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    purpose = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Бронирование'
        verbose_name_plural = 'Бронирования'
        indexes = [
            models.Index(fields=['company', 'start_time']),
            models.Index(fields=['company', 'branch', 'start_time']),  # NEW
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(end_time__gt=F('start_time')),
                name='booking_end_after_start',
            ),
        ]
        # Примечание: для защиты от пересечений по ресурсу можно добавить
        # ExclusionConstraint в Postgres (по желанию).

    def clean(self):
        # выбрано ровно одно целевое место
        chosen = [x for x in [self.hotel, self.room, self.bed] if x]
        if len(chosen) != 1:
            raise ValidationError("Выберите либо гостиницу, либо комнату, либо койко-место (ровно одно).")

        # company согласованность
        if self.company_id:
            if self.hotel and self.hotel.company_id != self.company_id:
                raise ValidationError({'hotel': 'Отель принадлежит другой компании.'})
            if self.room and self.room.company_id != self.company_id:
                raise ValidationError({'room': 'Комната принадлежит другой компании.'})
            if self.bed and self.bed.company_id != self.company_id:
                raise ValidationError({'bed': 'Койка принадлежит другой компании.'})
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})

        # branch согласованность (если задан)
        if self.branch_id:
            # ресурс — глобальный или того же филиала
            if self.hotel and self.hotel.branch_id not in (None, self.branch_id):
                raise ValidationError({'hotel': 'Отель принадлежит другому филиалу.'})
            if self.room and self.room.branch_id not in (None, self.branch_id):
                raise ValidationError({'room': 'Комната принадлежит другому филиалу.'})
            if self.bed and self.bed.branch_id not in (None, self.branch_id):
                raise ValidationError({'bed': 'Койка принадлежит другому филиалу.'})
            # клиент — глобальный или того же филиала
            if self.client and self.client.branch_id not in (None, self.branch_id):
                raise ValidationError({'client': 'Клиент принадлежит другому филиалу.'})

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError('Время окончания должно быть позже времени начала.')

    def __str__(self):
        hotel_name = self.hotel.name if self.hotel else "No Hotel"
        room_name = self.room.name if self.room else "No Room"
        bed_name = self.bed.name if self.bed else "No Bed"
        client_display = (self.client.name or self.client.phone) if self.client else "Unknown"
        return f"{hotel_name} / {room_name} / {bed_name} for {client_display}"


# ---------- BookingHistory ----------
class BookingHistory(models.Model):
    class TargetType(models.TextChoices):
        HOTEL = 'hotel', 'Отель'
        ROOM = 'room', 'Переговорная'
        BED = 'bed', 'Койко-место'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company', on_delete=models.CASCADE,
        related_name='booking_history', verbose_name='Компания'
    )
    # NEW: сохраняем и филиал архива (для удобства фильтрации)
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='booking_history', verbose_name='Филиал (ref)'
    )

    client = models.ForeignKey(
        'BookingClient', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='booking_history', verbose_name='Клиент (ref)'
    )
    client_label = models.CharField('Метка клиента (снапшот)', max_length=255, blank=True)

    original_booking_id = models.UUIDField('ID исходного бронирования', unique=True)

    target_type = models.CharField('Тип ресурса', max_length=16, choices=TargetType.choices)
    hotel = models.ForeignKey('Hotel', on_delete=models.SET_NULL, null=True, blank=True, related_name='archived_bookings', verbose_name='Отель (ref)')
    room = models.ForeignKey('ConferenceRoom', on_delete=models.SET_NULL, null=True, blank=True, related_name='archived_bookings', verbose_name='Комната (ref)')
    bed = models.ForeignKey('Bed', on_delete=models.SET_NULL, null=True, blank=True, related_name='archived_bookings', verbose_name='Койка (ref)')

    target_name = models.CharField('Название ресурса (снапшот)', max_length=255)
    target_price = models.DecimalField('Цена (снапшот)', max_digits=10, decimal_places=2, null=True, blank=True)

    start_time = models.DateTimeField('Начало (из брони)')
    end_time = models.DateTimeField('Окончание (из брони)')
    purpose = models.CharField('Цель (снапшот)', max_length=255, blank=True)

    archived_at = models.DateTimeField('Архивировано', auto_now_add=True)

    class Meta:
        verbose_name = 'Архив бронирования'
        verbose_name_plural = 'Архив бронирований'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['company', 'start_time']),
            models.Index(fields=['company', 'branch', 'start_time']),  # NEW
            models.Index(fields=['client', 'start_time']),
            models.Index(fields=['original_booking_id']),
        ]

    def __str__(self):
        who = self.client_label or (self.client and (self.client.name or self.client.phone)) or '—'
        return f'BookingHistory {str(self.original_booking_id)[:8]} — {self.target_name} для {who}'


# ---------- ManagerAssignment ----------
class ManagerAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='manager_assignments',
        verbose_name='Компания',
    )

    room = models.OneToOneField(ConferenceRoom, on_delete=models.CASCADE, related_name='manager_assignment')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='managed_rooms')

    class Meta:
        verbose_name = 'Назначение менеджера'
        verbose_name_plural = 'Назначения менеджеров'
        indexes = [models.Index(fields=['company'])]

    def clean(self):
        if self.company_id:
            if self.room and self.room.company_id != self.company_id:
                raise ValidationError({'room': 'Комната из другой компании.'})
            # менеджер — той же компании
            if self.manager and getattr(self.manager, 'company_id', None) != self.company_id:
                raise ValidationError({'manager': 'Пользователь из другой компании.'})
            # NEW: если у комнаты филиал задан — менеджер должен иметь доступ к этому филиалу (если у вас есть модель членства, проверьте тут)

    def __str__(self):
        return f"{self.manager} manages {self.room}"


# ---------- Folder ----------
class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_folders',
        verbose_name='Компания',
    )
    # NEW
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='booking_folders',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField('Название папки', max_length=255)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='children', verbose_name='Родительская папка'
    )

    class Meta:
        verbose_name = 'Папка'
        verbose_name_plural = 'Папки'
        constraints = [
            models.UniqueConstraint(
                fields=('company', 'branch', 'parent', 'name'),
                name='uniq_booking_folder_company_branch_parent_name',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'parent', 'name']),
            models.Index(fields=['company', 'branch', 'parent', 'name']),
        ]

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # parent та же company и тот же филиал/глобально
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительская папка другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительская папка другого филиала.'})

    def __str__(self):
        return self.name


# ---------- Document ----------
class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_documents',
        verbose_name='Компания',
    )
    # NEW
    branch = models.ForeignKey(
        'users.Branch', on_delete=models.CASCADE, related_name='booking_documents',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField("Название документа", max_length=255, blank=True)
    file = models.FileField("Файл", upload_to="documents/")
    folder = models.ForeignKey(
        Folder, on_delete=models.CASCADE, related_name="documents", verbose_name="Папка"
    )

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=['company', 'branch']),  # NEW
        ]

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # Папка и документ — одна компания и один и тот же филиал (или оба глобальные)
        if self.folder.company_id != self.company_id:
            raise ValidationError({'folder': 'Папка принадлежит другой компании.'})
        if (self.folder.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'folder': 'Папка принадлежит другому филиалу.'})

    def __str__(self):
        return self.name or self.file.name


# ---------- Архивация перед удалением ----------
@receiver(pre_delete, sender=Booking)
def archive_booking_before_delete(sender, instance: Booking, **kwargs):
    if instance.hotel_id:
        target_type = BookingHistory.TargetType.HOTEL
        target_name = instance.hotel.name
        target_price = instance.hotel.price
    elif instance.room_id:
        target_type = BookingHistory.TargetType.ROOM
        target_name = instance.room.name
        target_price = instance.room.price
    else:
        target_type = BookingHistory.TargetType.BED
        target_name = instance.bed.name
        target_price = instance.bed.price

    client_label = ''
    if instance.client_id:
        client_label = (instance.client.name or instance.client.phone or str(instance.client_id))

    BookingHistory.objects.create(
        company=instance.company,
        branch=instance.branch,  # NEW: сохраняем филиал брони в архив
        client=instance.client,
        client_label=client_label,
        original_booking_id=instance.id,
        target_type=target_type,
        hotel=instance.hotel if instance.hotel_id else None,
        room=instance.room if instance.room_id else None,
        bed=instance.bed if instance.bed_id else None,
        target_name=target_name,
        target_price=target_price,
        start_time=instance.start_time,
        end_time=instance.end_time,
        purpose=instance.purpose,
        archived_at=timezone.now(),
    )
