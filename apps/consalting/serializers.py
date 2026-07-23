from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    ServicesConsalting,
    TariffConsalting,
    SaleConsalting,
    SaleItemConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
    LeadConsalting,
    LossReasonConsalting,
    LeadActivityConsalting,
    LeadTaskConsalting,
    WhatsAppMessageConsalting,
    ServiceSalaryRateConsalting,
    SalaryAccrualConsalting,
    SalaryPayoutConsalting,
    InboundLeadConsalting,
    LeadDistributionSettingsConsalting,
    ServiceRolePriceConsalting,
    TariffRolePriceConsalting,
)
from apps.users.models import User, Branch, CustomRole


# ==========================
# Общий миксин: company/branch (branch авто из пользователя / ?branch)
# ==========================
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.

    Порядок получения branch:
      0) ?branch=<uuid> в query-параметрах, если филиал принадлежит компании пользователя
      1) user.primary_branch (свойство или метод, если есть и принадлежит компании)
      2) request.branch (если вы кладёте в middleware и он принадлежит компании)
      3) None (глобальная запись компании)
    """

    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # ---- helpers ----
    def _request(self):
        return self.context.get("request")

    def _user(self):
        request = self._request()
        return getattr(request, "user", None) if request else None

    def _user_company(self):
        user = self._user()
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        # поддержим как employee, так и owner
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    # ---- какой филиал реально использовать ----
    def _auto_branch(self):
        request = self._request()
        if not request:
            return None
        user = self._user()
        company = self._user_company()
        company_id = getattr(company, "id", None)
        if not company_id:
            return None

        # 0) branch из query-параметров (?branch=<uuid>)
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/кривой id — игнорируем и идём дальше
                pass

        # 1) primary_branch может быть полем или методом
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == company_id:
            return primary

        # 2) из middleware (на будущее)
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 3) глобально
        return None

    def create(self, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
            # не перетираем существующий branch, если авто-branch не определён
            auto_branch = self._auto_branch()
            if auto_branch is not None:
                validated_data["branch"] = auto_branch
        return super().update(instance, validated_data)


# ==========================
# Тариф и цены по ролям
# ==========================
class ServiceRolePriceConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRolePriceConsalting
        fields = ("custom_role", "price")


class TariffRolePriceConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TariffRolePriceConsalting
        fields = ("custom_role", "price")


class TariffConsaltingSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(required=False)
    role_prices = TariffRolePriceConsaltingSerializer(many=True, required=False)

    class Meta:
        model = TariffConsalting
        fields = ("id", "name", "price", "subscription_amount", "subscription_period", "role_prices")


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    tariffs = TariffConsaltingSerializer(many=True, required=False)
    role_prices = ServiceRolePriceConsaltingSerializer(many=True, required=False)
    custom_role = serializers.PrimaryKeyRelatedField(
        queryset=CustomRole.objects.all(), required=False, allow_null=True
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ServicesConsalting
        fields = (
            "id", "company", "branch", "name", "price",
            "description", "custom_role", "role_prices", "tariffs", "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")

    def validate_custom_role(self, value):
        company = self._user_company()
        if value and company and value.company_id not in (None, company.id):
            raise serializers.ValidationError("Роль принадлежит другой компании.")
        return value

    def validate(self, attrs):
        return attrs

    def _sync_service_role_prices(self, service, role_prices_data):
        if role_prices_data is None:
            return
        service.role_prices.all().delete()
        new_objs = []
        for item in role_prices_data:
            role = item.get("custom_role")
            price = item.get("price")
            if role and price is not None:
                role_obj = role if isinstance(role, CustomRole) else CustomRole.objects.filter(pk=role).first()
                if role_obj:
                    new_objs.append(ServiceRolePriceConsalting(service=service, custom_role=role_obj, price=price))
        if new_objs:
            ServiceRolePriceConsalting.objects.bulk_create(new_objs)

    def _sync_tariffs(self, service, tariffs_data):
        if tariffs_data is None:
            return
        service.tariffs.all().delete()
        for t in tariffs_data:
            tariff = TariffConsalting.objects.create(
                company_id=service.company_id,
                branch_id=service.branch_id,
                service=service,
                name=t["name"],
                price=t["price"],
                subscription_amount=t.get("subscription_amount") or 0,
                subscription_period=t.get("subscription_period") or "",
            )
            rp_data = t.get("role_prices")
            if rp_data:
                rp_objs = []
                for item in rp_data:
                    role = item.get("custom_role")
                    price = item.get("price")
                    if role and price is not None:
                        role_obj = role if isinstance(role, CustomRole) else CustomRole.objects.filter(pk=role).first()
                        if role_obj:
                            rp_objs.append(TariffRolePriceConsalting(tariff=tariff, custom_role=role_obj, price=price))
                if rp_objs:
                    TariffRolePriceConsalting.objects.bulk_create(rp_objs)

    def create(self, validated_data):
        tariffs_data = validated_data.pop("tariffs", None)
        role_prices_data = validated_data.pop("role_prices", None)
        service = super().create(validated_data)
        self._sync_service_role_prices(service, role_prices_data)
        self._sync_tariffs(service, tariffs_data)
        return service

    def update(self, instance, validated_data):
        tariffs_data = validated_data.pop("tariffs", None)
        role_prices_data = validated_data.pop("role_prices", None)
        service = super().update(instance, validated_data)
        self._sync_service_role_prices(service, role_prices_data)
        self._sync_tariffs(service, tariffs_data)
        return service


# ==========================
# Доп. товар продажи (вложенный)
# ==========================
class SaleItemConsaltingSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(required=False)

    class Meta:
        model = SaleItemConsalting
        fields = ("id", "name", "price")


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    # пользователь — текущий автор/оператор
    user = serializers.ReadOnlyField(source="user.id")
    user_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    service_display = serializers.CharField(source="services.name", read_only=True)
    service_price = serializers.DecimalField(source="services.price", max_digits=12, decimal_places=2, read_only=True)
    tariff = serializers.PrimaryKeyRelatedField(
        queryset=TariffConsalting.objects.all(), required=False, allow_null=True
    )
    tariff_display = serializers.CharField(source="tariff.name", read_only=True)
    tariff_price = serializers.DecimalField(
        source="tariff.price", max_digits=12, decimal_places=2, read_only=True
    )
    items = SaleItemConsaltingSerializer(many=True, required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SaleConsalting
        fields = (
            "id", "company", "branch",
            "user", "user_display",
            "services", "service_display", "service_price",
            "tariff", "tariff_display", "tariff_price",
            "client", "client_display",
            "items", "discount", "markup", "total",
            "description",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "company", "branch", "user", "user_display",
            "service_display", "service_price",
            "tariff_display", "tariff_price", "total",
            "created_at", "updated_at",
        )

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None

    def get_client_display(self, obj):
        if not obj.client:
            return None
        # стараемся красиво: full_name/название/телефон
        return (
            getattr(obj.client, "full_name", None)
            or getattr(obj.client, "name", None)
            or getattr(obj.client, "phone", None)
        )

    def validate_services(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Услуга принадлежит другой компании.")
        # ветка: глобальная или текущая
        target_branch = self._auto_branch()
        if target_branch is not None and value and value.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Услуга принадлежит другому филиалу.")
        return value

    def validate_tariff(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Тариф принадлежит другой компании.")
        return value

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()

        services = attrs.get("services") if "services" in attrs else getattr(self.instance, "services", None)
        tariff = attrs.get("tariff") if "tariff" in attrs else getattr(self.instance, "tariff", None)
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company:
            if client and getattr(client, "company_id", None) != company.id:
                raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

        if target_branch is not None:
            if client and getattr(client, "branch_id", None) not in (None, target_branch.id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # тариф должен относиться к выбранной услуге
        if tariff and services and tariff.service_id != services.id:
            raise serializers.ValidationError({"tariff": "Тариф относится к другой услуге."})

        # user заполним, если хотим фиксировать текущего оператора автоматически
        request = self.context.get("request")
        if request and getattr(request, "user", None):
            attrs.setdefault("user", request.user)

        # прогон через model.clean() на всякий случай (items — не поле модели)
        try:
            clean_attrs = {k: v for k, v in attrs.items() if k != "items"}
            temp = SaleConsalting(**{**clean_attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(
                getattr(
                    e,
                    "message_dict",
                    {"detail": e.messages if hasattr(e, "messages") else str(e)},
                )
            )

        return attrs

    def _sync_items(self, sale, items_data):
        """Полная замена доп. товаров продажи переданным списком."""
        if items_data is None:
            return
        sale.items.all().delete()
        SaleItemConsalting.objects.bulk_create([
            SaleItemConsalting(sale=sale, name=i["name"], price=i["price"])
            for i in items_data
        ])

    def create(self, validated_data):
        items_data = validated_data.pop("items", None)
        sale = super().create(validated_data)
        self._sync_items(sale, items_data)
        sale.recalc_total(save=True)
        return sale

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        sale = super().update(instance, validated_data)
        self._sync_items(sale, items_data)
        sale.recalc_total(save=True)
        return sale


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=True)
    user_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SalaryConsalting
        fields = (
            "id", "company", "branch",
            "user", "user_display",
            "amount", "percent", "description",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "user_display", "created_at", "updated_at")

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None

    def validate_user(self, value):
        company = self._user_company()
        if value and company and getattr(value, "company_id", None) != company.id:
            raise serializers.ValidationError("Сотрудник принадлежит другой компании.")
        return value


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    client_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = (
            "id", "company", "branch",
            "client", "client_display",
            "status", "name", "description",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")

    def get_client_display(self, obj):
        if not obj.client:
            return None
        return (
            getattr(obj.client, "full_name", None)
            or getattr(obj.client, "name", None)
            or getattr(obj.client, "phone", None)
        )

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company and client and getattr(client, "company_id", None) != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        if target_branch is not None and client and getattr(client, "branch_id", None) not in (None, target_branch.id):
            raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # прогон через model.clean() для комплексных проверок
        try:
            temp = RequestsConsalting(**{**attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(
                getattr(
                    e,
                    "message_dict",
                    {"detail": e.messages if hasattr(e, "messages") else str(e)},
                )
            )
        return attrs


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    employee_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BookingConsalting
        fields = (
            "id", "company", "branch",
            "title", "date", "time",
            "employee", "employee_display",
            "note",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at", "employee_display")

    def get_employee_display(self, obj):
        if obj.employee and (obj.employee.first_name or obj.employee.last_name):
            return f"{obj.employee.first_name or ''} {obj.employee.last_name or ''}".strip()
        return getattr(obj.employee, "email", None) if obj.employee else None

    def validate_employee(self, value):
        company = self._user_company()
        if value and company and getattr(value, "company_id", None) != company.id:
            raise serializers.ValidationError("Сотрудник принадлежит другой компании.")
        return value

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()

        # прогон через model.clean() (проверит company/branch/employee и уникальный слот)
        try:
            temp = BookingConsalting(**{**attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(
                getattr(
                    e,
                    "message_dict",
                    {"detail": e.messages if hasattr(e, "messages") else str(e)},
                )
            )
        return attrs


# ==========================
# FunnelStageConsalting (вложенное чтение)
# ==========================
class FunnelStageConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    funnel = serializers.PrimaryKeyRelatedField(queryset=FunnelConsalting.objects.all())
    leads_count = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FunnelStageConsalting
        fields = (
            "id", "company", "branch", "funnel",
            "name", "order", "color", "stage_type",
            "is_system", "system_key",
            "allowed_next", "required_fields", "sla_hours", "allow_skip",
            "is_final", "is_success",
            "leads_count", "created_at", "updated_at",
        )
        # is_final/is_success выводятся из stage_type в model.save() → только чтение
        read_only_fields = (
            "id", "company", "branch", "is_final", "is_success",
            "leads_count", "created_at", "updated_at",
        )

    def get_leads_count(self, obj):
        # если queryset аннотирован — используем его, иначе считаем
        return getattr(obj, "leads_count", None) if hasattr(obj, "leads_count") else obj.leads.count()

    def validate_funnel(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Воронка принадлежит другой компании.")
        return value

    def create(self, validated_data):
        # company/branch берём из воронки, чтобы стадии всегда совпадали с воронкой
        funnel = validated_data.get("funnel")
        if funnel is not None:
            validated_data["company"] = funnel.company
            validated_data["branch"] = funnel.branch
        return serializers.ModelSerializer.create(self, validated_data)

    def update(self, instance, validated_data):
        funnel = validated_data.get("funnel") or instance.funnel
        if funnel is not None:
            validated_data["company"] = funnel.company
            validated_data["branch"] = funnel.branch
        return serializers.ModelSerializer.update(self, instance, validated_data)


# ==========================
# FunnelConsalting
# ==========================
class FunnelConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    stages = FunnelStageConsaltingSerializer(many=True, read_only=True)
    leads_count = serializers.SerializerMethodField()
    custom_role = serializers.PrimaryKeyRelatedField(
        queryset=CustomRole.objects.all(), required=False, allow_null=True
    )
    custom_role_name = serializers.CharField(source="custom_role.name", read_only=True)
    is_protected = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FunnelConsalting
        fields = (
            "id", "company", "branch",
            "name", "description", "is_active",
            "funnel_kind", "is_main", "is_static", "is_protected",
            "custom_role", "custom_role_name",
            "stages", "leads_count",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "company", "branch", "stages", "leads_count",
            "funnel_kind", "is_main", "is_static", "is_protected", "custom_role_name",
            "created_at", "updated_at",
        )

    def get_leads_count(self, obj):
        return getattr(obj, "leads_count", None) if hasattr(obj, "leads_count") else obj.leads.count()

    def validate_custom_role(self, value):
        company = self._user_company()
        if value and company and value.company_id not in (None, company.id):
            raise serializers.ValidationError("Роль принадлежит другой компании.")
        return value

    def create(self, validated_data):
        # Деривация типа воронки: роль → ROLE+static, иначе CUSTOM (is_main только через provisioning)
        role = validated_data.get("custom_role")
        if role is not None:
            validated_data["funnel_kind"] = FunnelConsalting.FunnelKind.ROLE
            validated_data["is_static"] = True
        else:
            validated_data["funnel_kind"] = FunnelConsalting.FunnelKind.CUSTOM
            validated_data["is_static"] = False
        validated_data["is_main"] = False
        return super().create(validated_data)


# ==========================
# Bulk-переупорядочивание стадий
# ==========================
class FunnelStageReorderItemSerializer(serializers.Serializer):
    """Один элемент запроса reorder: { "id": <uuid>, "order": <int> }."""
    id = serializers.UUIDField()
    order = serializers.IntegerField(min_value=0)


# ==========================
# Пользовательские предпочтения по воронкам
# ==========================
class FunnelUserPreferenceConsaltingSerializer(serializers.Serializer):
    """Порядок воронок-строк на странице (per-user)."""
    funnel_order = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True
    )

    def validate_funnel_order(self, value):
        # нормализуем в строки и убираем дубликаты, сохраняя порядок
        seen = set()
        result = []
        for item in value:
            key = str(item)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result


# ==========================
# LeadConsalting (карточка лида)
# ==========================
class LeadConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    funnel = serializers.PrimaryKeyRelatedField(queryset=FunnelConsalting.objects.all())
    stage = serializers.PrimaryKeyRelatedField(
        queryset=FunnelStageConsalting.objects.all(), required=False, allow_null=True
    )
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )

    loss_reason = serializers.PrimaryKeyRelatedField(
        queryset=LossReasonConsalting.objects.all(), required=False, allow_null=True
    )
    source_lead = serializers.PrimaryKeyRelatedField(
        queryset=LeadConsalting.objects.all(), required=False, allow_null=True
    )
    service = serializers.PrimaryKeyRelatedField(
        queryset=ServicesConsalting.objects.all(), required=False, allow_null=True
    )
    tariff = serializers.PrimaryKeyRelatedField(
        queryset=TariffConsalting.objects.all(), required=False, allow_null=True
    )
    participant_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False, write_only=True, source="participants"
    )
    participants = serializers.SerializerMethodField(read_only=True)

    funnel_name = serializers.CharField(source="funnel.name", read_only=True)
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    stage_color = serializers.CharField(source="stage.color", read_only=True)
    stage_type = serializers.CharField(source="stage.stage_type", read_only=True)
    owner_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    loss_reason_label = serializers.CharField(source="loss_reason.label", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = LeadConsalting
        fields = (
            "id", "company", "branch",
            "funnel", "funnel_name",
            "stage", "stage_name", "stage_color", "stage_type",
            "client", "client_display",
            "owner", "owner_display",
            "title", "description",
            "full_name", "phone", "email",
            "source", "estimated_value", "probability", "status",
            # скоринг
            "score_grade", "score_value", "score_updated_at",
            "budget_confirmed", "urgency", "decision_maker_engaged", "avg_response_minutes",
            # следующее действие
            "next_action_type", "next_action_date", "next_action_note",
            # риск / тайминги
            "is_at_risk", "risk_reason", "last_activity_at", "stage_entered_at",
            # проигрыш
            "loss_reason", "loss_reason_label", "loss_comment",
            # lifecycle
            "first_contact_at", "won_at", "lost_at", "completed_at",
            "closed_at", "created_at", "updated_at",
            # передача между воронками
            "source_lead",
            # услуга/тариф/участники
            "service", "tariff", "participants", "participant_ids",
            # архив / оплата
            "is_archived", "archived_at", "payment_registered", "payment_mode",
        )
        read_only_fields = (
            "id", "company", "branch",
            "funnel_name", "stage_name", "stage_color", "stage_type",
            "owner_display", "client_display", "loss_reason_label",
            "score_grade", "score_value", "score_updated_at",
            "is_at_risk", "risk_reason", "last_activity_at", "stage_entered_at",
            "won_at", "lost_at", "completed_at", "first_contact_at",
            "created_at", "updated_at",
            "participants", "is_archived", "archived_at",
            "payment_registered", "payment_mode",
        )

    def get_participants(self, obj):
        result = []
        for u in obj.participants.all():
            name = f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email
            result.append({"id": str(u.id), "display": name})
        return result

    def get_owner_display(self, obj):
        if obj.owner and (obj.owner.first_name or obj.owner.last_name):
            return f"{obj.owner.first_name or ''} {obj.owner.last_name or ''}".strip()
        return getattr(obj.owner, "email", None) if obj.owner else None

    def get_client_display(self, obj):
        if not obj.client:
            return None
        return (
            getattr(obj.client, "full_name", None)
            or getattr(obj.client, "name", None)
            or getattr(obj.client, "phone", None)
        )

    def validate_funnel(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Воронка принадлежит другой компании.")
        return value

    def validate_owner(self, value):
        company = self._user_company()
        if value and company and getattr(value, "company_id", None) not in (None, company.id):
            raise serializers.ValidationError("Ответственный из другой компании.")
        return value

    def validate_loss_reason(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Причина проигрыша из другой компании.")
        return value

    def create(self, validated_data):
        # фиксируем момент входа в стартовую стадию для аналитики времени
        from django.utils import timezone
        validated_data.setdefault("stage_entered_at", timezone.now())
        return super().create(validated_data)

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()

        funnel = attrs.get("funnel") or getattr(self.instance, "funnel", None)
        stage = attrs.get("stage") if "stage" in attrs else getattr(self.instance, "stage", None)
        client = attrs.get("client") if "client" in attrs else getattr(self.instance, "client", None)

        if stage and funnel and stage.funnel_id != funnel.id:
            raise serializers.ValidationError({"stage": "Стадия относится к другой воронке."})

        if company and client and getattr(client, "company_id", None) != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        if target_branch is not None and client and getattr(client, "branch_id", None) not in (None, target_branch.id):
            raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # Новый лид по умолчанию попадает в общий пул (owner=None) и виден всем
        # сотрудникам, пока кто-то не «возьмёт» его (claim) или руководитель не
        # назначит ответственного. Поэтому owner здесь НЕ проставляем автоматически.

        try:
            # participants — M2M, нельзя в конструктор модели
            scalar_attrs = {k: v for k, v in attrs.items() if k != "participants"}
            temp = LeadConsalting(**{**scalar_attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(
                getattr(
                    e,
                    "message_dict",
                    {"detail": e.messages if hasattr(e, "messages") else str(e)},
                )
            )
        return attrs


# ==========================
# Перемещение лида по стадиям
# ==========================
class LeadMoveStageSerializer(serializers.Serializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=FunnelStageConsalting.objects.all())


# ==========================
# Назначение ответственного (руководителем)
# ==========================
class LeadAssignSerializer(serializers.Serializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())


# ==========================
# Закрытие лида (win / lose)
# ==========================
class LeadLoseSerializer(serializers.Serializer):
    loss_reason = serializers.PrimaryKeyRelatedField(queryset=LossReasonConsalting.objects.all())
    loss_comment = serializers.CharField(required=False, allow_blank=True)
    stage = serializers.PrimaryKeyRelatedField(
        queryset=FunnelStageConsalting.objects.all(), required=False, allow_null=True,
        help_text="Финальная LOST-стадия воронки. Если не указать — берётся первая LOST-стадия."
    )


class LeadWinSerializer(serializers.Serializer):
    stage = serializers.PrimaryKeyRelatedField(
        queryset=FunnelStageConsalting.objects.all(), required=False, allow_null=True,
        help_text="Финальная WON-стадия. Если не указать — берётся первая WON-стадия воронки."
    )


# ==========================
# LossReasonConsalting (справочник)
# ==========================
class LossReasonConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    branch = None  # у справочника нет филиала

    class Meta:
        model = LossReasonConsalting
        fields = ("id", "company", "code", "label", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "company", "created_at", "updated_at")

    # переопределяем mixin: модель без branch
    company = serializers.ReadOnlyField(source="company.id")

    def create(self, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
        return serializers.ModelSerializer.create(self, validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("company", None)
        return serializers.ModelSerializer.update(self, instance, validated_data)


# ==========================
# LeadActivityConsalting (timeline, append-only)
# ==========================
class LeadActivityConsaltingSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = LeadActivityConsalting
        fields = (
            "id", "lead", "actor", "actor_display", "type",
            "title", "body", "payload", "file", "created_at",
        )
        read_only_fields = ("id", "actor", "actor_display", "created_at")

    def get_actor_display(self, obj):
        if obj.actor and (obj.actor.first_name or obj.actor.last_name):
            return f"{obj.actor.first_name or ''} {obj.actor.last_name or ''}".strip()
        return getattr(obj.actor, "email", None) if obj.actor else "Система"

    def validate_type(self, value):
        # через API можно создавать только «контактные» активности, не системные
        manual = {
            LeadActivityConsalting.Type.NOTE,
            LeadActivityConsalting.Type.CALL,
            LeadActivityConsalting.Type.MESSAGE,
            LeadActivityConsalting.Type.EMAIL,
            LeadActivityConsalting.Type.MEETING,
            LeadActivityConsalting.Type.FILE,
        }
        if value not in manual:
            raise serializers.ValidationError("Этот тип активности создаётся только системой.")
        return value


# ==========================
# LeadTaskConsalting (follow-up)
# ==========================
class LeadTaskConsaltingSerializer(serializers.ModelSerializer):
    assignee_display = serializers.SerializerMethodField()

    class Meta:
        model = LeadTaskConsalting
        fields = (
            "id", "lead", "assignee", "assignee_display", "type", "title",
            "due_date", "status", "created_by", "created_by_automation",
            "completed_at", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "assignee_display", "created_by", "created_by_automation",
            "completed_at", "created_at", "updated_at",
        )

    def get_assignee_display(self, obj):
        if obj.assignee and (obj.assignee.first_name or obj.assignee.last_name):
            return f"{obj.assignee.first_name or ''} {obj.assignee.last_name or ''}".strip()
        return getattr(obj.assignee, "email", None) if obj.assignee else None


# ==========================
# WhatsAppMessageConsalting
# ==========================
class WhatsAppMessageConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppMessageConsalting
        fields = (
            "id", "company", "branch", "lead", "message_id",
            "direction", "text", "status", "created_at", "updated_at"
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")


class WhatsAppSendSerializer(serializers.Serializer):
    text = serializers.CharField(required=True, help_text="Текст сообщения для отправки")


# ==========================
# Salary Auto-Accrual System
# ==========================
class ServiceSalaryRateConsaltingSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    price = serializers.DecimalField(source="service.price", max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ServiceSalaryRateConsalting
        fields = ("id", "company", "service", "service_name", "price", "percent", "updated_at")
        read_only_fields = ("id", "company", "service_name", "price", "updated_at")


class SalaryAccrualConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = SalaryAccrualConsalting
        fields = (
            "id", "company", "user", "user_display", "service", "service_name",
            "sale", "lead", "base_amount", "percent", "amount", "status", "payout", "created_at"
        )
        read_only_fields = (
            "id", "company", "user_display", "service_name", "created_at"
        )

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None


class SalaryPayoutConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = SalaryPayoutConsalting
        fields = ("id", "company", "user", "user_display", "amount", "comment", "created_at")
        read_only_fields = ("id", "company", "user_display", "created_at")

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None


# ==========================
# Inbound Leads & Lead Distribution
# ==========================
class InboundLeadConsaltingSerializer(serializers.ModelSerializer):
    owner_display = serializers.SerializerMethodField()

    class Meta:
        model = InboundLeadConsalting
        fields = (
            "id", "company", "full_name", "phone", "source", "external_id",
            "message", "owner", "owner_display", "status", "lead", "created_at"
        )
        read_only_fields = ("id", "company", "owner_display", "created_at")

    def get_owner_display(self, obj):
        if obj.owner and (obj.owner.first_name or obj.owner.last_name):
            return f"{obj.owner.first_name or ''} {obj.owner.last_name or ''}".strip()
        return getattr(obj.owner, "email", None) if obj.owner else None


class LeadDistributionSettingsConsaltingSerializer(serializers.ModelSerializer):
    role_ids = serializers.PrimaryKeyRelatedField(
        source="roles", many=True, queryset=CustomRole.objects.all(), required=False
    )
    recipients = serializers.SerializerMethodField()

    class Meta:
        model = LeadDistributionSettingsConsalting
        fields = ("enabled", "strategy", "role_ids", "recipients")

    def get_recipients(self, obj):
        from apps.users.models import User
        role_ids = list(obj.roles.values_list("id", flat=True))
        if not role_ids:
            return []
        users = User.objects.filter(company=obj.company, is_active=True, custom_role_id__in=role_ids)
        return [
            {
                "id": str(u.id),
                "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email
            }
            for u in users
        ]



