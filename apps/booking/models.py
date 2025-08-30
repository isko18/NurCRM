import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings  # используем AUTH_USER_MODEL


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

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    room = models.ForeignKey(ConferenceRoom, on_delete=models.CASCADE, null=True, blank=True, related_name='bookings')
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='bookings',
    )

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    purpose = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Бронирование'
        verbose_name_plural = 'Бронирования'
        indexes = [
            models.Index(fields=['company', 'start_time']),
        ]

    def clean(self):
        # 1) Выбран либо hotel, либо room (ровно один)
        if (self.hotel and self.room) or (not self.hotel and not self.room):
            raise ValidationError("Выберите либо гостиницу, либо комнату, но не обе одновременно.")

        # 2) Согласованность компаний
        if self.company_id:
            if self.hotel and self.hotel.company_id != self.company_id:
                raise ValidationError({'hotel': 'Отель принадлежит другой компании.'})
            if self.room and self.room.company_id != self.company_id:
                raise ValidationError({'room': 'Комната принадлежит другой компании.'})
            if self.reserved_by and getattr(self.reserved_by, 'company_id', None) \
               and self.reserved_by.company_id != self.company_id:
                raise ValidationError({'reserved_by': 'Пользователь из другой компании.'})

        # 3) Временной интервал
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError('Время окончания должно быть позже времени начала.')

    def __str__(self):
        hotel_name = self.hotel.name if self.hotel else "No Hotel"
        room_name = self.room.name if self.room else "No Room"
        reserved_by_display = getattr(self.reserved_by, 'email', None) or "Unknown"
        return f"{hotel_name} / {room_name} by {reserved_by_display}"


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
