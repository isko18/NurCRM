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
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

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

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def send_products_to_scale(request):
    user = request.user
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)

    if not company:
        return Response(
            {"detail": "Пользователь не привязан к компании"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    product_ids = request.data.get("product_ids") or []
    plu_start = int(request.data.get("plu_start") or 1)

    qs = Product.objects.filter(company=company, kind=Product.Kind.PRODUCT)

    if product_ids:
        qs = qs.filter(id__in=product_ids)

    # Если хочешь слать только товары для весов:
    # - весовые (is_weight=True)
    # - и/или штучные (is_weight=False) — решай сам
    # Сейчас: шлём и весовые и штучные товары (оба типа)
    qs = qs.order_by("id")

    items = []
    cur_plu = plu_start

    for p in qs:
        name = p.name or ""
        price = float(p.price or 0)
        shelf = int(getattr(p, "shelf_life_days", 0) or 0)

        is_piece = not bool(p.is_weight)

        # используем product.plu, если есть, иначе назначаем
        plu_was_missing = p.plu is None
        plu_value = p.plu if p.plu is not None else cur_plu

        if plu_was_missing:
            p.plu = int(plu_value)
            p.save(update_fields=["plu"])

        code = int(plu_value)

        barcode = getattr(p, "barcode", None)
        barcode = str(barcode) if barcode else None

        items.append(
            {
                "product_uuid": str(p.id),
                "plu_number": int(plu_value),
                "code": int(plu_value),
                "name": name,
                "price": price,
                "shelf_life_days": shelf,
                "is_piece": is_piece,
                "barcode": barcode,
            }
        )

        if plu_was_missing:
            cur_plu += 1
        else:
            cur_plu = max(cur_plu, int(plu_value) + 1)

    if not items:
        return Response(
            {"detail": "Нет товаров для отправки"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    channel_layer = get_channel_layer()
    group_name = f"scale_company_{company.id}"

    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "send_scale_payload",
            "payload": {"action": "plu_batch", "items": items},
        },
    )

    return Response({"sent": len(items), "items": items}, status=status.HTTP_200_OK)
