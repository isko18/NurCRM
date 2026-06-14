from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.releases.models import ClientRelease
from apps.releases.serializers import (
    ClientReleaseSerializer,
    ClientReleaseUploadSerializer,
)


class ClientVersionView(APIView):
    """
    GET  /api/version/ — публичный эндпоинт автообновления десктопного клиента.
        exe обращается сюда ДО логина, поэтому аутентификация для GET отключена
        полностью (иначе протухший/кривой JWT в заголовке отбил бы запрос ещё до view).

    POST /api/version/ — загрузка новой версии .exe-клиента (ZIP), только для
        админов (is_staff/superuser). Предыдущая версия заменяется: её файл и
        запись удаляются, чтобы не копился мусор.

    Контракт GET остаётся прежним: {version, zip_url, release_notes}.
    """

    def initialize_request(self, request, *args, **kwargs):
        # HTTP-метод нужен в get_authenticators()/get_parsers(), которые вызываются
        # раньше, чем становится доступен self.request.
        self._http_method = (request.method or "GET").upper()
        return super().initialize_request(request, *args, **kwargs)

    def get_authenticators(self):
        if getattr(self, "_http_method", "GET") == "POST":
            return [JWTAuthentication()]
        return []

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsAdminUser()]
        return [AllowAny()]

    def get_parsers(self):
        if getattr(self, "_http_method", "GET") == "POST":
            return [MultiPartParser(), FormParser()]
        return super().get_parsers()

    def get(self, request):
        latest = ClientRelease.objects.first()  # ordering = -created_at
        if latest and latest.zip_file:
            data = ClientReleaseSerializer(latest, context={"request": request}).data
            return Response(data)

        # Фолбэк на settings, пока не загружен первый релиз в БД — чтобы уже
        # работающие exe-клиенты продолжали получать прежний ответ.
        return Response(
            {
                "version": settings.CLIENT_VERSION,
                "zip_url": settings.CLIENT_ZIP_URL,
                "release_notes": settings.CLIENT_RELEASE_NOTES,
            }
        )

    def post(self, request):
        serializer = ClientReleaseUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Заменяем предыдущие версии: удаляем файлы (storage-aware) и записи.
        for old in ClientRelease.objects.all():
            if old.zip_file:
                old.zip_file.delete(save=False)
            old.delete()

        instance = serializer.save()
        out = ClientReleaseSerializer(instance, context={"request": request})
        return Response(out.data, status=201)
