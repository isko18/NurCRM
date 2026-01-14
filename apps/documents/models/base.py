from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin,
    DateTimeMixin
)

from .abstract import CompanyBranchAbstractModel
from django.db import models

class Document(
    UUIDPrimaryKeyMixin,
    DateTimeMixin,
    CompanyBranchAbstractModel
):
   

    class DocumentTypes(models.TextChoices):
        CAPITALIZATION = "capitalization", "Оприходывание"
        DISPLACEMENT = "displacement", "Перемещение"
        INVENTORY = "inventory", "Инвентаризация"
        PROCUREMENT = "procurement", "Закупка"
        RETURN_PROCUREMENT = "return_procurement", "Возврат поставщику"
        RETURN_SALE = "return_sale", "Возврат от клиента"
        SALE = "sale", "Продажа"
        WRITE_OFF = "write_off", "Списание"
        EXPENSE = "expense","Расход"
        RECEIPT = "receipt","Приход"
    
    class StatusTypes(models.TextCHoices):
        NEW = "new","новый"
        INWORK = "inwork","В работе"
        CLOSED = "closed","закрыт"
        CANCELLED = "cancelled","отменен"  




    document_type = models.CharField(max_length=50,
        choices=DocumentTypes.choices,
        null=False,blank=False,
        verbose_name="Тип документа"
    )
    
    carried_out = models.BooleanField(
        verbose_name="Документ проведен",default=True
    )
    
    document_status = models.CharField(max_length=20,
        choices=StatusTypes.choices,
        null=True,blank=True,default=None,
        verbose_name="Статус"
    )

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["company","branch"]),
        ]



