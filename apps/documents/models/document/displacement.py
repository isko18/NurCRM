
from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models


class DocumentDisplacement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    from_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_from"
    )

    to_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_to"
    )

    class Meta:
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"

