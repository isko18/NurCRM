from django.db import models
from apps.users.models import Company, Branch, User
from apps.main.models import Client
import uuid


class Logistics(models.Model):
    class Status(models.TextChoices):
        DECORATED = "decorated", "Оформлен"
        TRANSIT = "transit", "В пути"
        COMPLETED = "completed", "Завершен"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="logistics",
        verbose_name="Компания",
    )

    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="crm_logistics",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Филиал",
    )

    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logistics",
        verbose_name="Клиент",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_logistics",
        verbose_name="Создал",
    )

    title = models.CharField(
        max_length=255,
        verbose_name="Название",
    )

    description = models.TextField(
        verbose_name="Описание",
        blank=True,
    )

    price_car = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Стоимость доставки",
    )

    price_service = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Стоимость услуги",
    )

    # 👉 Новое поле
    arrival_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Дата прибытия",
    )

    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.DECORATED,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Обновлено",
    )

    class Meta:
        verbose_name = "Логистика"
        verbose_name_plural = "Логистики"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
