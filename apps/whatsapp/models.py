from django.db import models
import uuid
from apps.users.models import Company


class WhatsAppSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_session",
        verbose_name="Компания"
    )
    session_name = models.CharField(max_length=255, unique=True)
    is_ready = models.BooleanField(default=False)
    last_qr = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WhatsApp-сессия"
        verbose_name_plural = "WhatsApp-сессии"

    def __str__(self):
        return f"WA Session {self.company.name}"


class Message(models.Model):
    DIRECTION_CHOICES = (
        ("in", "Inbound"),
        ("out", "Outbound"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="messages")
    phone = models.CharField(max_length=32)
    text = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, default="text")  # text, image, video, document, audio
    caption = models.TextField(blank=True, null=True)
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"

    def __str__(self):
        return f"[{self.direction}] {self.phone}: {self.text or self.type}"
