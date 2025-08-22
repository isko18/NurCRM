import uuid
from django.db import models
from apps.users.models import Company


class InstagramSession(models.Model):
    """
    Сессия Instagram для конкретной компании
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="instagram_sessions")
    username = models.CharField(max_length=150, blank=True, null=True)
    is_ready = models.BooleanField(default=False)   # подключено или нет
    last_login = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"IGSession({self.company.name} - {self.username})"


class InstagramMessage(models.Model):
    """
    Сообщения в DM
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="instagram_messages")
    user_id = models.CharField(max_length=50)   # id собеседника в IG
    text = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, default="text")  # text, image, video, voice, document
    direction = models.CharField(max_length=5, choices=(("in", "Входящее"), ("out", "Исходящее")))
    ts = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"IGMessage({self.user_id} - {self.text[:20]})"
