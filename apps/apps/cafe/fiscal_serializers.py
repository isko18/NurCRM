# apps/cafe/fiscal_serializers.py
from rest_framework import serializers

from .fiscal_models import CafeFiscalSettings, CafeFiscalShift, CafeFiscalReceipt


class CafeFiscalSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CafeFiscalSettings
        fields = [
            "enabled",
            "connector_base_url",
            "registration_number",
            "pin",
            "login",
            "password",
            "tin",
            "full_name",
            "cashier_name",
            "fiscal_memory_number",
            "location_address",
            "tax_system_codes",
            "calc_item_attr_codes",
            "entrepreneurship_object_code",
            "business_activity_code",
            "tax_authority_department_code",
            "default_vat_code",
            "default_st_code",
            "default_calc_item_attr_code",
            "default_measure",
            "receipt_width",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_registration_number(self, value):
        value = (value or "").strip()
        if value and len(value) > 16:
            raise serializers.ValidationError("РНМ кассы — не более 16 символов.")
        return value

    def validate_pin(self, value):
        return (value or "").strip()


class CafeFiscalShiftSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = CafeFiscalShift
        fields = [
            "id",
            "branch",
            "status",
            "status_display",
            "registration_number",
            "opened_at",
            "closed_at",
            "open_shift_datetime",
            "fm_expiration_date",
            "opened_by",
            "closed_by",
            "raw_open",
            "raw_close",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CafeFiscalShiftOpenSerializer(serializers.Serializer):
    """Тело при фиксации открытия смены (метаданные из ответа коннектора)."""
    registration_number = serializers.CharField(required=False, allow_blank=True)
    open_shift_datetime = serializers.DateTimeField(required=False, allow_null=True)
    fm_expiration_date = serializers.DateTimeField(required=False, allow_null=True)
    raw = serializers.JSONField(required=False)


class CafeFiscalShiftCloseSerializer(serializers.Serializer):
    raw = serializers.JSONField(required=False)


class CafeFiscalReceiptSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = CafeFiscalReceipt
        fields = [
            "id",
            "order",
            "shift",
            "branch",
            "kind",
            "kind_display",
            "operation_type",
            "fd_number",
            "fn_serial_number",
            "total_sum",
            "total_cash_sum",
            "total_cashless_sum",
            "pay_sum",
            "delivery_sum",
            "request_payload",
            "response_payload",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]


class CafeFiscalReceiptRecordSerializer(serializers.Serializer):
    """
    Запись результата фискализации чека продажи/возврата после ответа коннектора.
    """
    kind = serializers.ChoiceField(
        choices=[CafeFiscalReceipt.Kind.SALE, CafeFiscalReceipt.Kind.RETURN],
        default=CafeFiscalReceipt.Kind.SALE,
    )
    operation_type = serializers.CharField(required=False, allow_blank=True, default="INCOME")
    fd_number = serializers.IntegerField(required=False, allow_null=True)
    fn_serial_number = serializers.CharField(required=False, allow_blank=True, default="")
    request_payload = serializers.JSONField(required=False)
    response_payload = serializers.JSONField(required=False)


class CafeFiscalCashSerializer(serializers.Serializer):
    """Запись внесения/изъятия наличных (после операции на коннекторе)."""
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    fd_number = serializers.IntegerField(required=False, allow_null=True)
    fn_serial_number = serializers.CharField(required=False, allow_blank=True, default="")
    response_payload = serializers.JSONField(required=False)
