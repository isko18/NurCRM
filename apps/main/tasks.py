from django.db import transaction
from celery import shared_task
from apps.main.models import Task, Notification
from django.utils.timezone import localtime

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


# Пример использования транзакции
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Task)
def notify_assigned_user_async(sender, instance, created, **kwargs):
    if created and instance.assigned_to:
        # Убедитесь, что задача сохранена, прежде чем вызывать celery задачу
        transaction.on_commit(lambda: create_task_notification.delay(str(instance.id)))

# apps/main/tasks.py
from time import sleep

@shared_task
def test_celery_api(name: str):
    print("=== START TEST TASK FROM API ===", name)
    sleep(5)  # имитация тяжёлой задачи
    print("=== END TEST TASK FROM API ===", name)
    return f"Hello from Celery, {name}"
