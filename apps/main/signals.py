# apps/main/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.main.models import Task
from apps.main.tasks import create_task_notification

@receiver(post_save, sender=Task)
def notify_assigned_user_async(sender, instance, created, **kwargs):
    if created and instance.assigned_to:
        # Мы вызываем задачу только после того, как задача была сохранена
        create_task_notification.delay(str(instance.id))
