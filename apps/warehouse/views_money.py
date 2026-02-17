from decimal import Decimal

from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from django.db import transaction
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

    @staticmethod
    def _wants_post(request) -> bool:
        """
        Backward compatible:
          - if client sends {"status": "POSTED"} on create/update, auto-post it
          - or {"post": true}
          - or ?post=1
        """
        data = getattr(request, "data", {}) or {}
        raw_status = data.get("status")
        raw_post = data.get("post")
        qp = getattr(request, "query_params", None)
        raw_qp = qp.get("post") if qp is not None else None

        if isinstance(raw_post, bool):
            return raw_post
        if isinstance(raw_post, str) and raw_post.strip().lower() in ("1", "true", "yes", "y"):
            return True
        if isinstance(raw_qp, str) and raw_qp.strip().lower() in ("1", "true", "yes", "y"):
            return True
        return str(raw_status).upper() == models.MoneyDocument.Status.POSTED

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wants_post = self._wants_post(request)
        with transaction.atomic():
            # save with company/branch from mixin
            self._save_with_company_branch(serializer)
            doc = serializer.instance
            if wants_post:
                try:
                    services_money.post_money_document(doc)
                except Exception as e:
                    raise ValidationError({"detail": str(e)})

        # refetch for consistent response
        doc = (
            models.MoneyDocument.objects
            .select_related("cash_register", "warehouse", "counterparty", "payment_category", "company", "branch")
            .get(pk=serializer.instance.pk)
        )
        out = self.get_serializer(doc)
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)


class MoneyDocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.MoneyDocumentSerializer
    queryset = models.MoneyDocument.objects.select_related(
        "cash_register", "warehouse", "counterparty", "payment_category", "company", "branch"
    )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        wants_post = MoneyDocumentListCreateView._wants_post(request)
        with transaction.atomic():
            self.perform_update(serializer)
            if wants_post and serializer.instance.status != models.MoneyDocument.Status.POSTED:
                try:
                    services_money.post_money_document(serializer.instance)
                except Exception as e:
                    raise ValidationError({"detail": str(e)})

        instance = (
            models.MoneyDocument.objects
            .select_related("cash_register", "warehouse", "counterparty", "payment_category", "company", "branch")
            .get(pk=serializer.instance.pk)
        )
        return Response(self.get_serializer(instance).data, status=status.HTTP_200_OK)


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

