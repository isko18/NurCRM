import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, F
from django.utils import timezone
from django.conf import settings

from apps.users.models import Company, Branch  # Branch берём из твоей users-модели


# ===========================
# BarberProfile
# ===========================
class BarberProfile(models.Model):
    """Мастер барбершопа."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='barbers', verbose_name='Компания'
    )
    # ⬇️ новый FK на филиал (может быть NULL для «глобальных» мастеров)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='barbers',
        verbose_name='Филиал', null=True, blank=True, db_index=True
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
            models.Index(fields=['company', 'branch', 'is_active']),
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        # company ↔ branch.company согласованность (если branch задан)
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

    @property
    def is_busy_now(self) -> bool:
        """Проверка занятости мастера в текущий момент."""
        now = timezone.now()
        return self.appointments.filter(
            status__in=[Appointment.Status.BOOKED, Appointment.Status.CONFIRMED],
            start_at__lte=now, end_at__gt=now
        ).exists()


# ===========================
# Service
# ===========================
class Service(models.Model):
    """Услуга барбершопа."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='services', verbose_name='Компания'
    )
    # ⬇️ может быть глобальной (branch=NULL) или филиальной
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='services',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField(max_length=128, verbose_name='Название')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')
    is_active = models.BooleanField(default=True, verbose_name='Активна')

    class Meta:
        verbose_name = 'Услуга'
        verbose_name_plural = 'Услуги'
        # заменяем unique_together на условные ограничения:
        constraints = [
            # уникальность названия в рамках филиала
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uniq_service_name_per_branch',
                condition=Q(branch__isnull=False),
            ),
            # и отдельно — для глобальных услуг в рамках компании
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uniq_service_name_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'branch', 'is_active']),
        ]

    def __str__(self):
        return f'{self.name} — {self.price}₽'

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ===========================
# Client
# ===========================
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
    # ⬇️ клиент может быть глобальным (NULL) либо закреплённым за филиалом
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='barber_clients',
        verbose_name='Филиал', null=True, blank=True, db_index=True
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
        constraints = [
            # телефон уникален в рамках филиала
            models.UniqueConstraint(
                fields=('branch', 'phone'),
                name='uniq_client_phone_per_branch',
                condition=Q(branch__isnull=False),
            ),
            # и отдельно — для глобальных клиентов в рамках компании
            models.UniqueConstraint(
                fields=('company', 'phone'),
                name='uniq_client_phone_global_per_company',
                condition=Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ===========================
# Appointment
# ===========================
class Appointment(models.Model):
    """Запись на услугу."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Забронировано"
        CONFIRMED = "confirmed", "Подтверждено"
        COMPLETED = "completed", "Завершено"
        CANCELED = "canceled", "Отменено"
        NO_SHOW = "no_show", "Не пришёл"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="Компания",
    )
    # ⬇️ запись может быть глобальной (NULL) или филиальной
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="Филиал", null=True, blank=True, db_index=True
    )

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="Клиент",
    )
    # мастер — это User (в твоей модели User есть FK company, так что проверка валидна)
    barber = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="Мастер",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="Услуга",
    )

    start_at = models.DateTimeField(verbose_name="Начало")
    end_at = models.DateTimeField(verbose_name="Конец")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.BOOKED, db_index=True
    )
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Запись"
        verbose_name_plural = "Записи"
        indexes = [
            models.Index(fields=["company", "start_at"]),
            models.Index(fields=["company", "branch", "start_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["barber", "start_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(end_at__gt=F("start_at")),
                name="appointment_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.client} → {self.service} ({self.start_at:%Y-%m-%d %H:%M})"

    def clean(self):
        # пересечения по мастеру
        if self.barber_id and self.start_at and self.end_at:
            overlaps = Appointment.objects.filter(
                barber_id=self.barber_id,
                status__in=[self.Status.BOOKED, self.Status.CONFIRMED],
            ).exclude(id=self.id).filter(
                start_at__lt=self.end_at,
                end_at__gt=self.start_at,
            )
            if overlaps.exists():
                raise ValidationError("У мастера уже есть запись в это время.")

        # согласованность по компании
        if self.company_id:
            if self.client and self.client.company_id != self.company_id:
                raise ValidationError({"client": "Клиент принадлежит другой компании."})
            if self.barber and getattr(self.barber, "company_id", None) != self.company_id:
                raise ValidationError({"barber": "Мастер принадлежит другой компании."})
            if self.service and self.service.company_id != self.company_id:
                raise ValidationError({"service": "Услуга принадлежит другой компании."})

        # согласованность по филиалу (если задан)
        if self.branch_id:
            # клиент может быть глобальным (NULL) или того же филиала
            if self.client and self.client.branch_id not in (None, self.branch_id):
                raise ValidationError({"client": "Клиент принадлежит другому филиалу."})
            # услуга — глобальная или того же филиала
            if self.service and self.service.branch_id not in (None, self.branch_id):
                raise ValidationError({"service": "Услуга принадлежит другому филиалу."})
            # при наличии модели членства мастера в филиале — проверь её здесь

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


# ===========================
# Folder
# ===========================
class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='barber_folders', verbose_name='Компания'
    )
    # ⬇️ папка может быть глобальной или филиальной
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='barber_folders',
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
        # учитываем branch при уникальности
        constraints = [
            models.UniqueConstraint(
                fields=('company', 'branch', 'parent', 'name'),
                name='uniq_folder_company_branch_parent_name',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'parent', 'name']),
            models.Index(fields=['company', 'branch', 'parent', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

        # родитель должен быть той же компании и того же филиала (или оба NULL)
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительская папка другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительская папка другого филиала.'})


# ===========================
# Document
# ===========================
class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # UUID PK
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="barber_documents", verbose_name="Компания"
    )
    # ⬇️ документ может быть глобальным или филиальным
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="barber_documents",
        verbose_name="Филиал", null=True, blank=True, db_index=True
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
            models.Index(fields=["company", "branch"]),
        ]

    def __str__(self):
        return self.name or self.file.name

    def clean(self):
        # company ↔ branch.company
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})

        # Папка и документ — одна компания и один и тот же филиал (или оба глобальные)
        if self.folder.company_id != self.company_id:
            raise ValidationError({'folder': 'Папка принадлежит другой компании.'})
        if (self.folder.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'folder': 'Папка принадлежит другому филиалу.'})
