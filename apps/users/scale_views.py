# apps/scales/views.py
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, filters
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from apps.users.models import Company, ScaleDevice
from apps.main.models import Product
from apps.main.serializers import ProductSerializer
from apps.main.views import CompanyBranchRestrictedMixin
from apps.utils import product_images_prefetch
from rest_framework.permissions import IsAuthenticated, AllowAny
from apps.scale.auth import ScaleAgentAuthentication 

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_scale_api_token(request):
    """
    Вернуть постоянный токен для весового агента текущей компании.
    """
    user = request.user

    # Подставь свою логику связи user -> company
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)

    if not company:
        return Response(
            {"detail": "Пользователь не привязан к компании."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token = company.ensure_scale_api_token()
    return Response({"scale_api_token": token})




@api_view(["POST"])
def register_scale(request):
    token = request.data.get("token")
    if not token:
        return Response({"detail": "token is required"}, status=status.HTTP_400_BAD_REQUEST)

    company = get_object_or_404(Company, scale_api_token=token)

    name = request.data.get("name") or "Весы"
    ip = request.data.get("ip")

    scale, created = ScaleDevice.objects.get_or_create(
        company=company,
        name=name,
        defaults={"ip_address": ip},
    )
    if not created:
        scale.ip_address = ip or scale.ip_address
    scale.last_seen_at = timezone.now()
    scale.save()

    return Response(
        {
            "id": str(scale.id),
            "created": created,
            "company": str(company.id),
        },
        status=status.HTTP_200_OK,
    )
    
    
    
@api_view(["GET"])
@authentication_classes([ScaleAgentAuthentication])
@permission_classes([AllowAny])  # потому что аутентификация своя
def scale_products_list(request):
    """
    Вернуть список только весовых/штучных товаров для компании агента.
    Авторизация: Authorization: Bearer <scale_api_token>
    """
    company = getattr(request.user, "company", None)
    if not company:
        return Response([])

    qs = Product.objects.filter(
        company=company,
        scale_type__in=["weight", "piece"],
        is_active=True,
    ).order_by("name")

    data = ProductSerializer(qs, many=True).data
    return Response(data)