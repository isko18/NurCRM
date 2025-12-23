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
from django.db import transaction, connection, IntegrityError

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

def _pg_lock_company(company_id):
    if connection.vendor != "postgresql" or not company_id:
        return
    key = int(str(company_id).replace("-", "")[:16], 16) & 0x7FFFFFFFFFFFFFFF
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint);", [key])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def send_products_to_scale(request):
    user = request.user
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if not company:
        return Response({"detail": "Пользователь не привязан к компании"}, status=status.HTTP_400_BAD_REQUEST)

    product_ids = request.data.get("product_ids") or []
    plu_start = int(request.data.get("plu_start") or 1)

    qs = Product.objects.filter(company=company, kind=Product.Kind.PRODUCT)

    if product_ids:
        qs = qs.filter(id__in=product_ids)

    # ВАЖНО: если по твоей модели PLU нужен только весовым
    # (у тебя там auto_generate_plu завязан на is_weight)
    qs = qs.filter(is_weight=True).order_by("id")

    items = []

    with transaction.atomic():
        _pg_lock_company(company.id)

        # занятые PLU в компании
        used = set(
            Product.objects
            .filter(company=company, plu__isnull=False)
            .values_list("plu", flat=True)
        )

        cur_plu = max(plu_start, 1)

        def next_free_plu(start):
            x = int(start)
            while x in used:
                x += 1
            return x

        for p in qs:
            name = p.name or ""
            price = float(p.price or 0)
            shelf = int(getattr(p, "shelf_life_days", 0) or 0)

            plu_value = p.plu
            if plu_value is None:
                plu_value = next_free_plu(cur_plu)

                # сохраняем и сразу отмечаем как занятый
                p.plu = int(plu_value)

                # на всякий: если вдруг гонка/уникальность — поднимем PLU и повторим
                while True:
                    try:
                        p.save(update_fields=["plu"])
                        break
                    except IntegrityError:
                        # кто-то успел занять этот PLU — берём следующий
                        used.add(int(plu_value))
                        plu_value = next_free_plu(int(plu_value) + 1)
                        p.plu = int(plu_value)

                used.add(int(plu_value))
                cur_plu = int(plu_value) + 1
            else:
                used.add(int(plu_value))
                cur_plu = max(cur_plu, int(plu_value) + 1)

            items.append(
                {
                    "product_uuid": str(p.id),
                    "plu_number": int(plu_value),
                    "code": int(plu_value),
                    "name": name,
                    "price": price,
                    "shelf_life_days": shelf,
                    "is_piece": False,  # раз фильтруем is_weight=True
                    "barcode": str(p.barcode) if p.barcode else None,
                }
            )

    if not items:
        return Response({"detail": "Нет весовых товаров для отправки"}, status=status.HTTP_400_BAD_REQUEST)

    channel_layer = get_channel_layer()
    group_name = f"scale_company_{company.id}"

    async_to_sync(channel_layer.group_send)(
        group_name,
        {"type": "send_scale_payload", "payload": {"action": "plu_batch", "items": items}},
    )

    return Response({"sent": len(items), "items": items}, status=status.HTTP_200_OK)