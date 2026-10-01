from django.db import models

from apps.documents.models.mixins import UUIDPrimaryKeyMixin
from apps.documents.models.abstract import CompanyBranchAbstractModel


class PaymentCategory(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel
):
    title = models.CharField(max_length=255, verbose_name="Название")

    def __str__(self):
        return self.title




