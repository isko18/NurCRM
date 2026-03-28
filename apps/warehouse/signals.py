from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import Company, Branch

from .utils import ensure_system_payment_categories


@receiver(post_save, sender=Company)
def create_system_payment_categories_for_company(sender, instance: Company, created, **kwargs):
    if not created:
        return
    ensure_system_payment_categories(instance, branch=None)


@receiver(post_save, sender=Branch)
def create_system_payment_categories_for_branch(sender, instance: Branch, created, **kwargs):
    if not created:
        return
    ensure_system_payment_categories(instance.company, branch=instance)
