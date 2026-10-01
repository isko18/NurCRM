# apps/cafe/fiscal_models.py
"""
Интеграция с фискальным коннектором ГНС КР (налоговая).

Архитектура: фискальный коннектор (FiscalConnectorSetup.exe) слушает на
localhost:8080 на машине кассира. С ним общается ФРОНТ напрямую
(verify-pin / auth / open-shift / receipt / close-shift и т.д.).

Бэкенд НЕ ходит на localhost кассира. Его роль:
  * хранить реквизиты кассы (РНМ, логин, ПИН, СНО, ставки по умолчанию);
  * готовить тело фискального чека из заказа кафе (mapping позиций/сумм);
  * вести журнал фискальных смен и документов (для отчётности и связи с заказом).

Модуль полностью аддитивный — существующая логика кафе не затрагивается.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.users.models import Company, Branch


class CafeFiscalSettings(models.Model):
    """
    Фискальные настройки кассы — одна запись на компанию (по аналогии с
    CafeReceiptPrinterSettings). Хранит реквизиты для авторизации в коннекторе
    и значения по умолчанию для формирования чека.
    """
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="cafe_fiscal_settings",
        verbose_name="Компания",
    )
    enabled = models.BooleanField("Фискализация включена", default=False)
    connector_base_url = models.CharField(
        "URL фискального коннектора",
        max_length=512,
        blank=True,
        default="http://localhost:8080",
        help_text="Базовый URL локального коннектора (FiscalConnectorSetup.exe).",
    )

    # --- Реквизиты авторизации (используются фронтом при verify-pin / auth) ---
    registration_number = models.CharField(
        "РНМ кассы", max_length=16, blank=True, default="",
        help_text="Регистрационный номер ККМ (16 символов).",
    )
    pin = models.CharField(
        "ПИН SAM-карты", max_length=8, blank=True, default="",
        help_text="PIN код кассы (5 символов).",
    )
    login = models.CharField("Логин (почта)", max_length=255, blank=True, default="")
    password = models.CharField("Пароль", max_length=255, blank=True, default="")

    # --- Справочные данные (кэш из ответа /driver/auth) ---
    tin = models.CharField("ИНН НП", max_length=32, blank=True, default="")
    full_name = models.CharField("Полное имя НП", max_length=512, blank=True, default="")
    cashier_name = models.CharField("Кассир", max_length=255, blank=True, default="")
    fiscal_memory_number = models.CharField("Номер ФМ", max_length=64, blank=True, default="")
    location_address = models.CharField("Адрес", max_length=512, blank=True, default="")
    tax_system_codes = models.JSONField("СНО (коды)", default=list, blank=True)
    calc_item_attr_codes = models.JSONField("Коды признака предмета расчёта", default=list, blank=True)
    entrepreneurship_object_code = models.IntegerField(
        "Код объекта предпринимательства", null=True, blank=True
    )
    business_activity_code = models.IntegerField("Код вида деятельности", null=True, blank=True)
    tax_authority_department_code = models.IntegerField("Код УГНС", null=True, blank=True)

    # --- Значения по умолчанию для позиций чека ---
    default_vat_code = models.SmallIntegerField(
        "Код НДС по умолчанию", default=0,
        help_text="Код ставки НДS (например 0 для VAT_0, 12 для VAT_12 — по справочнику коннектора).",
    )
    default_st_code = models.SmallIntegerField(
        "Код НСП по умолчанию", default=0,
        help_text="Код ставки НСП (ST_0..ST_5).",
    )
    default_calc_item_attr_code = models.IntegerField(
        "Код признака предмета расчёта по умолчанию", default=1,
    )
    default_measure = models.CharField("Единица измерения по умолчанию", max_length=32, default="шт")
    receipt_width = models.IntegerField(
        "Ширина PDF чека (WIDTH-RECEIPT)", default=384,
    )

    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Фискальные настройки (кафе)"
        verbose_name_plural = "Фискальные настройки (кафе)"

    def __str__(self):
        return f"Фискальная касса: {self.company}"


class CafeFiscalShift(models.Model):
    """
    Журнал фискальной смены. Фактическое открытие/закрытие смены делает фронт
    через коннектор; бэкенд фиксирует факт и метаданные для отчётности и связи
    с заказами/документами.
    """
    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        CLOSED = "closed", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="cafe_fiscal_shifts", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name="cafe_fiscal_shifts", verbose_name="Филиал",
        null=True, blank=True, db_index=True,
    )
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True,
    )
    registration_number = models.CharField("РНМ кассы", max_length=16, blank=True, default="")

    opened_at = models.DateTimeField("Открыта в (сервер)", null=True, blank=True)
    closed_at = models.DateTimeField("Закрыта в (сервер)", null=True, blank=True)
    open_shift_datetime = models.DateTimeField("Открытие смены (коннектор)", null=True, blank=True)
    fm_expiration_date = models.DateTimeField("Истечение ФМ", null=True, blank=True)

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_fiscal_shifts_opened",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_fiscal_shifts_closed",
    )

    raw_open = models.JSONField("Ответ открытия (коннектор)", default=dict, blank=True)
    raw_close = models.JSONField("Ответ закрытия (коннектор)", default=dict, blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Фискальная смена (кафе)"
        verbose_name_plural = "Фискальные смены (кафе)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
            models.Index(fields=["company", "created_at"]),
        ]

    def __str__(self):
        return f"Смена {str(self.id)[:8]} — {self.get_status_display()}"


class CafeFiscalReceipt(models.Model):
    """
    Фискальный документ, записанный ПОСЛЕ того как фронт успешно фискализировал
    операцию через коннектор. Хранит результат (ФД/ФМ), суммы и снимок payload.
    """
    class Kind(models.TextChoices):
        SALE = "sale", "Чек продажи"
        RETURN = "return", "Чек возврата"
        DEPOSIT = "deposit", "Внесение наличных"
        WITHDRAW = "withdraw", "Изъятие наличных"
        OPEN_SHIFT = "open_shift", "Открытие смены"
        CLOSE_SHIFT = "close_shift", "Закрытие смены"
        X_REPORT = "x_report", "X-отчёт"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="cafe_fiscal_receipts", verbose_name="Компания",
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE,
        related_name="cafe_fiscal_receipts", verbose_name="Филиал",
        null=True, blank=True, db_index=True,
    )
    order = models.ForeignKey(
        "cafe.Order", on_delete=models.SET_NULL,
        related_name="fiscal_receipts", verbose_name="Заказ",
        null=True, blank=True,
    )
    shift = models.ForeignKey(
        CafeFiscalShift, on_delete=models.SET_NULL,
        related_name="receipts", verbose_name="Смена",
        null=True, blank=True,
    )

    kind = models.CharField("Тип документа", max_length=16, choices=Kind.choices, db_index=True)
    operation_type = models.CharField(
        "Тип операции", max_length=24, blank=True, default="",
        help_text="INCOME, INCOME_RETURN, EXPENDITURE, EXPENDITURE_RETURN.",
    )

    # Результат фискализации (из ответа коннектора)
    fd_number = models.BigIntegerField("Номер ФД", null=True, blank=True, db_index=True)
    fn_serial_number = models.CharField("Номер ФМ чека", max_length=64, blank=True, default="")

    total_sum = models.DecimalField("Общая сумма", max_digits=14, decimal_places=2, default=Decimal("0"))
    total_cash_sum = models.DecimalField("Наличные", max_digits=14, decimal_places=2, default=Decimal("0"))
    total_cashless_sum = models.DecimalField("Безналичные", max_digits=14, decimal_places=2, default=Decimal("0"))
    pay_sum = models.DecimalField("Внесено", max_digits=14, decimal_places=2, default=Decimal("0"))
    delivery_sum = models.DecimalField("Сдача", max_digits=14, decimal_places=2, default=Decimal("0"))

    request_payload = models.JSONField("Отправленный payload", default=dict, blank=True)
    response_payload = models.JSONField("Ответ коннектора", default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="cafe_fiscal_receipts",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Фискальный документ (кафе)"
        verbose_name_plural = "Фискальные документы (кафе)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "kind", "created_at"]),
            models.Index(fields=["company", "branch", "created_at"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} ФД={self.fd_number or '—'}"
