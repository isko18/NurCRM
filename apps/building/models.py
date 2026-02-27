import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

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


class BuildingProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="building_products",
        verbose_name="Компания",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    article = models.CharField(max_length=64, blank=True, verbose_name="Артикул")
    barcode = models.CharField(max_length=64, blank=True, verbose_name="Штрихкод")
    unit = models.CharField(max_length=64, default="шт.", verbose_name="Единица измерения")
    description = models.TextField(blank=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Товар (Building)"
        verbose_name_plural = "Товары (Building)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "barcode"]),
            models.Index(fields=["company", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("company", "barcode"),
                condition=~models.Q(barcode=""),
                name="uq_building_product_company_barcode_not_empty",
            )
        ]

    def __str__(self):
        return self.name


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
    product = models.ForeignKey(
        BuildingProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="procurement_items",
        verbose_name="Товар",
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
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit})"

    def save(self, *args, **kwargs):
        if self.product_id:
            if not self.name:
                self.name = self.product.name
            if not self.unit:
                self.unit = self.product.unit
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


class BuildingClient(models.Model):
    """
    Клиент (в рамках building).

    Не зависит от CRM, чтобы модуль building был самодостаточным.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="building_clients",
        verbose_name="Компания",
    )
    name = models.CharField(max_length=255, verbose_name="Клиент (ФИО/компания)")
    phone = models.CharField(max_length=32, blank=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, verbose_name="Email")
    inn = models.CharField(max_length=32, blank=True, verbose_name="ИНН")
    address = models.CharField(max_length=512, blank=True, verbose_name="Адрес")
    notes = models.TextField(blank=True, verbose_name="Заметки")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Клиент (Building)"
        verbose_name_plural = "Клиенты (Building)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "phone"]),
        ]

    def __str__(self):
        return self.name


def building_treaty_file_upload_to(instance, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"building/treaties/{instance.treaty_id}/files/{uuid.uuid4().hex}.{ext}"


class BuildingTreaty(models.Model):
    """
    Договор (в рамках building).
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активен"
        SIGNED = "signed", "Подписан"
        CANCELLED = "cancelled", "Отменён"

    class ErpSyncStatus(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Не отправляли"
        REQUESTED = "requested", "Запрошено"
        SYNCED = "synced", "Создано в ERP"
        FAILED = "failed", "Ошибка"
        NOT_CONFIGURED = "not_configured", "ERP не настроена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    residential_complex = models.ForeignKey(
        ResidentialComplex,
        on_delete=models.CASCADE,
        related_name="treaties",
        verbose_name="Жилой комплекс",
    )
    client = models.ForeignKey(
        BuildingClient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treaties",
        verbose_name="Клиент",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_treaties_created",
        verbose_name="Кто создал",
    )

    number = models.CharField(max_length=64, blank=True, verbose_name="Номер договора")
    title = models.CharField(max_length=255, blank=True, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание/условия")
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), verbose_name="Сумма")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="Статус")
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата подписания")

    auto_create_in_erp = models.BooleanField(default=False, verbose_name="Автосоздание в ERP")
    erp_sync_status = models.CharField(
        max_length=32,
        choices=ErpSyncStatus.choices,
        default=ErpSyncStatus.NOT_REQUESTED,
        db_index=True,
        verbose_name="ERP: статус",
    )
    erp_external_id = models.CharField(max_length=128, blank=True, verbose_name="ERP: внешний ID")
    erp_last_error = models.TextField(blank=True, verbose_name="ERP: последняя ошибка")
    erp_requested_at = models.DateTimeField(null=True, blank=True, verbose_name="ERP: запрос отправки")
    erp_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="ERP: синхронизировано")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Договор (Building)"
        verbose_name_plural = "Договоры (Building)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["residential_complex", "status"]),
            models.Index(fields=["erp_sync_status", "created_at"]),
        ]

    def __str__(self):
        base = self.number or (self.title or "Договор")
        return f"{base} ({self.residential_complex.name})"


class BuildingTreatyFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    treaty = models.ForeignKey(
        BuildingTreaty,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="Договор",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Название файла")
    file = models.FileField(upload_to=building_treaty_file_upload_to, verbose_name="Файл")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_treaty_files_created",
        verbose_name="Кто загрузил",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    class Meta:
        verbose_name = "Файл договора (Building)"
        verbose_name_plural = "Файлы договоров (Building)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["treaty", "created_at"]),
        ]

    def __str__(self):
        return self.title or str(getattr(self.file, "name", "")) or str(self.id)


def building_work_entry_photo_upload_to(instance, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"building/work-entries/{instance.entry_id}/photos/{uuid.uuid4().hex}.{ext}"


class BuildingWorkEntry(models.Model):
    """
    Запись в "процессе работ" по ЖК.
    Мастера/прорабы/технадзор пишут свои работы, владелец видит весь процесс.
    """

    class Category(models.TextChoices):
        NOTE = "note", "Заметка"
        TREATY = "treaty", "Договор"
        DEFECT = "defect", "Недостатки/дефекты"
        REPORT = "report", "Отчёт"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    residential_complex = models.ForeignKey(
        ResidentialComplex,
        on_delete=models.CASCADE,
        related_name="work_entries",
        verbose_name="Жилой комплекс",
    )
    client = models.ForeignKey(
        BuildingClient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_entries",
        verbose_name="Клиент",
    )
    treaty = models.ForeignKey(
        BuildingTreaty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_entries",
        verbose_name="Договор",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_work_entries_created",
        verbose_name="Кто добавил",
    )
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.NOTE,
        db_index=True,
        verbose_name="Категория",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Заголовок")
    description = models.TextField(blank=True, verbose_name="Описание")
    occurred_at = models.DateTimeField(default=timezone.now, verbose_name="Дата/время события")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Процесс работ: запись"
        verbose_name_plural = "Процесс работ: записи"
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["residential_complex", "occurred_at"]),
            models.Index(fields=["residential_complex", "category", "occurred_at"]),
            models.Index(fields=["created_by", "occurred_at"]),
        ]

    def __str__(self):
        return self.title or f"{self.get_category_display()} ({self.residential_complex.name})"


class BuildingWorkEntryPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    entry = models.ForeignKey(
        BuildingWorkEntry,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Запись процесса работ",
    )
    image = models.ImageField(upload_to=building_work_entry_photo_upload_to, verbose_name="Фото")
    caption = models.CharField(max_length=255, blank=True, verbose_name="Подпись")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="building_work_entry_photos_created",
        verbose_name="Кто загрузил",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    class Meta:
        verbose_name = "Фото процесса работ"
        verbose_name_plural = "Фото процесса работ"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entry", "created_at"]),
        ]

    def __str__(self):
        return self.caption or str(getattr(self.image, "name", "")) or str(self.id)
