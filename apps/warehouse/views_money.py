from decimal import Decimal

from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Sum, Q
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

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        qs = self.filter_queryset(self.get_queryset())
        register_ids = list(qs.values_list("pk", flat=True))
        if register_ids:
            agg = models.MoneyDocument.objects.filter(
                cash_register_id__in=register_ids,
                status=models.MoneyDocument.Status.POSTED,
            ).aggregate(
                receipts_total=Sum(
                    "amount",
                    filter=Q(doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT),
                    default=Decimal("0.00"),
                ),
                expenses_total=Sum(
                    "amount",
                    filter=Q(doc_type=models.MoneyDocument.DocType.MONEY_EXPENSE),
                    default=Decimal("0.00"),
                ),
            )
            response.data["receipts_total"] = str(agg["receipts_total"] or Decimal("0.00"))
            response.data["expenses_total"] = str(agg["expenses_total"] or Decimal("0.00"))
        else:
            response.data["receipts_total"] = "0.00"
            response.data["expenses_total"] = "0.00"
        return response


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

    def perform_create(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_wh_payment_category_title_per_branch" in msg
                or "uq_wh_payment_category_title_global_per_company" in msg
            ):
                raise ValidationError({"title": "Категория платежа с таким названием уже существует."})
            raise


class PaymentCategoryDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_money.PaymentCategorySerializer
    queryset = models.PaymentCategory.objects.all()

    def perform_update(self, serializer):
        try:
            self._save_with_company_branch(serializer)
        except IntegrityError as e:
            msg = str(e)
            if (
                "uq_wh_payment_category_title_per_branch" in msg
                or "uq_wh_payment_category_title_global_per_company" in msg
            ):
                raise ValidationError({"title": "Категория платежа с таким названием уже существует."})
            raise


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

    @staticmethod
    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

    def list(self, request, *args, **kwargs):
        """
        Backward compatible:
        - default: returns the same list of MoneyDocument as before.
        - if ?include_debts=1: returns an object with merged operations (money + кредитные складские документы).
        """
        include_debts = self._truthy(request.query_params.get("include_debts"))
        if not include_debts:
            return super().list(request, *args, **kwargs)

        # 1) money operations (respect existing filters/pagination/search)
        money_resp = super().list(request, *args, **kwargs)
        money_payload = money_resp.data

        # Flatten money results for merging
        if isinstance(money_payload, dict) and "results" in money_payload:
            money_items = list(money_payload.get("results") or [])
        else:
            money_items = list(money_payload or [])

        # 2) debt operations from warehouse documents (credit only)
        counterparty_id = self.kwargs.get("counterparty_id")
        doc_qs = (
            models.Document.objects
            .select_related("warehouse_from", "counterparty")
            .filter(
                counterparty_id=counterparty_id,
                status=models.Document.Status.POSTED,
                payment_kind=models.Document.PaymentKind.CREDIT,
                doc_type__in=(
                    models.Document.DocType.SALE,
                    models.Document.DocType.PURCHASE,
                    models.Document.DocType.SALE_RETURN,
                    models.Document.DocType.PURCHASE_RETURN,
                ),
            )
            .order_by("-date")
        )
        doc_qs = self._filter_qs_company_branch(
            doc_qs,
            company_field="warehouse_from__company_id",
            branch_field="warehouse_from__branch",
        )

        def _doc_debt_delta(doc) -> Decimal:
            amt = Decimal(getattr(doc, "total", None) or 0).quantize(Decimal("0.01"))
            # Consistent with reconciliation: debit increases counterparty debt to company; credit decreases it.
            if doc.doc_type in (models.Document.DocType.SALE, models.Document.DocType.PURCHASE_RETURN):
                return amt
            return -amt

        debt_ops = []
        for d in doc_qs:
            amt = Decimal(getattr(d, "total", None) or 0).quantize(Decimal("0.01"))
            debt_ops.append(
                {
                    "source": "document",
                    "id": str(d.id),
                    "date": d.date.isoformat() if getattr(d, "date", None) else None,
                    "number": d.number,
                    "status": d.status,
                    "doc_type": d.doc_type,
                    "payment_kind": d.payment_kind,
                    "amount": str(amt),
                    "debt_delta": str(_doc_debt_delta(d)),
                    "comment": (d.comment or ""),
                    "cash_register": None,
                    "payment_category": None,
                }
            )

        def _money_debt_delta(item: dict) -> Decimal:
            amt = Decimal(str(item.get("amount") or "0")).quantize(Decimal("0.01"))
            if item.get("doc_type") == models.MoneyDocument.DocType.MONEY_EXPENSE:
                return amt
            return -amt

        merged = []
        for it in money_items:
            merged.append(
                {
                    "source": "money",
                    "id": str(it.get("id")),
                    "date": it.get("date"),
                    "number": it.get("number"),
                    "status": it.get("status"),
                    "doc_type": it.get("doc_type"),
                    "payment_kind": None,
                    "amount": str(it.get("amount")),
                    "debt_delta": str(_money_debt_delta(it)),
                    "comment": it.get("comment") or "",
                    "cash_register": it.get("cash_register"),
                    "payment_category": it.get("payment_category"),
                }
            )
        merged.extend(debt_ops)
        merged.sort(key=lambda x: x.get("date") or "", reverse=True)

        return Response(
            {
                "money": money_payload,
                "debt_operations": debt_ops,
                "operations": merged,
            },
            status=money_resp.status_code,
            headers=getattr(money_resp, "headers", None),
        )

