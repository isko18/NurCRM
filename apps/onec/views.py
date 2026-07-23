from __future__ import annotations

from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import OneCIntegration, OneCSyncRecord
from .serializers import OneCIntegrationSerializer, OneCSyncRecordSerializer
from .services import _dispatch


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) in ("owner", "admin"):
        return True
    if getattr(user, "owned_company_id", None):
        return True
    return False


def _user_company_id(user):
    return getattr(user, "company_id", None) or getattr(getattr(user, "owned_company", None), "id", None)


class OneCIntegrationView(generics.RetrieveUpdateAPIView):
    """Настройки интеграции с 1С для компании текущего пользователя (owner-like)."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OneCIntegrationSerializer

    def get_object(self):
        if not _is_owner_like(self.request.user):
            raise PermissionDenied("Только владелец/админ компании может управлять интеграцией с 1С.")
        company_id = _user_company_id(self.request.user)
        if not company_id:
            raise PermissionDenied("У пользователя не определена компания.")
        obj, _ = OneCIntegration.objects.get_or_create(company_id=company_id)
        return obj


class OneCSyncRecordListView(generics.ListAPIView):
    """Журнал синхронизации (мониторинг). Фильтры: status, source_type."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OneCSyncRecordSerializer

    def get_queryset(self):
        user = self.request.user
        qs = OneCSyncRecord.objects.all()
        if not getattr(user, "is_superuser", False):
            company_id = _user_company_id(user)
            qs = qs.filter(company_id=company_id) if company_id else qs.none()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        source_type = self.request.query_params.get("source_type")
        if source_type:
            qs = qs.filter(source_type=source_type)
        return qs.order_by("-created_at")


class OneCSyncRecordRetryView(generics.GenericAPIView):
    """Ручной повтор выгрузки записи (owner-like)."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OneCSyncRecordSerializer

    def post(self, request, *args, **kwargs):
        if not _is_owner_like(request.user):
            raise PermissionDenied("Только владелец/админ компании может повторять выгрузку.")
        qs = OneCSyncRecord.objects.all()
        if not getattr(request.user, "is_superuser", False):
            qs = qs.filter(company_id=_user_company_id(request.user))
        rec = qs.filter(pk=kwargs.get("pk")).first()
        if not rec:
            return Response({"detail": "Запись не найдена."}, status=status.HTTP_404_NOT_FOUND)
        if rec.status in (OneCSyncRecord.Status.SENT, OneCSyncRecord.Status.POSTED):
            return Response({"detail": "Уже отправлено.", "status": rec.status}, status=status.HTTP_200_OK)
        rec.status = OneCSyncRecord.Status.PENDING
        rec.last_error = ""
        rec.save(update_fields=["status", "last_error", "updated_at"])
        _dispatch(rec.id)
        return Response(self.get_serializer(rec).data, status=status.HTTP_202_ACCEPTED)
