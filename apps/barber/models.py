import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.users.models import Company  # поправьте путь при необходимости


class BarberProfile(models.Model):
    """Мастер барбершопа."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='barbers', verbose_name='Компания'
    )

    full_name = models.CharField(max_length=128, verbose_name='ФИО')
    phone = models.CharField(max_length=32, verbose_name='Телефон', blank=True, null=True)
    extra_phone = models.CharField(max_length=32, verbose_name='Доп. телефон', blank=True, null=True)
    work_schedule = models.CharField(
        max_length=128, verbose_name='График (напр. Пн–Пт 10–18)', blank=True, null=True
    )
    is_active = models.BooleanField(default=True, verbose_name='Активен')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Мастер'
        verbose_name_plural = 'Мастера'
        indexes = [
            models.Index(fields=['company', 'is_active']),
        ]

    def __str__(self):
        return self.full_name

    @property
    def is_busy_now(self) -> bool:
        """Проверка занятости мастера в текущий момент."""
        now = timezone.now()
        return self.appointments.filter(
            status__in=[Appointment.Status.BOOKED, Appointment.Status.CONFIRMED],
            start_at__lte=now, end_at__gt=now
        ).exists()


class Service(models.Model):
    """Услуга барбершопа."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='services', verbose_name='Компания'
    )
    name = models.CharField(max_length=128, verbose_name='Название')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')
    is_active = models.BooleanField(default=True, verbose_name='Активна')

    class Meta:
        verbose_name = 'Услуга'
        verbose_name_plural = 'Услуги'
        unique_together = ('company', 'name')
        indexes = [models.Index(fields=['company', 'is_active'])]

    def __str__(self):
        return f'{self.name} — {self.price}₽'


class Client(models.Model):
    """Клиент."""
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активен'
        INACTIVE = 'inactive', 'Неактивен'
        VIP = 'vip', 'VIP'
        BLACKLIST = 'blacklist', 'В черном списке'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='barber_clients', verbose_name='Компания'
    )

    full_name = models.CharField(max_length=128, verbose_name='ФИО')
    phone = models.CharField(max_length=32, verbose_name='Телефон', db_index=True)
    email = models.EmailField(blank=True, null=True, verbose_name='Email')
    birth_date = models.DateField(blank=True, null=True, verbose_name='Дата рождения')
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, verbose_name='Статус'
    )
    notes = models.TextField(blank=True, null=True, verbose_name='Заметки')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'
        unique_together = ('company', 'phone')
        indexes = [models.Index(fields=['company', 'status'])]

    def __str__(self):
        return self.full_name


class Appointment(models.Model):
    """Запись на услугу."""
    class Status(models.TextChoices):
        BOOKED = 'booked', 'Забронировано'
        CONFIRMED = 'confirmed', 'Подтверждено'
        COMPLETED = 'completed', 'Завершено'
        CANCELED = 'canceled', 'Отменено'
        NO_SHOW = 'no_show', 'Не пришёл'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='appointments', verbose_name='Компания'
    )
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name='appointments', verbose_name='Клиент'
    )
    barber = models.ForeignKey(
        BarberProfile, on_delete=models.PROTECT, related_name='appointments', verbose_name='Мастер'
    )
    service = models.ForeignKey(
        Service, on_delete=models.PROTECT, related_name='appointments', verbose_name='Услуга'
    )

    start_at = models.DateTimeField(verbose_name='Начало')
    end_at = models.DateTimeField(verbose_name='Конец')
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.BOOKED, db_index=True
    )
    comment = models.TextField(blank=True, null=True, verbose_name='Комментарий')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Запись'
        verbose_name_plural = 'Записи'
        indexes = [
            models.Index(fields=['company', 'start_at']),
            models.Index(fields=['status']),
            models.Index(fields=['barber', 'start_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_at__gt=models.F('start_at')), name='appointment_end_after_start'
            ),
        ]

    def __str__(self):
        return f'{self.client} → {self.service} ({self.start_at:%Y-%m-%d %H:%M})'

    def clean(self):
        # проверка пересечений по мастеру
        if self.barber_id and self.start_at and self.end_at:
            overlaps = Appointment.objects.filter(
                barber_id=self.barber_id,
                status__in=[self.Status.BOOKED, self.Status.CONFIRMED],
            ).exclude(id=self.id).filter(
                start_at__lt=self.end_at,
                end_at__gt=self.start_at,
            )
            if overlaps.exists():
                raise ValidationError('У мастера уже есть запись в это время.')

        # проверка совпадения компании
        if self.company_id:
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError('Клиент принадлежит другой компании.')
            if self.barber and self.barber.company_id != self.company_id:
                raise ValidationError('Мастер принадлежит другой компании.')
            if self.service and self.service.company_id != self.company_id:
                raise ValidationError('Услуга принадлежит другой компании.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
