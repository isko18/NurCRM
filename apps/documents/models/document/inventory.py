
from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel,
    ShopAbstractModel
)

from django.db import models


class DocumentInventory(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
    ShopAbstractModel
):

    class Meta:
        verbose_name = "Инвентаризации"
        verbose_name_plural = "Инвентаризация"

