

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models


class DocumentProcurement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    supplier = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    
    class Meta:
        verbose_name = "Закупки"
        verbose_name_plural = "Закупка"
