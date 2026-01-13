

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models


class DocumentWriteOff(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    
    class Meta:
        verbose_name = "Списание"
        verbose_name_plural = "Списание"
