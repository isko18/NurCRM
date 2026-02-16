from decimal import Decimal

from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend

from .views import CompanyBranchRestrictedMixin
from . import models, serializers_money, services_money


class CashRegisterListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_money.CashRegisterSerializer
    queryset = models.CashRegister.objects.select_related("company", "branch").order_by("name")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["company", "branch"]
    search_fields = ["name", "location"]

    def perform_create(self, serializer):
        company = self._company()
        branch = self._auto_branch()
        if not company:
            # на всякий случай (не должно происходить при IsAuthenticated)
            raise ValidationError({"company": "Обязательное поле."})
        serializer.save(company=company, branch=branch)


class CashRegisterDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.CashRegisterSerializer
    queryset = models.CashRegister.objects.select_related("company", "branch")


class CashRegisterOperationsView(CompanyBranchRestrictedMixin, generics.RetrieveAPIView):
    """
    Детали кассы с балансом, приходами и расходами.
    GET /api/warehouse/cash-registers/{id}/operations/
    """

    queryset = models.CashRegister.objects.select_related("company", "branch")

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        docs = list(
            models.MoneyDocument.objects.filter(
                cash_register=instance,
                status=models.MoneyDocument.Status.POSTED,
            ).select_related("cash_register", "counterparty", "payment_category").order_by("-date")
        )
        receipts = []
        expenses = []
        receipts_sum = Decimal("0.00")
        expenses_sum = Decimal("0.00")
        for d in docs:
            data = serializers_money.MoneyDocumentSerializer(d).data
            if d.doc_type == models.MoneyDocument.DocType.MONEY_RECEIPT:
                receipts.append(data)
                receipts_sum += Decimal(d.amount or 0)
            else:
                expenses.append(data)
                expenses_sum += Decimal(d.amount or 0)
        balance = receipts_sum - expenses_sum

        data = serializers_money.CashRegisterSerializer(instance).data
        data["balance"] = str(balance)
        data["receipts"] = receipts
        data["expenses"] = expenses
        data["receipts_total"] = str(receipts_sum)
        data["expenses_total"] = str(expenses_sum)

        return Response(data)


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
        "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
    ).order_by("-date")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "cash_register", "warehouse", "counterparty", "payment_category"]
    search_fields = ["number", "comment", "counterparty__name"]


class MoneyDocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer
    queryset = models.MoneyDocument.objects.select_related(
        "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
    )


class MoneyDocumentPostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer

    def get_queryset(self):
        qs = models.MoneyDocument.objects.select_related(
            "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
        )
        return self._filter_qs_company_branch(qs)

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
        qs = models.MoneyDocument.objects.select_related(
            "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
        )
        return self._filter_qs_company_branch(qs)

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
    filterset_fields = ["doc_type", "status", "cash_register", "warehouse", "payment_category"]
    search_fields = ["number", "comment"]

    def get_queryset(self):
        counterparty_id = self.kwargs.get("counterparty_id")
        qs = models.MoneyDocument.objects.select_related(
            "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
        ).filter(counterparty_id=counterparty_id).order_by("-date")
        return self._filter_qs_company_branch(qs)

