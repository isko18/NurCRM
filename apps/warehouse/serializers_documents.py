from decimal import Decimal
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import get_user_model
from apps.users.models import Company
from . import models
from . import services as warehouse_services
from .serializers import WarehouseProductCharacteristicsSerializer
from .utils import normalize_payment_kind

User = get_user_model()


class PaymentKindField(serializers.CharField):
    """Принимает credit/debt/cash и нормализует к Document.PaymentKind."""

    def to_internal_value(self, data):
        if data is None or data == "":
            return None
        normalized = normalize_payment_kind(data)
        allowed = {choice for choice, _label in models.Document.PaymentKind.choices}
        if normalized not in allowed:
            raise serializers.ValidationError("Укажите cash, credit (или debt) либо external.")
        return normalized


def _merge_product_discount_into_item(item_data: dict) -> dict:
    """
    Если клиент не задал скидку по строке (поле нет или null) — берём процент с карточки товара.
    Явный 0% в запросе не заменяем.
    """
    if "discount_percent" in item_data and item_data.get("discount_percent") is not None:
        return item_data
    product = item_data.get("product")
    if product is None:
        return item_data
    if hasattr(product, "discount_percent"):
        pct = getattr(product, "discount_percent", None) or Decimal("0")
    else:
        row = (
            models.WarehouseProduct.objects.filter(pk=product)
            .values_list("discount_percent", flat=True)
            .first()
        )
        pct = row or Decimal("0")
    pct = Decimal(str(pct))
    if pct > 0:
        merged = dict(item_data)
        merged["discount_percent"] = pct
        return merged
    merged = dict(item_data)
    if merged.get("discount_percent") is None:
        merged["discount_percent"] = Decimal("0.00")
    return merged


class StockMoveSerializer(serializers.ModelSerializer):
    """Сериализатор движения товара с видом: приход или расход."""

    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = models.StockMove
        fields = (
            "id",
            "document",
            "warehouse",
            "warehouse_name",
            "product",
            "product_name",
            "product_article",
            "qty_delta",
            "move_kind",
            "created_at",
        )


class DocumentItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    product_article = serializers.CharField(source="product.article", read_only=True, allow_null=True)
    warehouse = serializers.UUIDField(source="product.warehouse_id", read_only=True, allow_null=True)
    warehouse_name = serializers.CharField(source="product.warehouse.name", read_only=True, allow_null=True)
    product_image_url = serializers.SerializerMethodField()
    product_characteristics = WarehouseProductCharacteristicsSerializer(
        source="product.characteristics",
        read_only=True,
        allow_null=True,
    )
    product_discount_percent = serializers.DecimalField(
        source="product.discount_percent",
        max_digits=5,
        decimal_places=2,
        read_only=True,
    )
    product_discount_amount = serializers.SerializerMethodField()
    discount_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    discount_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    effective_discount_percent = serializers.SerializerMethodField()

    class Meta:
        model = models.DocumentItem
        fields = (
            "id",
            "product",
            "product_name",
            "product_article",
            "warehouse",
            "warehouse_name",
            "product_image_url",
            "product_characteristics",
            "product_discount_percent",
            "product_discount_amount",
            "qty",
            "price",
            "discount_percent",
            "discount_amount",
            "effective_discount_percent",
            "line_total",
        )

    def get_effective_discount_percent(self, obj):
        doc = getattr(obj, "document", None)
        doc_dp = Decimal(getattr(doc, "discount_percent", None) or 0) if doc else Decimal("0")
        eff = warehouse_services.effective_document_line_discount_percent(obj.discount_percent, doc_dp)
        return eff.quantize(Decimal("0.01"))

    def get_product_discount_amount(self, obj):
        """Сумма скидки по проценту с карточки товара для текущих цены и количества в строке."""
        p = getattr(obj, "product", None)
        if not p:
            return Decimal("0.00").quantize(Decimal("0.01"))
        price = Decimal(obj.price or 0)
        qty = Decimal(obj.qty or 0)
        pct = Decimal(getattr(p, "discount_percent", None) or 0)
        return (price * qty * pct / Decimal("100")).quantize(Decimal("0.01"))

    def get_product_image_url(self, obj):
        request = self.context.get("request")
        p = getattr(obj, "product", None)
        if not p:
            return None
        img_row = None
        cache = getattr(p, "_prefetched_objects_cache", None)
        if cache and "images" in cache:
            imgs = list(p.images.all())
            img_row = imgs[0] if imgs else None
        else:
            img_row = (
                models.WarehouseProductImage.objects.filter(product=p)
                .order_by("-is_primary", "created_at")
                .first()
            )
        if not img_row or not getattr(img_row, "image", None):
            return None
        url = img_row.image.url
        return request.build_absolute_uri(url) if request else url


class DocumentSerializer(serializers.ModelSerializer):
    items = DocumentItemSerializer(many=True)
    moves = StockMoveSerializer(many=True, read_only=True)
    receipts = serializers.SerializerMethodField()
    expenses = serializers.SerializerMethodField()
    payment_kind = PaymentKindField(required=False, allow_null=True, allow_blank=True)

    money_document_id = serializers.SerializerMethodField()
    money_document_number = serializers.SerializerMethodField()
    money_document_status = serializers.SerializerMethodField()
    money_document_amount = serializers.SerializerMethodField()

    cash_register_name = serializers.CharField(source="cash_register.name", read_only=True, allow_null=True)
    payment_category_title = serializers.CharField(source="payment_category.title", read_only=True, allow_null=True)
    cash_request_status = serializers.SerializerMethodField()

    counterparty_display_name = serializers.CharField(
        source="counterparty.name", read_only=True, allow_null=True
    )
    warehouse_from_name = serializers.CharField(
        source="warehouse_from.name", read_only=True, allow_null=True
    )
    warehouse_to_name = serializers.CharField(
        source="warehouse_to.name", read_only=True, allow_null=True
    )
    agent = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
        help_text="Агент по документу; при указании контрагент должен быть закреплён за этим агентом.",
    )
    agent_display = serializers.SerializerMethodField()

    class Meta:
        ref_name = "WarehouseDocumentSerializer"
        model = models.Document
        fields = (
            "id",
            "doc_type",
            "status",
            "number",
            "date",
            "payment_kind",
            "prepayment_amount",
            "warehouse_from",
            "warehouse_to",
            "warehouse_from_name",
            "warehouse_to_name",
            "counterparty",
            "cash_register",
            "cash_register_name",
            "payment_category",
            "payment_category_title",
            "cash_request_status",
            "money_document_id",
            "money_document_number",
            "money_document_status",
            "money_document_amount",
            "agent",
            "agent_display",
            "is_sale_request",
            "counterparty_display_name",
            "comment",
            "discount_percent",
            "discount_amount",
            "total",
            "items",
            "moves",
            "receipts",
            "expenses",
        )
        read_only_fields = ("number", "total", "status", "date", "cash_request_status")

    @staticmethod
    def _resolve_sale_status(doc_type, is_sale_request):
        if doc_type == models.Document.DocType.SALE and bool(is_sale_request):
            return models.Document.Status.SALE_REQUEST
        return models.Document.Status.DRAFT

    def _apply_multi_warehouse_defaults(self, attrs):
        """Для продажи владельца: warehouse_from необязателен, подставляется из первой строки."""
        doc_type = attrs.get("doc_type") or getattr(self.instance, "doc_type", None)
        agent = attrs.get("agent")
        if agent is None and self.instance is not None:
            agent = getattr(self.instance, "agent", None)
        if doc_type not in warehouse_services.MULTI_WAREHOUSE_DOC_TYPES or agent:
            return attrs

        items = attrs.get("items")
        if not items:
            return attrs

        if attrs.get("warehouse_from") is not None:
            wh_company_id = attrs["warehouse_from"].company_id
            for it in items:
                product = it.get("product")
                if product is None:
                    continue
                if product.company_id != wh_company_id:
                    raise serializers.ValidationError(
                        {"items": "Все товары должны принадлежать той же компании, что и склад документа."}
                    )
            return attrs

        first_product = items[0].get("product")
        if first_product is not None and getattr(first_product, "warehouse_id", None):
            attrs = dict(attrs)
            attrs["warehouse_from"] = first_product.warehouse
        return attrs

    def validate(self, attrs):
        attrs = self._apply_multi_warehouse_defaults(attrs)
        return super().validate(attrs) if hasattr(super(), "validate") else attrs

    def get_agent_display(self, obj):
        u = getattr(obj, "agent", None)
        if not u:
            return None
        if hasattr(u, "get_full_name") and u.get_full_name():
            return u.get_full_name()
        return (
            f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            or getattr(u, "username", None)
            or getattr(u, "email", None)
            or str(getattr(u, "id", ""))
        )

    @staticmethod
    def _safe_one_to_one(obj, attr: str):
        """Обратная OneToOne без записи бросает RelatedObjectDoesNotExist — getattr это не ловит."""
        try:
            return getattr(obj, attr)
        except ObjectDoesNotExist:
            return None

    def get_money_document_id(self, obj):
        md = self._safe_one_to_one(obj, "money_document")
        return md.id if md else None

    def get_money_document_number(self, obj):
        md = self._safe_one_to_one(obj, "money_document")
        return md.number if md else None

    def get_money_document_status(self, obj):
        md = self._safe_one_to_one(obj, "money_document")
        return md.status if md else None

    def get_money_document_amount(self, obj):
        md = self._safe_one_to_one(obj, "money_document")
        return md.amount if md else None

    def get_cash_request_status(self, obj):
        req = self._safe_one_to_one(obj, "cash_request")
        return getattr(req, "status", None) if req else None

    def get_receipts(self, obj):
        """Приходы — движения с move_kind=RECEIPT."""
        moves = getattr(obj, "_receipts_moves", None)
        if moves is None and hasattr(obj, "moves"):
            moves = [m for m in obj.moves.all() if m.move_kind == models.StockMove.MoveKind.RECEIPT]
        if moves is None:
            return []
        return StockMoveSerializer(moves, many=True).data

    def get_expenses(self, obj):
        """Расходы — движения с move_kind=EXPENSE."""
        moves = getattr(obj, "_expenses_moves", None)
        if moves is None and hasattr(obj, "moves"):
            moves = [m for m in obj.moves.all() if m.move_kind == models.StockMove.MoveKind.EXPENSE]
        if moves is None:
            return []
        return StockMoveSerializer(moves, many=True).data

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        doc_type = validated_data.get("doc_type")
        is_sale_request = validated_data.get("is_sale_request", False)
        validated_data["status"] = self._resolve_sale_status(doc_type, is_sale_request)
        
        # Валидация документа перед созданием
        doc = models.Document(**validated_data)
        try:
            doc.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        
        doc = super().create(validated_data)
        
        # Валидация и создание items
        for it in items:
            it = _merge_product_discount_into_item(dict(it))
            item = models.DocumentItem(document=doc, **it)
            try:
                item.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
            item.save()

        warehouse_services.recalc_document_totals(doc)
        doc.refresh_from_db()
        return doc

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        
        # Проверяем, что документ не проведен
        if instance.status in (instance.Status.POSTED, instance.Status.CASH_PENDING):
            raise serializers.ValidationError(
                {"status": "Нельзя изменять проведенный/ожидающий кассу документ. Сначала отмените проведение."}
            )
        
        # Валидация документа перед обновлением
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if ("is_sale_request" in validated_data) or ("doc_type" in validated_data):
            instance.status = self._resolve_sale_status(instance.doc_type, instance.is_sale_request)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        
        instance = super().update(instance, validated_data)
        
        if items is not None:
            # Удаляем старые items
            instance.items.all().delete()
            
            # Валидация и создание новых items
            for it in items:
                it = _merge_product_discount_into_item(dict(it))
                item = models.DocumentItem(document=instance, **it)
                try:
                    item.clean()
                except DjangoValidationError as e:
                    raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
                item.save()

        warehouse_services.recalc_document_totals(instance)
        instance.refresh_from_db()
        return instance


class CashRequestDocumentMiniSerializer(serializers.ModelSerializer):
    warehouse_from_name = serializers.CharField(source="warehouse_from.name", read_only=True, allow_null=True)
    counterparty_display_name = serializers.CharField(source="counterparty.name", read_only=True, allow_null=True)

    class Meta:
        model = models.Document
        fields = (
            "id",
            "number",
            "doc_type",
            "status",
            "payment_kind",
            "date",
            "total",
            "warehouse_from",
            "warehouse_from_name",
            "counterparty",
            "counterparty_display_name",
            "cash_register",
            "payment_category",
        )


class CashApprovalRequestSerializer(serializers.ModelSerializer):
    document = CashRequestDocumentMiniSerializer(read_only=True)
    money_document_id = serializers.UUIDField(source="money_document.id", read_only=True, allow_null=True)
    decided_by_id = serializers.UUIDField(source="decided_by.id", read_only=True, allow_null=True)

    class Meta:
        model = models.CashApprovalRequest
        fields = (
            "id",
            "status",
            "requires_money",
            "money_doc_type",
            "amount",
            "decision_note",
            "requested_at",
            "decided_at",
            "decided_by_id",
            "money_document_id",
            "document",
        )


class CashApprovalDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class TransferItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=models.WarehouseProduct.objects.all())
    qty = serializers.DecimalField(max_digits=18, decimal_places=3)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))


class TransferCreateSerializer(serializers.Serializer):
    warehouse_from = serializers.PrimaryKeyRelatedField(queryset=models.Warehouse.objects.all())
    warehouse_to = serializers.PrimaryKeyRelatedField(queryset=models.Warehouse.objects.all())
    comment = serializers.CharField(required=False, allow_blank=True)
    items = TransferItemInputSerializer(many=True)

    def validate(self, attrs):
        items = attrs.get("items") or []
        if not items:
            raise serializers.ValidationError({"items": "Нельзя проводить пустое перемещение."})
        if attrs["warehouse_from"] == attrs["warehouse_to"]:
            raise serializers.ValidationError({"warehouse_to": "Склад-источник и склад-приемник должны быть разными."})
        return attrs


class ProductSimpleSerializer(serializers.ModelSerializer):
    group = serializers.UUIDField(source="group.id", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)
    alternate_barcodes = serializers.SerializerMethodField()

    class Meta:
        model = models.WarehouseProduct
        fields = ("id", "name", "article", "barcode", "unit", "quantity", "group", "group_name", "alternate_barcodes")

    def get_alternate_barcodes(self, obj):
        return list(obj.alternate_barcodes.order_by("barcode").values_list("barcode", flat=True))


class WarehouseSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Warehouse
        fields = ("id", "name")


class CounterpartySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    agent_display = serializers.SerializerMethodField()
    analytics = serializers.SerializerMethodField()
    agent = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
        help_text="Назначить контрагента агенту (только владелец/админ). Агент видит только контрагентов, назначенных ему.",
    )

    class Meta:
        model = models.Counterparty
        fields = (
            "id",
            "name",
            "phone",
            "type",
            "inn",
            "okpo",
            "score",
            "bik",
            "address",
            "company",
            "branch",
            "agent",
            "agent_display",
            "analytics",
        )
        read_only_fields = ("id", "company", "branch", "analytics")
        extra_kwargs = {
            "phone": {"required": True},
        }

    def get_agent_display(self, obj):
        u = getattr(obj, "agent", None)
        if not u:
            return None
        if hasattr(u, "get_full_name") and u.get_full_name():
            return u.get_full_name()
        return f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or getattr(u, "email", "") or str(u.id)

    def get_analytics(self, obj):
        m = self.context.get("counterparty_analytics_map")
        if not m:
            return None
        return m.get(obj.pk) or m.get(str(obj.pk))


class CompanyStockPartnershipRequestSerializer(serializers.ModelSerializer):
    from_company_name = serializers.CharField(source="from_company.name", read_only=True)
    to_company_name = serializers.CharField(source="to_company.name", read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, allow_null=True)
    decided_by_email = serializers.EmailField(source="decided_by.email", read_only=True, allow_null=True)

    class Meta:
        model = models.CompanyStockPartnershipRequest
        fields = (
            "id",
            "from_company",
            "from_company_name",
            "to_company",
            "to_company_name",
            "status",
            "note",
            "created_by",
            "created_by_email",
            "decided_by",
            "decided_by_email",
            "created_at",
            "decided_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "from_company",
            "status",
            "created_by",
            "decided_by",
            "created_at",
            "decided_at",
            "updated_at",
        )


class CompanyStockPartnershipRequestCreateSerializer(serializers.Serializer):
    to_company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())
    note = serializers.CharField(required=False, allow_blank=True, max_length=512)
