# apps/main/tasks.py

from celery import shared_task
from django.utils.timezone import localtime
from apps.main.models import Notification, Task

@shared_task
def create_task_notification(task_id):
    try:
        task = Task.objects.select_related('company', 'assigned_to').get(id=task_id)
        
        if not task.assigned_to:
            return
        
        message = (
            f"Вам назначена новая задача: «{task.title}», "
            f"срок — {localtime(task.due_date).strftime('%d.%m.%Y %H:%M')}"
        )

        Notification.objects.create(
            company=task.company,
            user=task.assigned_to,
            message=message
        )

    except Task.DoesNotExist:
        print(f"[ERROR] Task with ID {task_id} does not exist!")

    except Exception as e:
        print(f"[ERROR] Unexpected error while creating task notification: {e}")
