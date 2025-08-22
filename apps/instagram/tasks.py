from celery import shared_task
from django.shortcuts import get_object_or_404
from .models import CompanyIGAccount
from .service import IGChatService

@shared_task
def sync_ig_account_threads(account_id: str, amount: int = 20, per_thread_messages: int = 30):
    acc = get_object_or_404(CompanyIGAccount, pk=account_id)
    if not acc.is_active:
        return "inactive"
    svc = IGChatService(acc)
    if not svc.try_resume_session():
        return "login_required"
    return svc.sync_threads(amount=amount, per_thread_messages=per_thread_messages)
