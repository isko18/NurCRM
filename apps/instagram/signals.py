from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in
from django.conf import settings


from .autologin import autologin_company


@receiver(user_logged_in)
def _warmup_ig_sessions_on_login(sender, request, user, **kwargs):
    company_id = getattr(user, "company_id", None)
    if not company_id:
        return
    try:
# синхронно (быстро) — чтобы не требовать Celery
        autologin_company(str(company_id))
    except Exception:
        pass