import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings  # используем AUTH_USER_MOD
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone


# apps/booking/models.py
class BookingClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_clients',        # <-- было 'clients'
        related_query_name='booking_client',   # <-- чтобы не конфликтовал запросный псевдоним
        verbose_name='Компания',
    )
    phone = models.CharField(max_length=255, verbose_name="Номер телефона")
    name = models.CharField(max_length=255, verbose_name="Имя")
    text = models.TextField(verbose_name="Заметки", blank=True)

    class Meta:
        unique_together = (('company', 'phone'),)
        indexes = [models.Index(fields=['company', 'phone'])]

    
class Hotel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='hotels',
        verbose_name='Компания',
    )

    name = models.CharField(max_length=200)
    capacity = models.IntegerField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Отель'
        verbose_name_plural = 'Отели'
        unique_together = (('company', 'name'),)
        indexes = [models.Index(fields=['company', 'name'])]

    def __str__(self):
        return self.name

class Bed(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='beds',
        verbose_name='Компания',
    )

    name = models.CharField(max_length=200)
    capacity = models.IntegerField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Койко место'
        verbose_name_plural = 'Койки места'
        unique_together = (('company', 'name'),)
        indexes = [models.Index(fields=['company', 'name'])]

    def __str__(self):
        return self.name
    
class ConferenceRoom(models.Model):  # ← Room
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='rooms',
        verbose_name='Компания',
    )

    name = models.CharField(max_length=100)
    capacity = models.IntegerField()
    location = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    

    class Meta:
        verbose_name = 'Переговорная'
        verbose_name_plural = 'Переговорные'
        unique_together = (('company', 'name'),)
        indexes = [models.Index(fields=['company', 'name'])]

    def __str__(self):
        return self.name


class Booking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='bookings',
        verbose_name='Компания',
    )
    hotel = models.ForeignKey('Hotel', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    room = models.ForeignKey('ConferenceRoom', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    bed = models.ForeignKey('Bed', on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    client = models.ForeignKey(
        'BookingClient',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='bookings',
        verbose_name='Клиент',
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    purpose = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Бронирование'
        verbose_name_plural = 'Бронирования'
        indexes = [models.Index(fields=['company', 'start_time'])]

    def clean(self):
        chosen = [x for x in [self.hotel, self.room, self.bed] if x]
        if len(chosen) != 1:
            raise ValidationError("Выберите либо гостиницу, либо комнату, либо койко-место (ровно одно).")

        if self.company_id:
            if self.hotel and self.hotel.company_id != self.company_id:
                raise ValidationError({'hotel': 'Отель принадлежит другой компании.'})
            if self.room and self.room.company_id != self.company_id:
                raise ValidationError({'room': 'Комната принадлежит другой компании.'})
            if self.bed and self.bed.company_id != self.company_id:
                raise ValidationError({'bed': 'Койка принадлежит другой компании.'})
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({'client': 'Клиент из другой компании.'})

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError('Время окончания должно быть позже времени начала.')

    def __str__(self):
        hotel_name = self.hotel.name if self.hotel else "No Hotel"
        room_name = self.room.name if self.room else "No Room"
        bed_name = self.bed.name if self.bed else "No Bed"
        client_display = (self.client.name or self.client.phone) if self.client else "Unknown"
        return f"{hotel_name} / {room_name} / {bed_name} for {client_display}"
    
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
            models.Index(fields=['client', 'start_time']),
            models.Index(fields=['original_booking_id']),
        ]

    def __str__(self):
        who = self.client_label or (self.client and (self.client.name or self.client.phone)) or '—'
        return f'BookingHistory {str(self.original_booking_id)[:8]} — {self.target_name} для {who}'



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
        # room и manager должны относиться к той же компании, что и запись
        if self.company_id:
            if self.room and self.room.company_id != self.company_id:
                raise ValidationError({'room': 'Комната из другой компании.'})
            if self.manager and getattr(self.manager, 'company_id', None) \
               and self.manager.company_id != self.company_id:
                raise ValidationError({'manager': 'Пользователь из другой компании.'})

    def __str__(self):
        return f"{self.manager} manages {self.room}"


class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_folders',  # <-- было 'hotels' (конфликт), теперь корректно
        verbose_name='Компания',
    )

    name = models.CharField('Название папки', max_length=255)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='children', verbose_name='Родительская папка'
    )

    class Meta:
        verbose_name = 'Папка'
        verbose_name_plural = 'Папки'
        unique_together = (('company', 'parent', 'name'),)
        indexes = [models.Index(fields=['company', 'parent', 'name'])]

    def __str__(self):
        return self.name


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'users.Company',
        on_delete=models.CASCADE,
        related_name='booking_documents',  # <-- было 'hotels_docs', теперь логично и уникально
        verbose_name='Компания',
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
        indexes = [models.Index(fields=["company"])]

    def __str__(self):
        return self.name or self.file.name

    def clean(self):
        # Папка и документ должны быть одной компании
        folder_company_id = getattr(self.folder, 'company_id', None)
        if folder_company_id and self.company_id and folder_company_id != self.company_id:
            raise ValidationError({'folder': 'Папка принадлежит другой компании.'})


@receiver(pre_delete, sender=Booking)
def archive_booking_before_delete(sender, instance: Booking, **kwargs):
    # определить целевой ресурс и его поля-снимки
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
        archived_at=timezone.now(),  # auto_now_add тоже сработает, но явное не повредит
    )