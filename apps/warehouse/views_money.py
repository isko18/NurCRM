from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework import generics
from django_filters.rest_framework import DjangoFilterBackend

from .views import CompanyBranchRestrictedMixin
from . import models, serializers_money, services_money


class PaymentCategoryListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_money.PaymentCategorySerializer
    queryset = models.PaymentCategory.objects.all().order_by("title")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["company", "branch"]
    search_fields = ["title"]


class PaymentCategoryDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.PaymentCategorySerializer
    queryset = models.PaymentCategory.objects.all()


class MoneyDocumentListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer
    queryset = models.MoneyDocument.objects.select_related(
        "warehouse", "counterparty", "payment_category", "company", "branch"
    ).order_by("-date")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "warehouse", "counterparty", "payment_category"]
    search_fields = ["number", "comment", "counterparty__name"]


class MoneyDocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer
    queryset = models.MoneyDocument.objects.select_related(
        "warehouse", "counterparty", "payment_category", "company", "branch"
    )


class MoneyDocumentPostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer

    def get_queryset(self):
        return models.MoneyDocument.objects.select_related(
            "warehouse", "counterparty", "payment_category", "company", "branch"
        )

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services_money.post_money_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class MoneyDocumentUnpostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer

    def get_queryset(self):
        return models.MoneyDocument.objects.select_related(
            "warehouse", "counterparty", "payment_category", "company", "branch"
        )

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services_money.unpost_money_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class CounterpartyMoneyOperationsView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    Подробный список денежных операций по контрагенту.
    """

    serializer_class = serializers_money.MoneyDocumentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "warehouse", "payment_category"]
    search_fields = ["number", "comment"]

    def get_queryset(self):
        counterparty_id = self.kwargs.get("counterparty_id")
        qs = models.MoneyDocument.objects.select_related(
            "warehouse", "counterparty", "payment_category", "company", "branch"
        ).filter(counterparty_id=counterparty_id).order_by("-date")
        return self._filter_qs_company_branch(qs)

