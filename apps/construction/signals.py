from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Department, Cashbox

@receiver(post_save, sender=Department)
def create_cashbox_for_department(sender, instance, created, **kwargs):
    if created and not hasattr(instance, 'cashbox'):
        Cashbox.objects.create(department=instance)
