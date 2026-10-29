from .models import CompanyIGAccount
from .service import IGChatService


def autologin_company(company_id: str) -> dict:
    """
    Пройтись по всем активным IG-аккаунтам компании и попытаться резюмировать сессию.
    Возвращает статусы по каждому аккаунту.
    """
    results = []
    qs = CompanyIGAccount.objects.filter(company_id=company_id, is_active=True)
    for acc in qs:
        svc = IGChatService(acc)
        ok = svc.try_resume_session()
        results.append({"id": str(acc.id), "username": acc.username, "resumed": ok})
    return {"count": len(results), "accounts": results}