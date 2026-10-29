from django.db import IntegrityError
from decimal import Decimal
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
    SalarySchemeConsalting,
    SalarySchemeServiceOverrideConsalting,
    SalaryDefaultsConsalting,
    BonusRuleConsalting,
    BonusTierConsalting,
    SalaryAdjustmentConsalting,
    InboundLeadConsalting,
    LeadDistributionSettingsConsalting,
    ServiceRolePriceConsalting,
    TariffRolePriceConsalting,
    LeadFunnelHistoryConsalting,
    SubscriptionConsalting,
    SubscriptionPaymentConsalting,
    SalesPlanConsalting,
    KpiWeightsConsalting,
    CashOperationConsalting,
    CashRequestConsalting,
    CashConfirmationSettingsConsalting,
    SaleRefundConsalting,
    RegionalFunnelRoutingConsalting,
    RegionalFunnelRuleConsalting,
    LeadAdSpend,
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

    def validate_price(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Цена не может быть отрицательной.")
        return value


class TariffRolePriceConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TariffRolePriceConsalting
        fields = ("custom_role", "price")

    def validate_price(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Цена не может быть отрицательной.")
        return value


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
        name = attrs.get("name")
        company = self._user_company()
        if name and company:
            branch = self._auto_branch()
            qs = ServicesConsalting.objects.filter(company=company, name__iexact=name.strip())
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if branch:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)
            if qs.exists():
                raise serializers.ValidationError({
                    "name": ["Услуга с таким названием уже существует."]
                })

        role_prices = attrs.get("role_prices")
        if role_prices is not None:
            seen_roles = set()
            for rp in role_prices:
                price = rp.get("price")
                if price is not None and price < 0:
                    raise serializers.ValidationError({"role_prices": "Цена не может быть отрицательной."})
                role = rp.get("custom_role")
                role_id = role.id if isinstance(role, CustomRole) else role
                if role_id in seen_roles:
                    raise serializers.ValidationError({"role_prices": "Дублирование цены для одной роли запрещено."})
                if role_id:
                    seen_roles.add(role_id)
                role_obj = role if isinstance(role, CustomRole) else (CustomRole.objects.filter(pk=role_id).first() if role_id else None)
                if role_obj and company and role_obj.company_id not in (None, company.id):
                    raise serializers.ValidationError({"role_prices": "Роль принадлежит другой компании."})

        tariffs = attrs.get("tariffs")
        if tariffs is not None:
            for t in tariffs:
                t_role_prices = t.get("role_prices")
                if t_role_prices:
                    t_seen_roles = set()
                    for rp in t_role_prices:
                        price = rp.get("price")
                        if price is not None and price < 0:
                            raise serializers.ValidationError({"tariffs": "Цена в тарифе не может быть отрицательной."})
                        role = rp.get("custom_role")
                        role_id = role.id if isinstance(role, CustomRole) else role
                        if role_id in t_seen_roles:
                            raise serializers.ValidationError({"tariffs": "Дублирование цены для одной роли в тарифе запрещено."})
                        if role_id:
                            t_seen_roles.add(role_id)
                        role_obj = role if isinstance(role, CustomRole) else (CustomRole.objects.filter(pk=role_id).first() if role_id else None)
                        if role_obj and company and role_obj.company_id not in (None, company.id):
                            raise serializers.ValidationError({"tariffs": "Роль в тарифе принадлежит другой компании."})

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
        try:
            service = super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError({
                "name": ["Услуга с таким названием уже существует."]
            })
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
            "status", "canceled_at", "canceled_by", "cancel_reason", "cancel_comment", "refunded_amount",
            "description", "paid_months",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "company", "branch", "user", "user_display",
            "service_display", "service_price",
            "tariff_display", "tariff_price", "total",
            "status", "canceled_at", "canceled_by", "cancel_reason", "cancel_comment", "refunded_amount",
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

        if not self.instance:
            if not client:
                raise serializers.ValidationError({"client": "Клиент обязателен при создании продажи."})
            items = attrs.get("items") or []
            if not services and not tariff and not items:
                raise serializers.ValidationError({"detail": "Необходимо указать услугу, тариф или список позиций."})

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
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True
    )
    assigned_to_display = serializers.SerializerMethodField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = (
            "id", "company", "branch",
            "client", "client_display",
            "assigned_to", "assigned_to_display",
            "acceptance", "decline_reason",
            "status", "name", "description",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at", "client_display", "assigned_to_display", "acceptance", "decline_reason")

    def create(self, validated_data):
        if "assigned_to" in validated_data:
            if validated_data["assigned_to"]:
                validated_data["acceptance"] = RequestsConsalting.Acceptance.PENDING
                validated_data["decline_reason"] = ""
            else:
                validated_data["acceptance"] = None
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "assigned_to" in validated_data:
            if validated_data["assigned_to"]:
                validated_data["acceptance"] = RequestsConsalting.Acceptance.PENDING
                validated_data["decline_reason"] = ""
            else:
                validated_data["acceptance"] = None
        return super().update(instance, validated_data)

    def get_client_display(self, obj):
        if not obj.client:
            return None
        return (
            getattr(obj.client, "full_name", None)
            or getattr(obj.client, "name", None)
            or getattr(obj.client, "phone", None)
        )

    def get_assigned_to_display(self, obj):
        user = getattr(obj, "assigned_to", None)
        if not user:
            return None
        full = f"{user.first_name or ''} {user.last_name or ''}".strip()
        return full or getattr(user, "email", None) or str(user)

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company and client and getattr(client, "company_id", None) != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        if target_branch is not None and client and getattr(client, "branch_id", None) not in (None, target_branch.id):
            raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        if "assigned_to" in attrs:
            assigned_to = attrs["assigned_to"]
            if assigned_to is not None:
                if company and getattr(assigned_to, "company_id", None) != company.id:
                    raise serializers.ValidationError({"assigned_to": ["Сотрудник недоступен для назначения."]})
                request = self.context.get("request")
                if request and hasattr(request, "user") and request.user:
                    user = request.user
                    from .access import is_consulting_supervisor, get_user_region_codes
                    if is_consulting_supervisor(user):
                        sup_regions = get_user_region_codes(user)
                        emp_regions = get_user_region_codes(assigned_to)
                        if sup_regions and not any(r in sup_regions for r in emp_regions):
                            raise serializers.ValidationError({"assigned_to": ["Сотрудник недоступен для назначения."]})

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
        return super().create(validated_data)

    def update(self, instance, validated_data):
        funnel = validated_data.get("funnel") or instance.funnel
        if funnel is not None:
            validated_data["company"] = funnel.company
            validated_data["branch"] = funnel.branch
        return super().update(instance, validated_data)


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
    parent_funnel = serializers.PrimaryKeyRelatedField(
        queryset=FunnelConsalting.objects.all(), required=False, allow_null=True
    )
    parent_funnel_name = serializers.CharField(source="parent_funnel.name", read_only=True)
    owner_user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )
    owner_user_name = serializers.SerializerMethodField(read_only=True)
    next_funnel_display = serializers.CharField(source="next_funnel.name", read_only=True)
    next_stage_display = serializers.CharField(source="next_stage.name", read_only=True)
    next_assign = serializers.ChoiceField(
        choices=FunnelConsalting.NextAssign.choices,
        required=False,
        allow_null=True,
        default=FunnelConsalting.NextAssign.KEEP,
    )
    next_assign_display = serializers.CharField(source="get_next_assign_display", read_only=True)
    next_assign_user_display = serializers.SerializerMethodField()
    is_protected = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FunnelConsalting
        fields = (
            "id", "company", "branch",
            "name", "description", "is_active",
            "funnel_kind", "region_code", "is_main", "is_static", "is_protected",
            "parent_funnel", "parent_funnel_name",
            "owner_user", "owner_user_name",
            "custom_role", "custom_role_name",
            "next_funnel", "next_funnel_display",
            "next_stage", "next_stage_display",
            "next_assign", "next_assign_display",
            "next_assign_user", "next_assign_user_display",
            "is_final", "stage_sla_hours",
            "stages", "leads_count",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "company", "branch", "stages", "leads_count",
            "funnel_kind", "is_main", "is_static", "is_protected", "custom_role_name",
            "parent_funnel_name", "owner_user_name",
            "next_funnel_display", "next_stage_display", "next_assign_display", "next_assign_user_display",
            "created_at", "updated_at",
        )

    def get_owner_user_name(self, obj):
        if not obj.owner_user:
            return None
        full = f"{obj.owner_user.first_name or ''} {obj.owner_user.last_name or ''}".strip()
        return full or getattr(obj.owner_user, "email", None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        parent = attrs.get("parent_funnel") or getattr(self.instance, "parent_funnel", None)
        raw_is_main = self.initial_data.get("is_main") if hasattr(self, "initial_data") and isinstance(self.initial_data, dict) else None
        is_main = raw_is_main if raw_is_main is not None else attrs.get("is_main", getattr(self.instance, "is_main", False))
        funnel_kind = attrs.get("funnel_kind", getattr(self.instance, "funnel_kind", None))

        if parent:
            company = self.context.get("company") or getattr(self.instance, "company", None)
            request = self.context.get("request")
            if not company and request and hasattr(request, "user"):
                company = getattr(request.user, "company", None)
            if company and parent.company_id != company.id:
                raise serializers.ValidationError({"parent_funnel": "Не найдено."})
            if self.instance and parent.id == self.instance.id:
                raise serializers.ValidationError({"parent_funnel": "Воронка не может быть родителем самой себя."})
            if is_main in (True, "true", "True", 1):
                raise serializers.ValidationError({"is_main": "Главная воронка не может быть подворонкой."})
            if self.instance and (self.instance.is_main or getattr(self.instance, "funnel_kind", None) in (
                FunnelConsalting.FunnelKind.MAIN, FunnelConsalting.FunnelKind.ROLE, FunnelConsalting.FunnelKind.REGION
            )):
                raise serializers.ValidationError({"parent_funnel": "Системную или региональную воронку нельзя сделать подворонкой."})
            if parent.parent_funnel_id:
                raise serializers.ValidationError({"parent_funnel": "Нельзя вложить воронку в подворонку."})
            if not getattr(parent, "region_code", "") and not parent.regional_rules.exists() and parent.funnel_kind != FunnelConsalting.FunnelKind.REGION:
                raise serializers.ValidationError({"parent_funnel": "Родитель должен быть региональной воронкой."})
            if funnel_kind and funnel_kind in (
                FunnelConsalting.FunnelKind.MAIN, FunnelConsalting.FunnelKind.ROLE, FunnelConsalting.FunnelKind.REGION
            ):
                raise serializers.ValidationError({"parent_funnel": "Подворонка может быть только пользовательской или воронкой сотрудника."})

        if "next_assign" in attrs and not attrs.get("next_assign"):
            attrs["next_assign"] = FunnelConsalting.NextAssign.KEEP
        next_funnel = attrs.get("next_funnel", getattr(self.instance, "next_funnel", None))
        next_stage = attrs.get("next_stage", getattr(self.instance, "next_stage", None))
        next_assign = attrs.get("next_assign", getattr(self.instance, "next_assign", FunnelConsalting.NextAssign.KEEP))
        next_assign_user = attrs.get("next_assign_user", getattr(self.instance, "next_assign_user", None))

        instance_id = self.instance.id if self.instance else None
        if next_funnel:
            if instance_id and next_funnel.id == instance_id:
                raise serializers.ValidationError({"next_funnel": "Воронка не может быть следующей для самой себя."})
            visited = {instance_id} if instance_id else set()
            curr = next_funnel
            while curr:
                if curr.id in visited:
                    raise serializers.ValidationError({"next_funnel": "Цепочка воронок зациклена."})
                visited.add(curr.id)
                curr = curr.next_funnel

        if next_stage and next_funnel:
            if next_stage.funnel_id != next_funnel.id:
                raise serializers.ValidationError({"next_stage": "Следующая стадия должна принадлежать следующей воронке."})

        if next_assign == FunnelConsalting.NextAssign.USER and not next_assign_user:
            raise serializers.ValidationError({"next_assign_user": "Укажите сотрудника для назначения."})

        return attrs

    def get_leads_count(self, obj):
        """Return the current number of cards for a funnel.

        ``leads_count`` used to be accepted from an annotation/instance
        attribute.  That value is not maintained when a lead is transferred
        or redistributed, so it could become stale.  The main funnel is an
        aggregate board and therefore contains every non-archived company
        lead, even when the lead is physically stored in a regional funnel.
        """
        if obj.is_main:
            return LeadConsalting.objects.filter(
                company_id=obj.company_id,
                is_archived=False,
            ).count()
        return obj.leads.filter(is_archived=False).count()

    def get_next_assign_user_display(self, obj):
        if obj.next_assign_user:
            return f"{obj.next_assign_user.first_name or ''} {obj.next_assign_user.last_name or ''}".strip() or obj.next_assign_user.email
        return None

    def validate_custom_role(self, value):
        company = self._user_company()
        if value and company and value.company_id not in (None, company.id):
            raise serializers.ValidationError("Роль принадлежит другой компании.")
        return value

    def create(self, validated_data):
        role = validated_data.get("custom_role")
        if role is not None:
            validated_data["funnel_kind"] = FunnelConsalting.FunnelKind.ROLE
            validated_data["is_static"] = True
        elif validated_data.get("funnel_kind"):
            validated_data["is_static"] = False
        elif validated_data.get("owner_user"):
            validated_data["funnel_kind"] = FunnelConsalting.FunnelKind.EMPLOYEE
            validated_data["is_static"] = False
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
    region_label = serializers.SerializerMethodField(read_only=True)
    owner_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    loss_reason_label = serializers.CharField(source="loss_reason.label", read_only=True)
    queue_status_display = serializers.CharField(source="get_queue_status_display", read_only=True)
    defer_reason_display = serializers.SerializerMethodField(read_only=True)
    reject_reason_display = serializers.SerializerMethodField(read_only=True)
    is_overdue = serializers.SerializerMethodField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_defer_reason_display(self, obj):
        mapping = {
            "think": "Думает",
            "no_answer": "Не отвечает",
            "busy": "Занят",
            "callback": "Перезвонить",
            "client_request": "Просьба клиента",
            "other": "Другое",
        }
        return mapping.get(obj.defer_reason, obj.defer_reason)

    def get_reject_reason_display(self, obj):
        mapping = {
            "expensive": "Дорого",
            "no_need": "Нет потребности",
            "competitor": "Ушёл к конкуренту",
            "spam": "Спам / Ошибка",
            "invalid_contact": "Неверный контакт",
            "other": "Другое",
        }
        return mapping.get(obj.reject_reason, obj.reject_reason)

    def get_is_overdue(self, obj):
        from django.utils import timezone
        if obj.queue_status == "deferred" and obj.remind_at:
            return obj.remind_at <= timezone.now()
        return False

    class Meta:
        model = LeadConsalting
        fields = (
            "id", "company", "branch",
            "funnel", "funnel_name",
            "stage", "stage_name", "stage_color", "stage_type",
            "region_code", "region_label",
            "client", "client_display",
            "owner", "owner_display",
            "title", "description",
            "full_name", "phone", "email", "address",
            "source", "estimated_value", "probability", "status",
            # единая база лидов
            "queue_status", "queue_status_display", "channel", "remind_at",
            "defer_reason", "defer_reason_display", "defer_comment", "defer_count", "deferred_at", "reminded_at",
            "reject_reason", "reject_reason_display", "reject_comment", "first_reply_at", "converted_at",
            "inbound_external_id", "is_overdue",
            # скоринг
            "score_grade", "score_value", "score_updated_at",
            "budget_confirmed", "urgency", "decision_maker_engaged", "avg_response_minutes",
            # следующее действие
            "next_action_type", "next_action_date", "next_action_note",
            # риск / тайминги
            "is_at_risk", "risk_reason", "last_activity_at", "stage_entered_at", "is_sla_overdue",
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
            # данные WhatsApp чата
            "last_message", "unread_count", "has_unread",
            # CRM-аккаунт клиента (§10.6)
            "tenant_provision_status", "tenant_provision_status_display",
        )
        read_only_fields = (
            "id", "company", "branch",
            "funnel_name", "stage_name", "stage_color", "stage_type",
            "region_label",
            "owner_display", "client_display", "loss_reason_label",
            "score_grade", "score_value", "score_updated_at",
            "is_at_risk", "risk_reason", "last_activity_at", "stage_entered_at", "is_sla_overdue",
            "won_at", "lost_at", "completed_at", "first_contact_at",
            "created_at", "updated_at",
            "participants", "is_archived", "archived_at",
            "payment_registered", "payment_mode",
            "tenant_provision_status", "tenant_provision_status_display",
        )

    last_message = serializers.SerializerMethodField(read_only=True)
    unread_count = serializers.SerializerMethodField(read_only=True)
    has_unread = serializers.SerializerMethodField(read_only=True)
    is_sla_overdue = serializers.SerializerMethodField(read_only=True)
    tenant_provision_status = serializers.SerializerMethodField(read_only=True)
    tenant_provision_status_display = serializers.SerializerMethodField(read_only=True)

    def get_region_label(self, obj):
        code = getattr(obj, "region_code", "") or ""
        if not code:
            return ""
        from .funnel.regional_routing import REGION_LABELS
        return REGION_LABELS.get(code, code)

    def get_tenant_provision_status(self, obj):
        return obj.client.provision_status if obj.client else None

    def get_tenant_provision_status_display(self, obj):
        return obj.client.get_provision_status_display() if obj.client else None

    def get_is_sla_overdue(self, obj):
        if not obj.stage_entered_at:
            return False
        effective_sla = (obj.stage.sla_hours if obj.stage else None) or (obj.funnel.stage_sla_hours if obj.funnel else None)
        if not effective_sla:
            return False
        from django.utils import timezone
        from datetime import timedelta
        return (timezone.now() - obj.stage_entered_at) > timedelta(hours=effective_sla)

    def get_last_message(self, obj):
        last_msg = obj.whatsapp_messages.order_by("-created_at").first()
        if not last_msg:
            return None
        return {
            "id": str(last_msg.id),
            "message_id": last_msg.message_id,
            "text": last_msg.text,
            "direction": last_msg.direction,
            "status": last_msg.status,
            "is_incoming": last_msg.direction == "inbound",
            "created_at": last_msg.created_at.isoformat() if last_msg.created_at else None,
        }

    def get_unread_count(self, obj):
        from .models import WhatsAppMessageConsalting
        return obj.whatsapp_messages.filter(
            direction=WhatsAppMessageConsalting.Direction.INBOUND
        ).exclude(status=WhatsAppMessageConsalting.Status.READ).count()

    def get_has_unread(self, obj):
        return self.get_unread_count(obj) > 0

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

    def validate(self, attrs):
        service = attrs.get("service") if "service" in attrs else getattr(self.instance, "service", None)
        tariff = attrs.get("tariff") if "tariff" in attrs else getattr(self.instance, "tariff", None)
        funnel = attrs.get("funnel") if "funnel" in attrs else getattr(self.instance, "funnel", None)

        if (service or tariff) and ("estimated_value" not in attrs or attrs.get("estimated_value") in (None, Decimal("0.00"), 0)):
            funnel_role = getattr(funnel, "custom_role", None) if funnel else None
            price = None
            if tariff:
                if funnel_role:
                    rp = tariff.role_prices.filter(custom_role=funnel_role).first()
                    price = rp.price if rp else tariff.price
                else:
                    price = tariff.price
            elif service:
                if funnel_role:
                    rp = service.role_prices.filter(custom_role=funnel_role).first()
                    price = rp.price if rp else service.price
                else:
                    price = service.price
            if price is not None:
                attrs["estimated_value"] = price

        return attrs


class LeadFunnelHistoryConsaltingSerializer(serializers.ModelSerializer):
    funnel_display = serializers.CharField(source="funnel.name", read_only=True)
    stage_display = serializers.CharField(source="stage.name", default="", read_only=True)
    owner_display = serializers.SerializerMethodField()
    duration_hours = serializers.SerializerMethodField()

    class Meta:
        model = LeadFunnelHistoryConsalting
        fields = (
            "id", "funnel", "funnel_display", "stage", "stage_display",
            "owner", "owner_display", "entered_at", "left_at", "duration_hours", "transition"
        )
        read_only_fields = ("id", "funnel_display", "stage_display", "owner_display", "duration_hours")

    def get_owner_display(self, obj):
        if obj.owner:
            return f"{obj.owner.first_name or ''} {obj.owner.last_name or ''}".strip() or obj.owner.email
        return None

    def get_duration_hours(self, obj):
        from django.utils import timezone
        end_time = obj.left_at or timezone.now()
        diff = (end_time - obj.entered_at).total_seconds() / 3600.0
        return round(max(0.0, diff), 1)

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
    contentUri = serializers.CharField(source="content_uri", read_only=True)

    class Meta:
        model = WhatsAppMessageConsalting
        fields = (
            "id", "company", "branch", "lead", "message_id",
            "direction", "text", "content_uri", "contentUri", "media_type", "status", "created_at", "updated_at"
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")


class WhatsAppSendSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, default="", help_text="Текст сообщения")
    content_uri = serializers.CharField(required=False, allow_blank=True, default="", help_text="URL медиафайла")
    contentUri = serializers.CharField(required=False, allow_blank=True, default="", help_text="URL медиафайла")


# ==========================
# Salary System (02-salary.md)
# ==========================
class ServiceSalaryRateConsaltingSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    price = serializers.DecimalField(source="service.price", max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ServiceSalaryRateConsalting
        fields = ("id", "company", "service", "service_name", "price", "percent", "fixed_amount", "updated_at")
        read_only_fields = ("id", "company", "service_name", "price", "updated_at")


class SalarySchemeServiceOverrideConsaltingSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = SalarySchemeServiceOverrideConsalting
        fields = ("id", "service", "service_name", "percent", "fixed_amount")
        read_only_fields = ("id", "service_name")


class SalarySchemeConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    service_overrides = SalarySchemeServiceOverrideConsaltingSerializer(many=True, required=False)

    class Meta:
        model = SalarySchemeConsalting
        fields = (
            "id", "company", "user", "user_display",
            "base_salary_enabled", "base_salary", "base_salary_period",
            "percent_enabled", "percent",
            "fixed_enabled", "fixed_amount",
            "service_overrides", "updated_at"
        )
        read_only_fields = ("id", "company", "user", "user_display", "updated_at")

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None


class SalaryDefaultsConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryDefaultsConsalting
        fields = ("id", "company", "percent", "fixed_amount", "base_salary", "base_salary_period", "updated_at")
        read_only_fields = ("id", "company", "updated_at")


class BonusTierConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = BonusTierConsalting
        fields = ("id", "from_amount", "to_amount", "percent")
        read_only_fields = ("id",)


class BonusRuleConsaltingSerializer(serializers.ModelSerializer):
    condition_display = serializers.CharField(source="get_condition_display", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    user_name = serializers.SerializerMethodField()
    tiers = BonusTierConsaltingSerializer(many=True, required=False)

    class Meta:
        model = BonusRuleConsalting
        fields = (
            "id", "company", "name", "condition", "condition_display",
            "service", "service_name", "threshold",
            "reward_type", "reward_value", "period", "applies_to",
            "role", "role_name", "user", "user_name",
            "valid_from", "valid_to", "is_active", "tiers", "created_at"
        )
        read_only_fields = ("id", "company", "condition_display", "service_name", "role_name", "user_name", "created_at")

    def get_user_name(self, obj):
        if obj.user:
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip() or obj.user.email
        return None

    def create(self, validated_data):
        tiers_data = validated_data.pop("tiers", [])
        rule = BonusRuleConsalting.objects.create(**validated_data)
        for tier in tiers_data:
            BonusTierConsalting.objects.create(rule=rule, **tier)
        return rule

    def update(self, instance, validated_data):
        tiers_data = validated_data.pop("tiers", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if tiers_data is not None:
            instance.tiers.all().delete()
            for tier in tiers_data:
                BonusTierConsalting.objects.create(rule=instance, **tier)
        return instance


class SalaryAdjustmentConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    created_by_display = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = SalaryAdjustmentConsalting
        fields = (
            "id", "company", "user", "user_display", "kind", "kind_display",
            "amount", "reason", "reason_display", "comment", "date",
            "status", "source_sale", "created_by", "created_by_display", "created_at"
        )
        read_only_fields = ("id", "company", "user_display", "kind_display", "reason_display", "created_by", "created_by_display", "created_at")

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None

    def get_created_by_display(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name or ''} {obj.created_by.last_name or ''}".strip() or obj.created_by.email
        return None


class SalaryAccrualConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    service_name = serializers.CharField(source="service.name", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = SalaryAccrualConsalting
        fields = (
            "id", "company", "user", "user_display", "service", "service_name",
            "sale", "lead", "kind", "kind_display", "rule", "period_month",
            "base_amount", "percent", "amount", "status", "payout", "created_at"
        )
        read_only_fields = (
            "id", "company", "user_display", "service_name", "kind_display", "created_at"
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
    external_id = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    full_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='manual')
    message = serializers.CharField(required=False, allow_blank=True, default='')
    region_label = serializers.SerializerMethodField(read_only=True)
    owner_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True, default="")
    defer_reason_display = serializers.SerializerMethodField()
    reject_reason_display = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = InboundLeadConsalting
        fields = (
            "id", "company", "full_name", "phone", "source", "external_id",
            "region_code", "region_label",
            "message", "owner", "owner_display", "status", "status_display", "lead",
            "remind_at", "defer_reason", "defer_reason_display", "defer_comment", "defer_count",
            "deferred_at", "reminded_at", "is_overdue",
            "reject_reason", "reject_reason_display", "reject_comment",
            "first_reply_at", "converted_at", "closed_at", "sale",
            "created_at", "updated_at"
        )
        read_only_fields = (
            "id", "company", "region_label", "owner_display", "status_display", "defer_reason_display",
            "reject_reason_display", "defer_count", "deferred_at", "reminded_at",
            "is_overdue", "first_reply_at", "converted_at", "closed_at",
            "created_at", "updated_at"
        )

    def get_region_label(self, obj):
        code = getattr(obj, "region_code", "") or ""
        if not code:
            return ""
        from .funnel.regional_routing import REGION_LABELS
        return REGION_LABELS.get(code, code)

    def get_owner_display(self, obj):
        if obj.owner and (obj.owner.first_name or obj.owner.last_name):
            return f"{obj.owner.first_name or ''} {obj.owner.last_name or ''}".strip()
        return getattr(obj.owner, "email", None) if obj.owner else None

    def get_defer_reason_display(self, obj):
        if hasattr(obj, "get_defer_reason_display"):
            return obj.get_defer_reason_display() or obj.defer_reason or ""
        return obj.defer_reason or ""

    def get_reject_reason_display(self, obj):
        if hasattr(obj, "get_reject_reason_display"):
            return obj.get_reject_reason_display() or obj.reject_reason or ""
        return obj.reject_reason or ""

    def get_is_overdue(self, obj):
        if obj.status == InboundLeadConsalting.Status.DEFERRED and obj.remind_at:
            from django.utils import timezone
            return obj.remind_at <= timezone.now()
        return False


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


# ==========================
# WazzupAccountConsaltingSerializer
# ==========================
from .models import WazzupAccountConsalting


class WazzupAccountConsaltingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    integration_type_display = serializers.CharField(source='get_integration_type_display', read_only=True)

    class Meta:
        model = WazzupAccountConsalting
        fields = (
            "id", "company", "branch", "api_key", "api_url", "channel_id",
            "integration_type", "integration_type_display",
            "is_active", "is_connected",
            "green_api_id_instance", "green_api_token_instance",
            "green_api_url", "green_api_media_url", "green_api_enabled",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")


# ==========================
# Subscription Serializers (§5.5)
# ==========================
class SubscriptionPaymentConsaltingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPaymentConsalting
        fields = ("id", "period_month", "due_date", "amount", "status", "paid_at", "paid_via", "cashbox_id", "payment_method")
        read_only_fields = ("id", "period_month", "due_date", "amount")


class SubscriptionConsaltingSerializer(serializers.ModelSerializer):
    service_display = serializers.CharField(source="service.name", read_only=True)
    tariff_display = serializers.CharField(source="tariff.name", default="", read_only=True)
    period_display = serializers.CharField(source="get_period_display", read_only=True)
    next_payment = serializers.SerializerMethodField()
    payments = SubscriptionPaymentConsaltingSerializer(many=True, read_only=True)

    class Meta:
        model = SubscriptionConsalting
        fields = (
            "id", "service", "service_display", "tariff", "tariff_display",
            "amount", "period", "period_display", "status", "start_date", "paid_through",
            "next_payment", "payments", "created_at"
        )
        read_only_fields = (
            "id", "service_display", "tariff_display", "period_display", "paid_through", "next_payment", "payments", "created_at"
        )

    def get_next_payment(self, obj):
        next_p = obj.payments.filter(
            status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
        ).order_by("due_date").first()
        if not next_p:
            return None
        return SubscriptionPaymentConsaltingSerializer(next_p).data


# ==========================
# SalesPlanConsaltingSerializer (§6.4)
# ==========================
class SalesPlanConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = SalesPlanConsalting
        fields = ("id", "user", "user_display", "period_month", "amount", "created_at")
        read_only_fields = ("id", "user_display", "created_at")

    def get_user_display(self, obj):
        if obj.user:
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip() or obj.user.email
        return ""


# ==========================
# CashOperation & CashRequest Serializers (§7.2, §7.4)
# ==========================
class CashOperationConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    confirmed_by_display = serializers.SerializerMethodField()

    class Meta:
        model = CashOperationConsalting
        fields = (
            "id", "user", "user_display", "confirmed_by", "confirmed_by_display",
            "sale", "kind", "direction", "amount", "payment_method", "comment", "created_at"
        )
        read_only_fields = ("id", "user_display", "confirmed_by_display", "created_at")

    def get_user_display(self, obj):
        if obj.user:
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip() or obj.user.email
        return ""

    def get_confirmed_by_display(self, obj):
        if obj.confirmed_by:
            return f"{obj.confirmed_by.first_name or ''} {obj.confirmed_by.last_name or ''}".strip() or obj.confirmed_by.email
        return ""


class CashRequestConsaltingSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    confirmed_by_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    source_display = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_method_display = serializers.SerializerMethodField()
    reject_reason_display = serializers.CharField(source="get_reject_reason_display", read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = CashRequestConsalting
        fields = (
            "id", "user", "user_display", "client", "client_display",
            "sale", "subscription_payment", "source_display",
            "kind", "kind_display", "direction", "amount",
            "payment_method", "payment_method_display",
            "status", "status_display", "comment",
            "reject_reason", "reject_reason_display", "reject_comment",
            "confirmed_by", "confirmed_by_display", "confirmed_at",
            "is_overdue", "created_at"
        )
        read_only_fields = (
            "id", "user_display", "client_display", "source_display",
            "kind_display", "status_display", "payment_method_display",
            "reject_reason_display", "confirmed_by_display", "confirmed_at",
            "is_overdue", "created_at"
        )

    def get_user_display(self, obj):
        if obj.user:
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip() or obj.user.email
        return ""

    def get_confirmed_by_display(self, obj):
        if obj.confirmed_by:
            return f"{obj.confirmed_by.first_name or ''} {obj.confirmed_by.last_name or ''}".strip() or obj.confirmed_by.email
        return ""

    def get_client_display(self, obj):
        if obj.client:
            return obj.client.full_name
        if obj.sale and obj.sale.client:
            return obj.sale.client.full_name
        return "—"

    def get_source_display(self, obj):
        if obj.sale:
            s_name = obj.sale.services.name if obj.sale.services else "Продажа"
            t_name = obj.sale.tariff.name if obj.sale.tariff else ""
            return f"{s_name} / {t_name}".strip(" /")
        return obj.get_kind_display()

    def get_payment_method_display(self, obj):
        pm = obj.payment_method or "cash"
        return "Наличными" if pm == "cash" else ("Перевод" if pm == "transfer" else "Карта")

    def get_is_overdue(self, obj):
        if obj.status != CashRequestConsalting.Status.PENDING:
            return False
        from django.utils import timezone
        from datetime import timedelta
        overdue_hrs = 24
        if obj.company and hasattr(obj.company, "consalting_cash_confirmation"):
            overdue_hrs = obj.company.consalting_cash_confirmation.overdue_hours
        return (timezone.now() - obj.created_at) > timedelta(hours=overdue_hrs)


class CashConfirmationSettingsConsaltingSerializer(serializers.ModelSerializer):
    mode = serializers.CharField(required=False)

    class Meta:
        model = CashConfirmationSettingsConsalting
        fields = ("id", "company", "mode", "skip_for_cashier", "overdue_hours")
        read_only_fields = ("id", "company")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Канон §9.0: наружу отдавать required вместо always
        if data.get("mode") == "always":
            data["mode"] = "required"
        return data

    def validate_mode(self, value):
        val = str(value or "").strip().lower()
        if val in ("always", "required"):
            return "always"
        elif val in ("off", "auto"):
            return "off"
        elif val == "cash_only":
            return "cash_only"
        raise serializers.ValidationError("Недопустимый режим подтверждения. Варианты: required, cash_only, off.")


# ==========================
# SaleRefundConsaltingSerializer (§8.3)
# ==========================
class SaleRefundConsaltingSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = SaleRefundConsalting
        fields = ("id", "sale", "amount", "reason", "comment", "refund_mode", "created_by", "created_by_display", "created_at")
        read_only_fields = ("id", "created_by", "created_by_display", "created_at")

    def get_created_by_display(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name or ''} {obj.created_by.last_name or ''}".strip() or obj.created_by.email
        return ""


# ==========================
# RegionalFunnelRouting (§4.3)
# ==========================
class RegionalFunnelRuleConsaltingSerializer(serializers.ModelSerializer):
    funnel_id = serializers.UUIDField(source="funnel.id", read_only=True)
    funnel_display = serializers.SerializerMethodField()
    region_label = serializers.SerializerMethodField()

    class Meta:
        model = RegionalFunnelRuleConsalting
        fields = (
            "id", "funnel_id", "funnel_display", "region_code", "label", "region_label",
            "phone_prefixes", "wazzup_account_ids", "source_channels",
            "assign_role_ids", "assign_strategy", "is_active", "order"
        )
        read_only_fields = ("id", "funnel_id", "funnel_display", "region_label")

    def get_funnel_display(self, obj):
        return obj.funnel.name if obj.funnel else ""

    def get_region_label(self, obj):
        if getattr(obj, "label", None):
            return obj.label
        from .funnel.regional_routing import REGION_LABELS
        return REGION_LABELS.get(obj.region_code, obj.region_code)


class RegionalFunnelRoutingConsaltingSerializer(serializers.ModelSerializer):
    default_funnel_id = serializers.UUIDField(source="default_funnel.id", allow_null=True, required=False)
    default_funnel_display = serializers.SerializerMethodField()
    rules = RegionalFunnelRuleConsaltingSerializer(many=True, read_only=True)

    class Meta:
        model = RegionalFunnelRoutingConsalting
        fields = (
            "id", "enabled", "fallback_strategy", "balance_strategy",
            "default_funnel_id", "default_funnel_display",
            "rules"
        )
        read_only_fields = ("id", "default_funnel_display", "rules")

    def get_default_funnel_display(self, obj):
        return obj.default_funnel.name if obj.default_funnel else ""


class LeadAdSpendSerializer(serializers.ModelSerializer):
    cost_per_lead = serializers.SerializerMethodField()
    spend = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True, default=Decimal("0.00"))

    class Meta:
        model = LeadAdSpend
        fields = (
            "id",
            "date",
            "impressions",
            "leads",
            "spend",
            "cost_per_lead",
            "note",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "cost_per_lead", "created_at", "updated_at")

    def _request(self):
        return self.context.get("request")

    def _user(self):
        req = self._request()
        return getattr(req, "user", None) if req else None

    def _user_company(self):
        user = self._user()
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def create(self, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
        user = self._user()
        if user and user.is_authenticated:
            validated_data["created_by"] = user
        return super().create(validated_data)

    def get_cost_per_lead(self, obj) -> str:
        return f"{obj.cost_per_lead:.2f}"

    def validate_date(self, value):
        from django.utils import timezone
        if not value:
            raise serializers.ValidationError("Укажите дату строки.")
        if value > timezone.localdate():
            raise serializers.ValidationError("Дата не может быть в будущем.")
        return value

    def validate_impressions(self, value):
        if value < 0:
            raise serializers.ValidationError("Показы и лиды не могут быть отрицательными.")
        return value

    def validate_leads(self, value):
        if value < 0:
            raise serializers.ValidationError("Показы и лиды не могут быть отрицательными.")
        return value

    def validate_spend(self, value):
        if value < 0 or value > Decimal("999999999.99"):
            raise serializers.ValidationError("Сумма затрат указана неверно.")
        return value

    def validate_note(self, value):
        if value and len(value) > 255:
            raise serializers.ValidationError("Комментарий слишком длинный.")
        return value or ""

    def validate(self, attrs):
        from django.utils import timezone
        date_val = attrs.get("date") or (self.instance.date if self.instance else None)
        if not date_val:
            raise serializers.ValidationError({"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]})
        if date_val > timezone.localdate():
            raise serializers.ValidationError({"detail": "Дата не может быть в будущем.", "date": ["Дата не может быть в будущем."]})

        impressions = attrs.get("impressions") if "impressions" in attrs else (self.instance.impressions if self.instance else 0)
        leads = attrs.get("leads") if "leads" in attrs else (self.instance.leads if self.instance else 0)
        spend = attrs.get("spend") if "spend" in attrs else (self.instance.spend if self.instance else Decimal("0.00"))

        if impressions < 0 or leads < 0:
            raise serializers.ValidationError({"detail": "Показы и лиды не могут быть отрицательными."})
        if spend < 0 or spend > Decimal("999999999.99"):
            raise serializers.ValidationError({"detail": "Сумма затрат указана неверно."})

        if impressions > 0 and leads > 0 and leads > impressions:
            raise serializers.ValidationError({"detail": "Лидов больше, чем показов — проверьте цифры."})

        note = attrs.get("note", self.instance.note if self.instance else "")
        if note and len(note) > 255:
            raise serializers.ValidationError({"detail": "Комментарий слишком длинный."})

        company = self._user_company()
        if company:
            qs = LeadAdSpend.objects.filter(company=company, date=date_val)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                date_str = date_val.strftime("%d.%m.%Y")
                raise serializers.ValidationError({
                    "detail": f"За {date_str} отчёт уже заведён — измените существующую строку."
                })

        return attrs






