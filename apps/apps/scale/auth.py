from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework import exceptions

from apps.main.models import Company


class ScaleAgentAuthentication(BaseAuthentication):
    """
    Авторизация по постоянному токену компании в заголовке:

        Authorization: Bearer <scale_api_token>
    """

    def authenticate(self, request):
        auth = get_authorization_header(request).decode("utf-8").strip()

        if not auth.startswith("Bearer "):
            return None

        token = auth.split(" ", 1)[1].strip()
        if not token:
            return None

        try:
            company = Company.objects.get(scale_api_token=token)
        except Company.DoesNotExist:
            raise exceptions.AuthenticationFailed("Неверный токен агента")

        # Можно вернуть псевдо-пользователя, но нам важна компания
        user = type("ScaleAgentUser", (), {"is_authenticated": True, "company": company})()
        return (user, None)
