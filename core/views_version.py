from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class ClientVersionView(APIView):
    """
    GET /api/version/ — публичный эндпоинт автообновления десктопного клиента.

    exe обращается сюда ДО логина, поэтому аутентификация отключена полностью
    (иначе протухший/кривой JWT в заголовке отбил бы запрос ещё до view).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(
            {
                "version": settings.CLIENT_VERSION,
                "zip_url": settings.CLIENT_ZIP_URL,
                "release_notes": settings.CLIENT_RELEASE_NOTES,
            }
        )
