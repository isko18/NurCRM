

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    DocumentAbstractModel
)

from django.db import models


class DocumentExpense(
    UUIDPrimaryKeyMixin,
    DocumentAbstractModel,
):
    
    from_account = models.CharField(max_length=128)
    
    to_account = models.CharField(max_length=128)
    
    
    total_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        verbose_name="Сумма"
    )
    
    comment = models.CharField(max_length=255, blank=True)

        
    class Meta:
        verbose_name = "Переводы"
        verbose_name_plural = "Перевод"

        indexes = [
            models.Index(fields=["client", "user"]),
            models.Index(fields=["client", "payment_category"]),
            models.Index(fields=["user", "payment_category"]),
        ]

