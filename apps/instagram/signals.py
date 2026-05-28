import logging
import threading

from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in

from .autologin import autologin_company

logger = logging.getLogger(__name__)


def _warmup_ig_sessions_async(company_id: str) -> None:
    try:
        autologin_company(company_id)
    except Exception:
        logger.exception("IG session warmup failed for company %s", company_id)


@receiver(user_logged_in)
def _warmup_ig_sessions_on_login(sender, request, user, **kwargs):
    company_id = getattr(user, "company_id", None)
    if not company_id:
        return
    threading.Thread(
        target=_warmup_ig_sessions_async,
        args=(str(company_id),),
        daemon=True,
        name=f"ig-warmup-{company_id}",
    ).start()