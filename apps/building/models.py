import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.users.models import Company


class ResidentialComplex(models.Model):
    """
    Жилой комплекс (ЖК) — объект строительной компании.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="building_residential_complexes",
        verbose_name="Компания",
    )

    name = models.CharField(max_length=255, verbose_name="Название ЖК")
    address = models.CharField(max_length=512, blank=True, null=True, verbose_name="Адрес")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")

    is_active = models.BooleanField(default=True, verbose_name="Активен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Жилой комплекс (ЖК)"
        verbose_name_plural = "Жилые комплексы (ЖК)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.name})"


def residential_complex_drawing_upload_to(instance, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"building/residential-complexes/{instance.residential_complex_id}/drawings/{uuid.uuid4().hex}.{ext}"


class ResidentialComplexDrawing(models.Model):
    """
    Чертеж, привязанный к конкретному жилому комплексу.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    residential_complex = models.ForeignKey(
        ResidentialComplex,
        on_delete=models.CASCADE,
        related_name="drawings",
        verbose_name="Жилой комплекс",
    )
    title = models.CharField(max_length=255, verbose_name="Название чертежа")
    file = models.FileField(upload_to=residential_complex_drawing_upload_to, verbose_name="Файл чертежа")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Чертеж ЖК"
        verbose_name_plural = "Чертежи ЖК"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["residential_complex"]),
            models.Index(fields=["residential_complex", "is_active"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.residential_complex.name})"


class ResidentialComplexWarehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    residential_complex = models.OneToOneField(
        ResidentialComplex,
        on_delete=models.CASCADE,
        related_name="warehouse",
        verbose_name="Жилой комплекс",
    )
    name = models.CharField(max_length=255, verbose_name="Название склада")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Склад ЖК"
        verbose_name_plural = "Склады ЖК"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.residential_complex.name})"


class BuildingProcurementRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SUBMITTED_TO_CASH = "submitted_to_cash", "Отправлено в кассу"
        CASH_APPROVED = "cash_approved", "Одобрено кассой"
        CASH_REJECTED = "cash_rejected", "Отклонено кассой"
        TRANSFER_CREATED = "transfer_created", "Передача создана"
        TRANSFERRED = "transferred", "Передано на склад"
        PARTIALLY_TRANSFERRED = "partially_transferred", "Передано частично"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    residential_complex = models.ForeignKey(
        ResidentialComplex,
        on_delete=models.CASCADE,
        related_name="procurements",
        verbose_name="Жилой комплекс",
    )
    initiator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_procurement_requests",
        verbose_name="Инициатор закупки",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Название закупки")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Статус",
    )
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    submitted_to_cash_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлено в кассу")
    cash_decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Решение кассы")
    cash_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_procurement_cash_decisions",
        verbose_name="Кто принял решение в кассе",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Заявка на закупку"
        verbose_name_plural = "Заявки на закупку"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["residential_complex", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return self.title or f"Закупка {self.id}"

    def recalculate_totals(self):
        total = self.items.aggregate(total=Coalesce(Sum("line_total"), Decimal("0.00"))).get("total") or Decimal("0.00")
        type(self).objects.filter(pk=self.pk).update(total_amount=total)
        self.total_amount = total
        return total


class BuildingProcurementItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    procurement = models.ForeignKey(
        BuildingProcurementRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Закупка",
    )
    name = models.CharField(max_length=255, verbose_name="Наименование")
    unit = models.CharField(max_length=64, verbose_name="Единица измерения")
    quantity = models.DecimalField(max_digits=16, decimal_places=3, verbose_name="Количество")
    price = models.DecimalField(max_digits=16, decimal_places=2, verbose_name="Цена")
    line_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    note = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Позиция закупки"
        verbose_name_plural = "Позиции закупки"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["procurement", "order"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"

    def save(self, *args, **kwargs):
        qty = Decimal(self.quantity or 0)
        price = Decimal(self.price or 0)
        self.line_total = (qty * price).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)
        if self.procurement_id:
            self.procurement.recalculate_totals()

    def delete(self, *args, **kwargs):
        procurement = self.procurement
        super().delete(*args, **kwargs)
        if procurement:
            procurement.recalculate_totals()


class BuildingProcurementCashDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    procurement = models.OneToOneField(
        BuildingProcurementRequest,
        on_delete=models.CASCADE,
        related_name="cash_decision",
        verbose_name="Закупка",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices, verbose_name="Решение")
    reason = models.TextField(blank=True, verbose_name="Причина")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_cash_decisions",
        verbose_name="Кто принял решение",
    )
    decided_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата решения")

    class Meta:
        verbose_name = "Решение кассы по закупке"
        verbose_name_plural = "Решения кассы по закупкам"
        indexes = [
            models.Index(fields=["decision", "decided_at"]),
        ]

    def __str__(self):
        return f"{self.procurement_id} - {self.decision}"


class BuildingTransferRequest(models.Model):
    class Status(models.TextChoices):
        PENDING_RECEIPT = "pending_receipt", "Ожидает приемку"
        ACCEPTED = "accepted", "Принято"
        REJECTED = "rejected", "Отклонено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    procurement = models.ForeignKey(
        BuildingProcurementRequest,
        on_delete=models.CASCADE,
        related_name="transfers",
        verbose_name="Закупка",
    )
    warehouse = models.ForeignKey(
        ResidentialComplexWarehouse,
        on_delete=models.CASCADE,
        related_name="transfers",
        verbose_name="Склад ЖК",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_created_transfers",
        verbose_name="Кто создал передачу",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_decided_transfers",
        verbose_name="Кто принял решение",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_RECEIPT,
        db_index=True,
        verbose_name="Статус",
    )
    note = models.TextField(blank=True, verbose_name="Комментарий")
    rejection_reason = models.TextField(blank=True, verbose_name="Причина отказа")
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата приемки")
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата отказа")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Передача на склад ЖК"
        verbose_name_plural = "Передачи на склад ЖК"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["warehouse", "status"]),
            models.Index(fields=["procurement", "status"]),
        ]

    def __str__(self):
        return f"Передача {self.id} ({self.get_status_display()})"

    def recalculate_totals(self):
        total = self.items.aggregate(total=Coalesce(Sum("line_total"), Decimal("0.00"))).get("total") or Decimal("0.00")
        type(self).objects.filter(pk=self.pk).update(total_amount=total)
        self.total_amount = total
        return total


class BuildingTransferItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    transfer = models.ForeignKey(
        BuildingTransferRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Передача",
    )
    procurement_item = models.ForeignKey(
        BuildingProcurementItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_items",
        verbose_name="Позиция закупки",
    )
    name = models.CharField(max_length=255, verbose_name="Наименование")
    unit = models.CharField(max_length=64, verbose_name="Единица измерения")
    quantity = models.DecimalField(max_digits=16, decimal_places=3, verbose_name="Количество")
    price = models.DecimalField(max_digits=16, decimal_places=2, verbose_name="Цена")
    line_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Позиция передачи"
        verbose_name_plural = "Позиции передачи"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["transfer", "order"]),
            models.Index(fields=["procurement_item"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"

    def save(self, *args, **kwargs):
        qty = Decimal(self.quantity or 0)
        price = Decimal(self.price or 0)
        self.line_total = (qty * price).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)
        if self.transfer_id:
            self.transfer.recalculate_totals()

    def delete(self, *args, **kwargs):
        transfer = self.transfer
        super().delete(*args, **kwargs)
        if transfer:
            transfer.recalculate_totals()


class BuildingWarehouseStockItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    warehouse = models.ForeignKey(
        ResidentialComplexWarehouse,
        on_delete=models.CASCADE,
        related_name="stock_items",
        verbose_name="Склад ЖК",
    )
    name = models.CharField(max_length=255, verbose_name="Наименование")
    unit = models.CharField(max_length=64, verbose_name="Единица измерения")
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=Decimal("0.000"), verbose_name="Остаток")
    last_price = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Последняя цена")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Остаток склада ЖК"
        verbose_name_plural = "Остатки склада ЖК"
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "name", "unit"],
                name="uq_building_warehouse_stock_item",
            )
        ]
        indexes = [
            models.Index(fields=["warehouse", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"


class BuildingWarehouseStockMove(models.Model):
    class MoveType(models.TextChoices):
        INCOMING = "incoming", "Приход"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    warehouse = models.ForeignKey(
        ResidentialComplexWarehouse,
        on_delete=models.CASCADE,
        related_name="stock_moves",
        verbose_name="Склад ЖК",
    )
    stock_item = models.ForeignKey(
        BuildingWarehouseStockItem,
        on_delete=models.CASCADE,
        related_name="moves",
        verbose_name="Остаток",
    )
    transfer = models.ForeignKey(
        BuildingTransferRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_moves",
        verbose_name="Передача",
    )
    move_type = models.CharField(max_length=16, choices=MoveType.choices, verbose_name="Тип движения")
    quantity_delta = models.DecimalField(max_digits=16, decimal_places=3, verbose_name="Изменение количества")
    price = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Цена")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_stock_moves",
        verbose_name="Кто создал",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Движение остатка склада ЖК"
        verbose_name_plural = "Движения остатков склада ЖК"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["warehouse", "created_at"]),
            models.Index(fields=["stock_item", "created_at"]),
        ]


class BuildingWorkflowEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    procurement = models.ForeignKey(
        BuildingProcurementRequest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Закупка",
    )
    procurement_item = models.ForeignKey(
        BuildingProcurementItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Позиция закупки",
    )
    transfer = models.ForeignKey(
        BuildingTransferRequest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Передача",
    )
    transfer_item = models.ForeignKey(
        BuildingTransferItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Позиция передачи",
    )
    warehouse = models.ForeignKey(
        ResidentialComplexWarehouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Склад ЖК",
    )
    stock_item = models.ForeignKey(
        BuildingWarehouseStockItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_events",
        verbose_name="Остаток склада",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_workflow_events",
        verbose_name="Кто выполнил действие",
    )
    action = models.CharField(max_length=64, verbose_name="Действие")
    from_status = models.CharField(max_length=64, blank=True, null=True, verbose_name="Статус до")
    to_status = models.CharField(max_length=64, blank=True, null=True, verbose_name="Статус после")
    message = models.TextField(blank=True, verbose_name="Комментарий")
    payload = models.JSONField(default=dict, blank=True, verbose_name="Данные события")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата события")

    class Meta:
        verbose_name = "Событие процесса закупки"
        verbose_name_plural = "События процесса закупки"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["procurement", "created_at"]),
            models.Index(fields=["transfer", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} ({self.created_at:%Y-%m-%d %H:%M:%S})"
