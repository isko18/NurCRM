from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import Company, Branch

from .utils import ensure_system_payment_categories


@receiver(post_save, sender=Company)
def create_system_payment_categories_for_company(sender, instance: Company, created, **kwargs):
    if not created:
        return
    try:
        ensure_system_payment_categories(instance, branch=None)
    except Exception:
        pass


@receiver(post_save, sender=Branch)
def create_system_payment_categories_for_branch(sender, instance: Branch, created, **kwargs):
    if not created:
        return
    try:
        ensure_system_payment_categories(instance.company, branch=instance)
    except Exception:
        pass


# --- Сброс кэша аналитики при изменении данных, из которых она считается ---
from django.db import transaction  # noqa: E402
from django.db.models.signals import post_delete  # noqa: E402

from . import models as wm  # noqa: E402
from .analytics_cache import bump_analytics_version  # noqa: E402


def _bump_after_commit(company_id):
    if company_id:
        transaction.on_commit(lambda: bump_analytics_version(company_id))


def _document_company_ids(instance):
    """Компании документа: склады from/to, а для мультискладского без складов — компании товаров строк."""
    wh_ids = [i for i in (instance.warehouse_from_id, instance.warehouse_to_id) if i]
    ids = set()
    if wh_ids:
        ids.update(wm.Warehouse.objects.filter(id__in=wh_ids).values_list("company_id", flat=True))
    if not ids and instance.pk:
        try:
            ids.update(
                wm.DocumentItem.objects.filter(document_id=instance.pk)
                .exclude(product__company_id__isnull=True)
                .values_list("product__company_id", flat=True)
                .distinct()
            )
        except Exception:
            pass
    return ids


@receiver(post_save, sender=wm.Document)
@receiver(post_delete, sender=wm.Document)
def _bump_on_document(sender, instance, **kwargs):
    # post_delete: строки уже удалены каскадом — тогда сработает только ветка по складам.
    for company_id in _document_company_ids(instance):
        _bump_after_commit(company_id)


@receiver(post_save, sender=wm.MoneyDocument)
@receiver(post_delete, sender=wm.MoneyDocument)
@receiver(post_save, sender=wm.AgentRequestCart)
@receiver(post_save, sender=wm.AgentSalaryAccrual)
@receiver(post_save, sender=wm.AgentSalaryPayout)
@receiver(post_delete, sender=wm.AgentSalaryPayout)
@receiver(post_save, sender=wm.AgentStockBalance)
def _bump_on_company_object(sender, instance, **kwargs):
    _bump_after_commit(getattr(instance, "company_id", None))
