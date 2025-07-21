from django.db import models

class Client(models.Model):
    name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.phone


class Message(models.Model):
    DIRECTION_CHOICES = [
        ('in', 'Incoming'),
        ('out', 'Outgoing'),
    ]

    MESSAGE_TYPE_CHOICES = [
        ('text', 'Text'),
        ('file', 'File'),
        ('template', 'Template'),
        ('button_reply', 'Button Reply'),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='messages')
    text = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default='text')
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    external_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    channel_id = models.CharField(max_length=100, blank=True, null=True)
    file_url = models.URLField(blank=True, null=True)
    template_name = models.CharField(max_length=255, blank=True, null=True)
    button_payload = models.CharField(max_length=255, blank=True, null=True)
    button_text = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        preview = self.text if self.text else self.file_url or "..."
        return f"[{self.direction}] {self.client.phone}: {preview[:30]}"
