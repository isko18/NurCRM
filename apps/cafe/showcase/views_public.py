# apps/cafe/views_public.py
from django.db.models import Prefetch
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import Company, Branch
from ..models import Category, MenuItem
from .serializers_public import (
    PublicCafeCompanySerializer,
    PublicBranchSerializer,
    PublicCategorySerializer,
    PublicMenuItemSerializer,
)


class PublicCafeInfoAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, company_slug: str):
        company = Company.objects.only("id", "name", "slug", "phone", "phones_howcase").get(slug=company_slug)

        # филиалы — если хочешь показывать (можно убрать)
        branches = Branch.objects.filter(company_id=company.id, is_active=True).only(
            "id", "name", "code", "address", "phone", "email"
        )

        return Response({
            "company": PublicCafeCompanySerializer(company, context={"request": request}).data,
            "branches": PublicBranchSerializer(branches, many=True).data,
        })


class PublicCafeMenuAPIView(generics.ListAPIView):
    """
    Возвращает категории и блюда по company_slug.
    По умолчанию показываем только is_active=True (это здраво для витрины).
    Если хочешь ВСЕ блюда — снимем фильтр.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicCategorySerializer
    pagination_class = None  # категории лучше отдавать одним списком

    def get_queryset(self):
        company_slug = self.kwargs["company_slug"]
        company = Company.objects.only("id").get(slug=company_slug)

        # ВИТРИНА: обычно только активные
        items_qs = (
            MenuItem.objects
            .filter(company_id=company.id, is_active=True)
            .select_related("category", "kitchen")
            .only("id", "title", "price", "is_active", "category_id", "kitchen_id", "image", "created_at")
            .order_by("title")
        )

        # prefetch, но сохраним под именем items_prefetched, чтобы сериализатор не делал N+1
        return (
            Category.objects
            .filter(company_id=company.id)
            .only("id", "title")
            .order_by("title")
            .prefetch_related(Prefetch("items", queryset=items_qs, to_attr="items_prefetched"))
        )


class PublicCafeMenuItemsAPIView(generics.ListAPIView):
    """
    Плоский список блюд для поиска.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicMenuItemSerializer

    def get_queryset(self):
        company_slug = self.kwargs["company_slug"]
        company = Company.objects.only("id").get(slug=company_slug)

        qs = (
            MenuItem.objects
            .filter(company_id=company.id, is_active=True)
            .select_related("category", "kitchen")
            .order_by("-created_at")
        )

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(title__icontains=q)

        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category_id=cat)

        return qs
