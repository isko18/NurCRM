# apps/scales/views.py
from rest_framework.decorators import api_view, permission_classes
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

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_scale_api_token(request):
    """
    Вернуть постоянный токен для весов/агентов этой компании.
    Если ещё не сгенерирован — создать.
    """
    user = request.user

    # как у тебя устроено: либо user.company, либо owned_company
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if not company:
        return Response({"detail": "Пользователь не привязан к компании."},
                        status=status.HTTP_400_BAD_REQUEST)

    token = company.ensure_scale_token()
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
    
    
    
class ScaleProductListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/products/scale/

    Отдаёт только товары, которые используются с весами:
    - scale_type = piece (штучные)
    - scale_type = weight (весовые)
    """
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode"]
    ordering_fields = ["created_at", "updated_at", "price", "name"]
    ordering = ["name"]

    def get_queryset(self):
        qs = (
            Product.objects
            .select_related("brand", "category", "client")
            .prefetch_related("item_make", product_images_prefetch)
            .all()
        )
        qs = self._filter_qs_company_branch(qs)

        # только весовые и штучные
        return qs.filter(
            scale_type__in=[
                Product.ScaleType.PIECE,
                Product.ScaleType.WEIGHT,
            ]
        )