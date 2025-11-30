from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import Company, Branch
from apps.construction.models import Cashbox


@receiver(post_save, sender=Company)
def create_cashbox_for_company(sender, instance: Company, created, **kwargs):
    """
    При создании компании создаём для неё отдельную кассу (глобальную, без филиала).
    """
    if not created:
        return

    # если касса компании уже есть — ничего не делаем
    if Cashbox.objects.filter(company=instance, branch__isnull=True).exists():
        return

    Cashbox.objects.create(
        company=instance,
        branch=None,
        name="Основная касса компании",
    )


@receiver(post_save, sender=Branch)
def create_cashbox_for_branch(sender, instance: Branch, created, **kwargs):
    """
    При создании филиала создаём для него отдельную кассу.
    """
    if not created:
        return

    # если у филиала уже есть касса — ничего не делаем
    if Cashbox.objects.filter(company=instance.company, branch=instance).exists():
        return

    Cashbox.objects.create(
        company=instance.company,
        branch=instance,
        name=f"Касса филиала {instance.name}",
    )
