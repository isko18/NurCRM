from rest_framework import status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend

from . import models, serializers_documents, services
from rest_framework.exceptions import ValidationError as DRFValidationError
from .views import CompanyBranchRestrictedMixin
from apps.utils import _is_owner_like


class DocumentListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["doc_type", "status", "payment_kind", "warehouse_from", "warehouse_to", "counterparty"]
    search_fields = ["number", "comment"]
    
    def _filter_company_branch(self, qs):
        company = self._company()
        if company is None:
            return qs.none()

        qs = qs.filter(
            Q(warehouse_from__company=company) | Q(warehouse_to__company=company)
        )

        branch = self._auto_branch()
        if branch is not None:
            qs = qs.filter(
                Q(warehouse_from__branch=branch) | Q(warehouse_to__branch=branch)
            )
        return qs

    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related(
            "items__product",
            "moves__warehouse",
            "moves__product",
        ).order_by("-date")
        qs = self._filter_company_branch(qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if _is_owner_like(user):
            self._save_with_company_branch(serializer)
            return
        self._save_with_company_branch(serializer, agent=user)


class AgentDocumentListCreateView(DocumentListCreateView):
    """
    Документы агента (операции по своим товарам).
    """
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(agent=self.request.user)

    def perform_create(self, serializer):
        serializer.save(agent=self.request.user)


class _DocumentTypedListCreateView(DocumentListCreateView):
    """
    Базовый класс для списков по одному типу документа.
    """
    DOC_TYPE = None  # override in subclasses

    def get_queryset(self):
        qs = super().get_queryset()
        if self.DOC_TYPE:
            qs = qs.filter(doc_type=self.DOC_TYPE)
        return qs

    def perform_create(self, serializer):
        extra = {}
        if self.DOC_TYPE:
            extra["doc_type"] = self.DOC_TYPE
        user = self.request.user
        if _is_owner_like(user):
            self._save_with_company_branch(serializer, **extra)
            return
        self._save_with_company_branch(serializer, agent=user, **extra)


class DocumentSaleListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.SALE


class DocumentPurchaseListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.PURCHASE


class DocumentSaleReturnListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.SALE_RETURN


class DocumentPurchaseReturnListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.PURCHASE_RETURN


class DocumentInventoryListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.INVENTORY


class DocumentReceiptListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.RECEIPT


class DocumentWriteOffListCreateView(_DocumentTypedListCreateView):
    DOC_TYPE = models.Document.DocType.WRITE_OFF


class DocumentTransferListCreateView(_DocumentTypedListCreateView):
    """
    Документы перемещения. При создании автоматически проводится —
    остатки снимаются со склада-источника и добавляются на склад-приёмник.
    """
    DOC_TYPE = models.Document.DocType.TRANSFER

    def perform_create(self, serializer):
        super().perform_create(serializer)
        doc = serializer.instance
        if doc and doc.status == doc.Status.DRAFT:
            try:
                allow_negative = self.request.data.get("allow_negative", False)
                if isinstance(allow_negative, str):
                    allow_negative = allow_negative.lower() in ("true", "1", "yes")
                services.post_document(doc, allow_negative=allow_negative)
                doc.refresh_from_db()
            except Exception as e:
                raise DRFValidationError({"detail": str(e)})


class DocumentDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related(
            "items__product",
            "items__product__brand",
            "items__product__category",
            "moves__warehouse",
            "moves__product",
        )
        qs = DocumentListCreateView._filter_company_branch(self, qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs


class AgentDocumentDetailView(DocumentDetailView):
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(agent=self.request.user)


class DocumentPostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем items с продуктами
        qs = models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("items__product", "items__product__warehouse")
        user = self.request.user
        qs = DocumentListCreateView._filter_company_branch(self, qs)
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            # Позволяем передать allow_negative в теле запроса для обхода проверки остатков
            allow_negative = request.data.get('allow_negative', False)
            if isinstance(allow_negative, str):
                allow_negative = allow_negative.lower() in ('true', '1', 'yes')
            services.post_document(doc, allow_negative=allow_negative)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class DocumentUnpostView(CompanyBranchRestrictedMixin, generics.GenericAPIView):
    serializer_class = serializers_documents.DocumentSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем moves с продуктами и складами
        qs = models.Document.objects.select_related(
            "warehouse_from", "warehouse_to", "counterparty"
        ).prefetch_related("moves__warehouse", "moves__product")
        qs = DocumentListCreateView._filter_company_branch(self, qs)
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def post(self, request, pk=None):
        doc = self.get_object()
        try:
            services.unpost_document(doc)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(doc).data)


class DocumentTransferCreateAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Быстрое перемещение товара (создает документ TRANSFER и сразу проводит).
    POST /api/warehouse/transfer/
    """
    def post(self, request, *args, **kwargs):
        ser = serializers_documents.TransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        company = self._company()
        branch = self._auto_branch()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        wh_from = ser.validated_data["warehouse_from"]
        wh_to = ser.validated_data["warehouse_to"]

        if company and (wh_from.company_id != company.id or wh_to.company_id != company.id):
            raise DRFValidationError({"warehouse": "Склад принадлежит другой компании."})

        if branch is not None:
            if wh_from.branch_id not in (None, branch.id) or wh_to.branch_id not in (None, branch.id):
                raise DRFValidationError({"warehouse": "Склад другого филиала."})
        else:
            if wh_from.branch_id is not None or wh_to.branch_id is not None:
                raise DRFValidationError({"warehouse": "Склад другого филиала."})

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=wh_from,
            warehouse_to=wh_to,
            comment=ser.validated_data.get("comment") or "",
        )

        for it in ser.validated_data["items"]:
            item = models.DocumentItem(document=doc, **it)
            try:
                item.clean()
            except Exception as e:
                raise DRFValidationError(getattr(e, "message_dict", {"detail": str(e)}))
            item.save()

        try:
            services.post_document(doc)
        except Exception as e:
            raise DRFValidationError({"detail": str(e)})

        out = serializers_documents.DocumentSerializer(doc, context={"request": request}).data
        return Response(out, status=status.HTTP_201_CREATED)


class ProductListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.ProductSimpleSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "article", "barcode"]
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.WarehouseProduct.objects.select_related(
            "warehouse", "brand", "category", "company", "branch"
        )
        qs = self._filter_qs_company_branch(qs)
        
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
                # Ищем товар и кэшируем его ID
                product = qs.filter(barcode=search).first()
                if product:
                    cache.set(cache_key, product.id, 300)  # Кэш на 5 минут
        
        return qs


class ProductDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.ProductSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.WarehouseProduct.objects.select_related(
            "warehouse", "brand", "category", "company", "branch"
        )
        return self._filter_qs_company_branch(qs)


class WarehouseListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = serializers_documents.WarehouseSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.Warehouse.objects.select_related("company", "branch")
        return self._filter_qs_company_branch(qs)


class WarehouseDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = serializers_documents.WarehouseSimpleSerializer
    
    def get_queryset(self):
        # Оптимизация: предзагружаем связанные объекты
        qs = models.Warehouse.objects.select_related("company", "branch")
        return self._filter_qs_company_branch(qs)


class CounterpartyListCreateView(CompanyBranchRestrictedMixin, generics.ListCreateAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if _is_owner_like(user):
            self._save_with_company_branch(serializer)
            return
        self._save_with_company_branch(serializer, agent=user)


class CounterpartyDetailView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = models.Counterparty.objects.all()
    serializer_class = serializers_documents.CounterpartySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(agent=user)
        return qs
