import uuid
from decimal import Decimal

from rest_framework import serializers
from django.db import transaction
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    ResidentialComplex,
    ResidentialComplexDrawing,
    ResidentialComplexWarehouse,
    ResidentialComplexApartment,
    BuildingProduct,
    BuildingProcurementRequest,
    BuildingProcurementItem,
    BuildingProcurementCashDecision,
    BuildingTransferRequest,
    BuildingTransferItem,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingWorkflowEvent,
    BuildingClient,
    BuildingTreatyNumberSequence,
    BuildingTreaty,
    BuildingTreatyInstallment,
    BuildingTreatyFile,
    BuildingWorkEntry,
    BuildingWorkEntryPhoto,
    BuildingTask,
    BuildingTaskAssignee,
    BuildingTaskChecklistItem,
    BuildingEmployeeCompensation,
    BuildingPayrollPeriod,
    BuildingPayrollLine,
    BuildingPayrollAdjustment,
    BuildingPayrollPayment,
)

User = get_user_model()


class ResidentialComplexSerializer(serializers.ModelSerializer):
    """Сериализатор для ЖК: список и детали."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "company",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class ResidentialComplexCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания ЖК. company подставляется из request.user."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        company = self.context["request"].user.company
        if not company:
            raise serializers.ValidationError({"company": "У пользователя не указана компания."})
        validated_data["company_id"] = company.id
        return super().create(validated_data)


class ResidentialComplexDrawingSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ResidentialComplexDrawing
        fields = [
            "id",
            "residential_complex",
            "title",
            "file",
            "file_url",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "file_url", "created_at", "updated_at"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "file", None):
            return None
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def validate_residential_complex(self, value):
        user = self.context["request"].user
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            user_company_id = getattr(user, "company_id", None)
            if not user_company_id:
                raise serializers.ValidationError("У пользователя не указана компания.")
            if value.company_id != user_company_id:
                raise serializers.ValidationError("ЖК принадлежит другой компании.")
        return value


class ResidentialComplexWarehouseSerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)

    class Meta:
        model = ResidentialComplexWarehouse
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ResidentialComplexApartmentSerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)

    class Meta:
        model = ResidentialComplexApartment
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "floor",
            "number",
            "rooms",
            "area",
            "price",
            "status",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "residential_complex_name"]


class BuildingProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingProduct
        fields = [
            "id",
            "company",
            "name",
            "article",
            "barcode",
            "unit",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class BuildingProcurementItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = BuildingProcurementItem
        fields = [
            "id",
            "procurement",
            "product",
            "product_name",
            "product_article",
            "name",
            "unit",
            "quantity",
            "price",
            "line_total",
            "order",
            "note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "line_total", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        procurement = attrs.get("procurement") or getattr(self.instance, "procurement", None)
        product = attrs.get("product")
        if product and procurement and product.company_id != procurement.residential_complex.company_id:
            raise serializers.ValidationError({"product": "Товар принадлежит другой компании."})
        if product:
            attrs.setdefault("name", product.name)
            attrs.setdefault("unit", product.unit)
        return attrs


class BuildingProcurementCashDecisionSerializer(serializers.ModelSerializer):
    decided_by_display = serializers.SerializerMethodField()

    class Meta:
        model = BuildingProcurementCashDecision
        fields = ["id", "procurement", "decision", "reason", "decided_by", "decided_by_display", "decided_at"]
        read_only_fields = ["id", "decided_at", "decided_by_display"]

    def get_decided_by_display(self, obj):
        user = getattr(obj, "decided_by", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingTransferItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingTransferItem
        fields = [
            "id",
            "transfer",
            "procurement_item",
            "name",
            "unit",
            "quantity",
            "price",
            "line_total",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "line_total", "created_at", "updated_at"]


class BuildingTransferSerializer(serializers.ModelSerializer):
    items = BuildingTransferItemSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    procurement_title = serializers.CharField(source="procurement.title", read_only=True)

    class Meta:
        model = BuildingTransferRequest
        fields = [
            "id",
            "procurement",
            "procurement_title",
            "warehouse",
            "warehouse_name",
            "created_by",
            "decided_by",
            "status",
            "note",
            "rejection_reason",
            "total_amount",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
            "items",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "decided_by",
            "status",
            "rejection_reason",
            "total_amount",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
            "items",
        ]


class BuildingProcurementSerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    initiator_display = serializers.SerializerMethodField()
    cash_decision = BuildingProcurementCashDecisionSerializer(read_only=True)
    transfers = BuildingTransferSerializer(many=True, read_only=True)
    items = BuildingProcurementItemSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingProcurementRequest
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "initiator",
            "initiator_display",
            "title",
            "comment",
            "status",
            "total_amount",
            "submitted_to_cash_at",
            "cash_decided_at",
            "cash_decided_by",
            "created_at",
            "updated_at",
            "cash_decision",
            "items",
            "transfers",
        ]
        read_only_fields = [
            "id",
            "initiator",
            "status",
            "total_amount",
            "submitted_to_cash_at",
            "cash_decided_at",
            "cash_decided_by",
            "created_at",
            "updated_at",
            "cash_decision",
            "items",
            "transfers",
        ]

    def get_initiator_display(self, obj):
        user = getattr(obj, "initiator", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingWorkflowEventSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = BuildingWorkflowEvent
        fields = [
            "id",
            "procurement",
            "procurement_item",
            "transfer",
            "transfer_item",
            "warehouse",
            "stock_item",
            "actor",
            "actor_display",
            "action",
            "from_status",
            "to_status",
            "message",
            "payload",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_display(self, obj):
        user = getattr(obj, "actor", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingWarehouseStockItemSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = BuildingWarehouseStockItem
        fields = ["id", "warehouse", "warehouse_name", "name", "unit", "quantity", "last_price", "created_at", "updated_at"]
        read_only_fields = fields


class BuildingWarehouseStockMoveSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    stock_item_name = serializers.CharField(source="stock_item.name", read_only=True)

    class Meta:
        model = BuildingWarehouseStockMove
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "stock_item",
            "stock_item_name",
            "transfer",
            "move_type",
            "quantity_delta",
            "price",
            "created_by",
            "created_at",
        ]
        read_only_fields = fields


class BuildingReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)


class BuildingTransferCreateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class BuildingTransferAcceptSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class BuildingClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingClient
        fields = [
            "id",
            "company",
            "name",
            "phone",
            "email",
            "inn",
            "address",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]

class BuildingClientDetailSerializer(BuildingClientSerializer):
    treaties = serializers.SerializerMethodField(read_only=True)

    class Meta(BuildingClientSerializer.Meta):
        fields = BuildingClientSerializer.Meta.fields + ["treaties"]

    def get_treaties(self, obj):
        # В карточке клиента возвращаем договора/сделки по квартирам вместе с файлами и рассрочкой.
        treaties = getattr(obj, "treaties", None)
        if treaties is None:
            return []
        qs = treaties.select_related("residential_complex", "apartment", "created_by").prefetch_related("files", "installments")
        return BuildingTreatySerializer(qs, many=True, context=self.context).data


class BuildingTreatyFileSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingTreatyFile
        fields = ["id", "treaty", "title", "file", "file_url", "created_by", "created_at"]
        read_only_fields = ["id", "created_by", "created_at", "file_url"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "file", None):
            return None
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class BuildingTreatyFileCreateSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    title = serializers.CharField(required=False, allow_blank=True)


class BuildingTreatySerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)
    apartment_number = serializers.CharField(source="apartment.number", read_only=True)
    apartment_floor = serializers.IntegerField(source="apartment.floor", read_only=True)
    files = BuildingTreatyFileSerializer(many=True, read_only=True)
    created_by_display = serializers.SerializerMethodField()
    installments = serializers.SerializerMethodField(read_only=True)

    def _installments_qs(self, obj):
        rel = getattr(obj, "installments", None)
        if rel is None:
            return []
        return rel.all().order_by("order", "due_date", "created_at")

    def get_installments(self, obj):
        return BuildingTreatyInstallmentSerializer(self._installments_qs(obj), many=True).data

    class Meta:
        model = BuildingTreaty
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "client",
            "client_name",
            "created_by",
            "created_by_display",
            "number",
            "title",
            "description",
            "amount",
            "apartment",
            "apartment_number",
            "apartment_floor",
            "operation_type",
            "payment_type",
            "down_payment",
            "payment_terms",
            "status",
            "signed_at",
            "auto_create_in_erp",
            "erp_sync_status",
            "erp_external_id",
            "erp_last_error",
            "erp_requested_at",
            "erp_synced_at",
            "created_at",
            "updated_at",
            "files",
            "installments",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "created_by_display",
            "erp_sync_status",
            "erp_external_id",
            "erp_last_error",
            "erp_requested_at",
            "erp_synced_at",
            "created_at",
            "updated_at",
            "files",
            "residential_complex_name",
            "client_name",
            "apartment_number",
            "apartment_floor",
            "installments",
        ]

    def get_created_by_display(self, obj):
        user = getattr(obj, "created_by", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))

    def validate(self, attrs):
        attrs = super().validate(attrs)

        payment_type = attrs.get("payment_type") or getattr(self.instance, "payment_type", None)
        installments_data = self.initial_data.get("installments", None)

        # На чтение installments идёт отдельным полем; на запись принимаем как list[...]
        if installments_data is not None:
            if payment_type != BuildingTreaty.PaymentType.INSTALLMENT:
                raise serializers.ValidationError({"installments": "График рассрочки можно задавать только при payment_type=installment."})
            if not isinstance(installments_data, list) or not installments_data:
                raise serializers.ValidationError({"installments": "Передайте непустой список платежей рассрочки."})

        apartment = attrs.get("apartment") or getattr(self.instance, "apartment", None)
        rc = attrs.get("residential_complex") or getattr(self.instance, "residential_complex", None)
        if apartment and rc and apartment.residential_complex_id != rc.id:
            raise serializers.ValidationError({"apartment": "Квартира относится к другому ЖК."})

        # не даём менять квартиру у не-черновика (чтобы не ломать историю)
        if self.instance and "apartment" in attrs:
            if getattr(self.instance, "status", None) != BuildingTreaty.Status.DRAFT and attrs["apartment"] != getattr(self.instance, "apartment", None):
                raise serializers.ValidationError({"apartment": "Квартиру можно менять только в статусе draft."})

        return attrs

    def _next_number(self, company_id):
        seq, _ = BuildingTreatyNumberSequence.objects.select_for_update().get_or_create(company_id=company_id)
        v = int(seq.next_value or 1)
        seq.next_value = v + 1
        seq.save(update_fields=["next_value", "updated_at"])
        return f"ДГ-{v:06d}"

    def _parse_installments(self):
        raw = self.initial_data.get("installments", None)
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise serializers.ValidationError({"installments": "Ожидается список платежей."})
        date_f = serializers.DateField()
        dec_f = serializers.DecimalField(max_digits=16, decimal_places=2)
        out = []
        for i, it in enumerate(raw, start=1):
            if not isinstance(it, dict):
                raise serializers.ValidationError({"installments": f"Платёж #{i}: ожидается объект."})
            due_date = it.get("due_date")
            amount = it.get("amount")
            if not due_date or amount in (None, ""):
                raise serializers.ValidationError({"installments": f"Платёж #{i}: нужны due_date и amount."})
            try:
                due_date_v = date_f.to_internal_value(due_date)
            except Exception:
                raise serializers.ValidationError({"installments": f"Платёж #{i}: некорректный due_date."})
            try:
                amount_v = dec_f.to_internal_value(amount)
            except Exception:
                raise serializers.ValidationError({"installments": f"Платёж #{i}: некорректная amount."})
            out.append(
                {
                    "order": int(it.get("order") or i),
                    "due_date": due_date_v,
                    "amount": amount_v,
                }
            )
        return out

    @transaction.atomic
    def create(self, validated_data):
        installments_data = self._parse_installments()

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        rc = validated_data["residential_complex"]
        if not (validated_data.get("number") or "").strip():
            validated_data["number"] = self._next_number(rc.company_id)

        apartment = validated_data.get("apartment")
        if apartment:
            apartment = ResidentialComplexApartment.objects.select_for_update().get(pk=apartment.pk)
            if apartment.residential_complex_id != rc.id:
                raise serializers.ValidationError({"apartment": "Квартира относится к другому ЖК."})
            if apartment.status != ResidentialComplexApartment.Status.AVAILABLE:
                raise serializers.ValidationError({"apartment": "Квартира недоступна для продажи/брони (статус не available)."})
            validated_data["apartment"] = apartment

        payment_type = validated_data.get("payment_type") or BuildingTreaty.PaymentType.FULL
        if payment_type == BuildingTreaty.PaymentType.INSTALLMENT and not installments_data:
            raise serializers.ValidationError({"installments": "Для рассрочки нужно передать график платежей."})

        treaty = BuildingTreaty.objects.create(**validated_data)

        if installments_data:
            total_installments = Decimal("0.00")
            for it in installments_data:
                BuildingTreatyInstallment.objects.create(
                    treaty=treaty,
                    order=it["order"],
                    due_date=it["due_date"],
                    amount=it["amount"],
                )
                total_installments += Decimal(it["amount"] or 0)

            down = Decimal(treaty.down_payment or 0).quantize(Decimal("0.01"))
            amt = Decimal(treaty.amount or 0).quantize(Decimal("0.01"))
            total = (down + total_installments).quantize(Decimal("0.01"))
            if total != amt:
                # допускаем, что на фронте могут не хотеть строгую проверку;
                # но базово контролируем целостность условий оплаты
                raise serializers.ValidationError(
                    {"installments": "Сумма (down_payment + installments) должна быть равна amount."}
                )

        if apartment:
            new_status = (
                ResidentialComplexApartment.Status.SOLD
                if treaty.operation_type == BuildingTreaty.OperationType.SALE
                else ResidentialComplexApartment.Status.RESERVED
            )
            apartment.status = new_status
            apartment.save(update_fields=["status", "updated_at"])

        # created_by обычно проставляет view; но если нет — попробуем проставить из request
        if treaty.created_by_id is None and user and getattr(user, "id", None):
            BuildingTreaty.objects.filter(pk=treaty.pk).update(created_by=user)
            treaty.created_by = user

        return treaty

    @transaction.atomic
    def update(self, instance, validated_data):
        installments_data = self._parse_installments()

        payment_type = validated_data.get("payment_type") or instance.payment_type
        if payment_type == BuildingTreaty.PaymentType.INSTALLMENT and installments_data is None:
            # не передали installments — не трогаем существующие
            pass

        # если переключили на full — очищаем рассрочку
        if "payment_type" in validated_data and payment_type != BuildingTreaty.PaymentType.INSTALLMENT:
            instance.installments.all().delete()

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if installments_data is not None:
            if payment_type != BuildingTreaty.PaymentType.INSTALLMENT:
                raise serializers.ValidationError({"installments": "График рассрочки можно задавать только при payment_type=installment."})
            instance.installments.all().delete()
            total_installments = Decimal("0.00")
            for it in installments_data:
                BuildingTreatyInstallment.objects.create(
                    treaty=instance,
                    order=it["order"],
                    due_date=it["due_date"],
                    amount=it["amount"],
                )
                total_installments += Decimal(it["amount"] or 0)

            down = Decimal(instance.down_payment or 0).quantize(Decimal("0.01"))
            amt = Decimal(instance.amount or 0).quantize(Decimal("0.01"))
            total = (down + total_installments).quantize(Decimal("0.01"))
            if total != amt:
                raise serializers.ValidationError(
                    {"installments": "Сумма (down_payment + installments) должна быть равна amount."}
                )

        return instance


class BuildingTreatyInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingTreatyInstallment
        fields = ["id", "treaty", "order", "due_date", "amount", "status", "paid_amount", "paid_at", "created_at", "updated_at"]
        read_only_fields = ["id", "treaty", "status", "paid_amount", "paid_at", "created_at", "updated_at"]


class BuildingTaskChecklistItemSerializer(serializers.ModelSerializer):
    done_by_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingTaskChecklistItem
        fields = [
            "id",
            "task",
            "text",
            "is_done",
            "order",
            "done_by",
            "done_by_display",
            "done_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "task", "done_by", "done_by_display", "done_at", "created_at", "updated_at"]

    def get_done_by_display(self, obj):
        u = getattr(obj, "done_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))


class BuildingTaskSerializer(serializers.ModelSerializer):
    assignee_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
        help_text="Список пользователей, которым назначена задача",
    )
    assignees = serializers.SerializerMethodField(read_only=True)
    checklist_items = BuildingTaskChecklistItemSerializer(many=True, read_only=True)

    created_by_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingTask
        fields = [
            "id",
            "company",
            "created_by",
            "created_by_display",
            "residential_complex",
            "client",
            "treaty",
            "title",
            "description",
            "status",
            "due_at",
            "completed_at",
            "created_at",
            "updated_at",
            "assignee_ids",
            "assignees",
            "checklist_items",
        ]
        read_only_fields = ["id", "company", "created_by", "created_by_display", "completed_at", "created_at", "updated_at", "assignees", "checklist_items"]

    def get_created_by_display(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))

    def get_assignees(self, obj):
        items = getattr(obj, "assignees", None)
        if items is None:
            return []
        out = []
        for a in items.select_related("user").all():
            u = a.user
            if not u:
                continue
            full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            out.append(
                {
                    "id": str(getattr(u, "id", "")),
                    "display": full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", "")),
                }
            )
        return out

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        company_id = getattr(user, "company_id", None)
        if not company_id and not getattr(user, "is_superuser", False):
            raise serializers.ValidationError({"company": "У пользователя не указана компания."})

        # Проверяем привязанные сущности на компанию (если указаны)
        rc = attrs.get("residential_complex") or getattr(self.instance, "residential_complex", None)
        if rc and company_id and rc.company_id != company_id and not getattr(user, "is_superuser", False):
            raise serializers.ValidationError({"residential_complex": "ЖК принадлежит другой компании."})

        client = attrs.get("client") or getattr(self.instance, "client", None)
        if client and company_id and client.company_id != company_id and not getattr(user, "is_superuser", False):
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

        treaty = attrs.get("treaty") or getattr(self.instance, "treaty", None)
        if treaty and company_id and treaty.residential_complex.company_id != company_id and not getattr(user, "is_superuser", False):
            raise serializers.ValidationError({"treaty": "Договор принадлежит другой компании."})

        return attrs

    def _sync_assignees(self, task: BuildingTask, assignee_ids: list[uuid.UUID] | None, actor):
        if assignee_ids is None:
            return

        ids = [str(x) for x in assignee_ids]
        qs = User.objects.filter(id__in=ids)
        if not getattr(actor, "is_superuser", False):
            company_id = getattr(actor, "company_id", None)
            qs = qs.filter(company_id=company_id)

        valid_ids = set(str(x) for x in qs.values_list("id", flat=True))
        if len(valid_ids) != len(set(ids)):
            raise serializers.ValidationError({"assignee_ids": "Один или несколько пользователей не найдены (или другой компании)."})

        existing = set(str(x) for x in task.assignees.values_list("user_id", flat=True))
        desired = set(ids)

        to_add = desired - existing
        to_del = existing - desired

        if to_del:
            task.assignees.filter(user_id__in=list(to_del)).delete()
        for uid in to_add:
            BuildingTaskAssignee.objects.create(task=task, user_id=uid, added_by=actor)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        actor = getattr(request, "user", None) if request else None

        assignee_ids = validated_data.pop("assignee_ids", None)

        company_id = getattr(actor, "company_id", None)
        if not company_id and not getattr(actor, "is_superuser", False):
            raise serializers.ValidationError({"company": "У пользователя не указана компания."})

        task = BuildingTask.objects.create(
            company_id=company_id,
            created_by=actor,
            **validated_data,
        )

        self._sync_assignees(task, assignee_ids, actor)
        return task

    @transaction.atomic
    def update(self, instance, validated_data):
        request = self.context.get("request")
        actor = getattr(request, "user", None) if request else None

        assignee_ids = validated_data.pop("assignee_ids", None)

        # completed_at ставим автоматически по статусу
        if "status" in validated_data:
            new_status = validated_data.get("status")
            if new_status == BuildingTask.Status.DONE and instance.completed_at is None:
                instance.completed_at = timezone.now()
            if new_status != BuildingTask.Status.DONE:
                instance.completed_at = None

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        self._sync_assignees(instance, assignee_ids, actor)
        return instance


class BuildingTaskChecklistItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingTaskChecklistItem
        fields = ["text", "is_done", "order"]



class BuildingSalaryEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    display = serializers.CharField()
    compensation_id = serializers.UUIDField(allow_null=True)
    salary_type = serializers.CharField(allow_blank=True, allow_null=True)
    base_salary = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    is_active = serializers.BooleanField(allow_null=True)


class BuildingEmployeeCompensationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingEmployeeCompensation
        fields = ["id", "company", "user", "salary_type", "base_salary", "is_active", "notes", "created_at", "updated_at"]
        read_only_fields = ["id", "company", "user", "created_at", "updated_at"]


class BuildingPayrollAdjustmentSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingPayrollAdjustment
        fields = ["id", "line", "type", "title", "amount", "created_by", "created_by_display", "created_at"]
        read_only_fields = ["id", "line", "created_by", "created_by_display", "created_at"]

    def get_created_by_display(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))


class BuildingPayrollPaymentSerializer(serializers.ModelSerializer):
    paid_by_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingPayrollPayment
        fields = [
            "id",
            "line",
            "amount",
            "paid_at",
            "paid_by",
            "paid_by_display",
            "cashbox",
            "shift",
            "cashflow",
            "status",
            "void_reason",
            "voided_by",
            "voided_at",
            "created_at",
        ]
        read_only_fields = ["id", "line", "paid_by", "paid_by_display", "cashflow", "status", "voided_by", "voided_at", "created_at"]

    def get_paid_by_display(self, obj):
        u = getattr(obj, "paid_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))


class BuildingPayrollLineSerializer(serializers.ModelSerializer):
    employee_display = serializers.SerializerMethodField(read_only=True)
    adjustments = BuildingPayrollAdjustmentSerializer(many=True, read_only=True)
    payments = BuildingPayrollPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingPayrollLine
        fields = [
            "id",
            "payroll",
            "employee",
            "employee_display",
            "base_amount",
            "bonus_total",
            "deduction_total",
            "advance_total",
            "net_to_pay",
            "paid_total",
            "comment",
            "created_at",
            "updated_at",
            "adjustments",
            "payments",
        ]
        read_only_fields = [
            "id",
            "bonus_total",
            "deduction_total",
            "advance_total",
            "net_to_pay",
            "paid_total",
            "created_at",
            "updated_at",
            "employee_display",
            "adjustments",
            "payments",
        ]

    def get_employee_display(self, obj):
        u = getattr(obj, "employee", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))


class BuildingPayrollPeriodSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField(read_only=True)
    approved_by_display = serializers.SerializerMethodField(read_only=True)
    totals = serializers.SerializerMethodField(read_only=True)
    lines = BuildingPayrollLineSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingPayrollPeriod
        fields = [
            "id",
            "company",
            "title",
            "period_start",
            "period_end",
            "status",
            "created_by",
            "created_by_display",
            "approved_by",
            "approved_by_display",
            "approved_at",
            "created_at",
            "updated_at",
            "totals",
            "lines",
        ]
        read_only_fields = [
            "id",
            "company",
            "created_by",
            "created_by_display",
            "approved_by",
            "approved_by_display",
            "approved_at",
            "created_at",
            "updated_at",
            "totals",
            "lines",
        ]

    def get_created_by_display(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))

    def get_approved_by_display(self, obj):
        u = getattr(obj, "approved_by", None)
        if not u:
            return None
        full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))

    def get_totals(self, obj):
        z = Decimal("0.00")
        a = obj.lines.aggregate(
            net=models.Sum("net_to_pay"),
            paid=models.Sum("paid_total"),
        )
        net = a.get("net") or z
        paid = a.get("paid") or z
        return {
            "net_to_pay_total": str(net),
            "paid_total": str(paid),
            "remaining_total": str((Decimal(net) - Decimal(paid)).quantize(Decimal("0.01"))),
        }


class BuildingPayrollPeriodApproveSerializer(serializers.Serializer):
    approve = serializers.BooleanField(default=True)


class BuildingPayrollLineCreateSerializer(serializers.Serializer):
    employee = serializers.UUIDField(required=True)
    base_amount = serializers.DecimalField(max_digits=16, decimal_places=2, required=False)
    comment = serializers.CharField(required=False, allow_blank=True)


class BuildingPayrollAdjustmentCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=BuildingPayrollAdjustment.Type.choices)
    title = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)


class BuildingPayrollPaymentCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    cashbox = serializers.UUIDField(required=True)
    shift = serializers.UUIDField(required=False, allow_null=True)
    paid_at = serializers.DateTimeField(required=False)


class BuildingPayrollMyLineSerializer(serializers.ModelSerializer):
    payroll_title = serializers.CharField(source="payroll.title", read_only=True)
    payroll_period_start = serializers.DateField(source="payroll.period_start", read_only=True)
    payroll_period_end = serializers.DateField(source="payroll.period_end", read_only=True)
    payroll_status = serializers.CharField(source="payroll.status", read_only=True)
    payments = BuildingPayrollPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingPayrollLine
        fields = [
            "id",
            "payroll",
            "payroll_title",
            "payroll_period_start",
            "payroll_period_end",
            "payroll_status",
            "base_amount",
            "bonus_total",
            "deduction_total",
            "advance_total",
            "net_to_pay",
            "paid_total",
            "comment",
            "payments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BuildingWorkEntryPhotoSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BuildingWorkEntryPhoto
        fields = ["id", "entry", "image", "image_url", "caption", "created_by", "created_at"]
        read_only_fields = ["id", "created_by", "created_at", "image_url"]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "image", None):
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


class BuildingWorkEntrySerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)
    treaty_number = serializers.CharField(source="treaty.number", read_only=True)
    created_by_display = serializers.SerializerMethodField()
    photos = BuildingWorkEntryPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingWorkEntry
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "client",
            "client_name",
            "treaty",
            "treaty_number",
            "created_by",
            "created_by_display",
            "category",
            "title",
            "description",
            "occurred_at",
            "created_at",
            "updated_at",
            "photos",
        ]
        read_only_fields = ["id", "created_by", "created_by_display", "created_at", "updated_at", "photos"]

    def get_created_by_display(self, obj):
        user = getattr(obj, "created_by", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingWorkEntryPhotoCreateSerializer(serializers.Serializer):
    image = serializers.ImageField(required=True)
    caption = serializers.CharField(required=False, allow_blank=True)


class BuildingPurchaseDocumentItemSerializer(serializers.ModelSerializer):
    qty = serializers.DecimalField(source="quantity", max_digits=16, decimal_places=3)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = BuildingProcurementItem
        fields = ["id", "product", "product_name", "product_article", "name", "unit", "qty", "price", "line_total", "order", "note"]
        read_only_fields = ["id", "line_total"]


class BuildingPurchaseDocumentSerializer(serializers.ModelSerializer):
    doc_type = serializers.CharField(read_only=True, default="PURCHASE")
    date = serializers.DateTimeField(source="created_at", read_only=True)
    total = serializers.DecimalField(source="total_amount", max_digits=16, decimal_places=2, read_only=True)
    number = serializers.CharField(read_only=True, allow_blank=True, default="")
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    items = BuildingPurchaseDocumentItemSerializer(many=True)

    class Meta:
        model = BuildingProcurementRequest
        fields = [
            "id",
            "doc_type",
            "status",
            "number",
            "date",
            "residential_complex",
            "residential_complex_name",
            "comment",
            "total",
            "items",
        ]
        read_only_fields = ["id", "doc_type", "status", "number", "date", "total", "residential_complex_name"]

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        if not items_data:
            raise serializers.ValidationError({"items": "Нельзя создать закупку без позиций."})

        request = self.context.get("request")
        initiator = getattr(request, "user", None) if request else None
        with transaction.atomic():
            procurement = BuildingProcurementRequest.objects.create(initiator=initiator, **validated_data)
            for idx, item in enumerate(items_data, start=1):
                quantity = item.pop("quantity")
                product = item.get("product")
                if product and product.company_id != procurement.residential_complex.company_id:
                    raise serializers.ValidationError({"items": "Один из товаров принадлежит другой компании."})
                if product:
                    item.setdefault("name", product.name)
                    item.setdefault("unit", product.unit)
                BuildingProcurementItem.objects.create(
                    procurement=procurement,
                    quantity=quantity,
                    order=item.get("order", idx),
                    **item,
                )
            procurement.recalculate_totals()
        return procurement

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        if instance.status != BuildingProcurementRequest.Status.DRAFT:
            raise serializers.ValidationError({"status": "Можно изменять только закупку в статусе draft."})

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if items_data is not None:
                if not items_data:
                    raise serializers.ValidationError({"items": "Нельзя оставить закупку без позиций."})
                instance.items.all().delete()
                for idx, item in enumerate(items_data, start=1):
                    quantity = item.pop("quantity")
                    product = item.get("product")
                    if product and product.company_id != instance.residential_complex.company_id:
                        raise serializers.ValidationError({"items": "Один из товаров принадлежит другой компании."})
                    if product:
                        item.setdefault("name", product.name)
                        item.setdefault("unit", product.unit)
                    BuildingProcurementItem.objects.create(
                        procurement=instance,
                        quantity=quantity,
                        order=item.get("order", idx),
                        **item,
                    )
                instance.recalculate_totals()
        return instance
