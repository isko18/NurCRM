# apps/integrations/models.py
import uuid
from django.db import models

class WhatsAppSession(models.Model):
    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Отключено"
        PENDING_QR   = "pending_qr",  "Ожидает QR"
        CONNECTED    = "connected",   "Подключено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField("users.Company", on_delete=models.CASCADE, related_name="wa_session")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DISCONNECTED)
    last_qr_data_url = models.TextField(blank=True, null=True)
    phone_hint = models.CharField(max_length=64, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return f"{self.company_id}: {self.status}"
