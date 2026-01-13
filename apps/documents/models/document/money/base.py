from django.db import models

from apps.documents.model.mixins import UUIDPrimaryKeyMixin


class PaymentCategory(UUIDPrimaryKeyMixin):
    title = models.CharField(max_length=255, verbose_name="Название")

    def __str__(self):
        return self.title




