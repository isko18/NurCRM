from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin,
    DateTimeMixin,
    CompanyBranchMixin
)

from django.db import models

class Document(UUIDPrimaryKeyMixin,DateTimeMixin,CompanyBranchMixin):
   

    class DocumentTypes(models.TextChoices):
        CAPITALIZATION = "capitalization", "Оприходывание"
        DISPLACEMENT = "displacement", "Перемещение"
        INVENTORY = "inventory", "Инвентаризация"
        PROCUREMENT = "procurement", "Закупка"
        RETURN_PROCUREMENT = "return_procurement", "Возврат поставщику"
        RETURN_SALE = "return_sale", "Возврат от клиента"
        SALE = "sale", "Продажа"
        WRITE_OFF = "write_off", "Списание"
    
    type = models.CharField(max_length=50,
        choices=DocumentTypes.choices,
        null=False,blank=False
    )
    
    carried_out = models.BooleanField(
        verbose_name="Документ проведен",default=True
    )

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["company","branch"]),
        ]



