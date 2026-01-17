from rest_framework import status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend

from . import models, serializers_documents, services
from .views import CompanyBranchRestrictedMixin


class DocumentListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "warehouse_from", "warehouse_to", "counterparty"]
    search_fields = ["number", "comment"]
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        return models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("items__product").order_by("-date")


class DocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        return models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("items__product", "items__product__brand", "items__product__category")


class DocumentPostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем items с продуктами
        return models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("items__product", "items__product__warehouse")

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services.post_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class DocumentUnpostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем moves с продуктами и складами
        return models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("moves__warehouse", "moves__product")

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services.unpost_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class ProductListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.ProductSimpleSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "article", "barcode"]
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.WarehouseProduct.objects.select_related(
            "warehouse", "brand", "category", "company", "branch"
        )
        
        # Кэширование поиска по barcode
        search = self.request.query_params.get("search", "").strip()
        if search and len(search) >= 8:  # Предполагаем, что barcode обычно длиннее 8 символов
            from django.core.cache import cache
            company = self._company()
            if company:
                cache_key = f"warehouse_product_barcode:{company.id}:{search}"
                cached_product_id = cache.get(cache_key)
                if cached_product_id:
                    # Если найден в кэше - возвращаем только этот товар
                    return qs.filter(pk=cached_product_id)
                else:
                    # Ищем товар и кэшируем его ID
                    product = qs.filter(barcode=search).first()
                    if product:
                        cache.set(cache_key, product.id, 300)  # Кэш на 5 минут
        
        return qs


class ProductDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.ProductSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        return models.WarehouseProduct.objects.select_related(
            "warehouse", "brand", "category", "company", "branch"
        )


class WarehouseListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.WarehouseSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        return models.Warehouse.objects.select_related("company", "branch")


class WarehouseDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.WarehouseSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        return models.Warehouse.objects.select_related("company", "branch")


class CounterpartyListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer


class CounterpartyDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer
