from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import OneCIntegration


@receiver(post_save, sender=OneCIntegration)
def invalidate_onec_token_cache(sender, instance, **kwargs):
    """При смене настроек интеграции сбрасываем кеш Bearer-токена компании."""
    cache.delete(f"onec:bearer:{instance.company_id}")
