# apps/main/tasks.py

from celery import shared_task
from django.utils.timezone import localtime
from apps.main.models import Notification, Task

@shared_task
def create_task_notification(task_id):
    try:
        task = Task.objects.get(id=task_id)
        if task.assigned_to:
            message = f"Вам назначена новая задача: «{task.title}», срок — {localtime(task.due_date).strftime('%d.%m.%Y %H:%M')}"
            Notification.objects.create(user=task.assigned_to, message=message)
    except Task.DoesNotExist:
        pass
