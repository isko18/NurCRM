

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    DocumentAbstractModel
)

from django.db import models


class DocumentReceipt(
    UUIDPrimaryKeyMixin,
    DocumentAbstractModel,
):
    
    account = models.CharField(max_length=128)
    
    
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.CASCADE,
        db_index=True,
        related_name="money_documents"
    )

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        db_index=True,
        related_name="money_documents"
    )


    payment_category = models.ForeignKey(
        "documents.PaymentCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="money_documents"
    )

    total_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        verbose_name="Сумма"
    )
    
    comment = models.CharField(max_length=255, blank=True)

        
    class Meta:
        verbose_name = "Приходы"
        verbose_name_plural = "Приход"

        indexes = [
            models.Index(fields=["client", "user"]),
            models.Index(fields=["client", "payment_category"]),
            models.Index(fields=["user", "payment_category"]),
        ]

