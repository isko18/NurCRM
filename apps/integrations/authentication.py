from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import SAFE_METHODS

from .models import ApiKey, hash_api_key

KEYWORD = "api-key"


class ApiKeyAuthentication(BaseAuthentication):
    """
    Authorization: Api-Key nk_live_…

    Запрос выполняется от имени владельца компании ключа. Ключ со scope "read"
    пропускает только чтение (GET/HEAD/OPTIONS).
    """

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != KEYWORD.encode():
            return None
        if len(auth) != 2:
            raise exceptions.AuthenticationFailed("Неверный заголовок Api-Key.")
        try:
            raw = auth[1].decode("utf-8")
        except UnicodeError:
            raise exceptions.AuthenticationFailed("Неверный заголовок Api-Key.")

        key = (
            ApiKey.objects.select_related("company", "company__owner")
            .filter(key_hash=hash_api_key(raw), revoked_at__isnull=True)
            .first()
        )
        if key is None:
            raise exceptions.AuthenticationFailed("Неверный или отозванный API-ключ.")

        company = key.company
        owner = getattr(company, "owner", None)
        if owner is None or not owner.is_active or not getattr(company, "is_active", True):
            raise exceptions.AuthenticationFailed("Компания ключа недоступна.")

        if request.method not in SAFE_METHODS and not key.has_scope(ApiKey.Scope.WRITE):
            raise exceptions.PermissionDenied("Этот API-ключ только для чтения.")

        now = timezone.now()
        if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)

        return (owner, key)

    def authenticate_header(self, request):
        return "Api-Key"
