

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models


class DocumentSale(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    sale = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    

    class Meta:
        verbose_name = "Продажы"
        verbose_name_plural = "Продажа"
