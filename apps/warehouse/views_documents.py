from rest_framework import status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend

from . import models, serializers_documents, services
from .views import CompanyBranchRestrictedMixin


class DocumentListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.Document.objects.all().order_by("-date")
    serializer_class = serializers_documents.DocumentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "warehouse_from", "warehouse_to", "counterparty"]
    search_fields = ["number", "comment"]


class DocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Document.objects.all()
    serializer_class = serializers_documents.DocumentSerializer


class DocumentPostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    queryset = models.Document.objects.all()
    serializer_class = serializers_documents.DocumentSerializer

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services.post_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class DocumentUnpostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    queryset = models.Document.objects.all()
    serializer_class = serializers_documents.DocumentSerializer

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services.unpost_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class ProductListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.WarehouseProduct.objects.all()
    serializer_class = serializers_documents.ProductSimpleSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "article", "barcode"]


class ProductDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.WarehouseProduct.objects.all()
    serializer_class = serializers_documents.ProductSimpleSerializer


class WarehouseListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.Warehouse.objects.all()
    serializer_class = serializers_documents.WarehouseSimpleSerializer


class WarehouseDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Warehouse.objects.all()
    serializer_class = serializers_documents.WarehouseSimpleSerializer


class CounterpartyListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer


class CounterpartyDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer
