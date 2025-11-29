from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    BarberProfile,
    Service,
    Client,
    Appointment,
    Document,
    Folder,
    ServiceCategory,
    Payout,
    PayoutSale,
)
from apps.users.models import Branch  # для проверки филиала по ?branch=
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from calendar import monthrange
from datetime import timedelta
import re


# ===========================
# Общий миксин: company/branch (branch авто из контекста/запроса)
# ===========================
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      0) ?branch=<uuid> в запросе (если филиал принадлежит компании пользователя)
      1) user.primary_branch (свойство или метод, если есть)
      2) request.branch (если положил middleware)
      3) None (глобальная запись компании)
    """

    # ---- helpers ----
    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def _user_company(self):
        user = self._user()
        if not user:
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    # ---- какой филиал реально использовать ----
    def _auto_branch(self):
        request = self.context.get("request")
        user = self._user()
        company = self._user_company()
        comp_id = getattr(company, "id", None)

        if not request or not user or not comp_id:
            return None

        # 0) ?branch=<uuid> в query-параметрах
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=comp_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # если id кривой/чужой — игнорируем
                pass

        # 1) primary_branch может быть полем или методом
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == comp_id:
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == comp_id:
            return primary

        # 2) из middleware (на будущее)
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == comp_id:
                return b

        # 3) глобально
        return None

    def create(self, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
        # branch строго из контекста (payload игнорируется)
        validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
        validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ===========================
# BarberProfile
# ===========================
class BarberProfileSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")  # показываем id филиала (или None)

    class Meta:
        model = BarberProfile
        fields = [
            "id", "company", "branch",
            "full_name", "phone", "extra_phone",
            "work_schedule", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]


class ServiceCategorySerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = ServiceCategory
        fields = [
            "id",
            "company",
            "branch",
            "name",
            "is_active",
        ]
        read_only_fields = [
            "id",
            "company",
            "branch",
        ]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Название не может быть пустым.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if not company:
            return attrs

        target_branch = self._auto_branch()
        name = (attrs.get("name") or getattr(self.instance, "name", "")).strip()

        qs = ServiceCategory.objects.filter(company=company, name__iexact=name)
        if target_branch is None:
            qs = qs.filter(branch__isnull=True)   # глобальные категории
        else:
            qs = qs.filter(branch=target_branch)  # категории конкретного филиала

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                {"name": "Категория с таким названием уже существует (для этой компании/филиала)."}
            )

        return attrs
    
class ServiceSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # категория теперь динамическая (FK)
    category = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.all(),
        allow_null=True,
        required=False,
    )
    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
    )

    class Meta:
        model = Service
        fields = [
            "id", "company", "branch",
            "name", "time",
            "category", "category_name",
            "price", "is_active",
        ]
        read_only_fields = ["id", "company", "branch", "category_name"]

    def validate_name(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        request = self.context.get("request")
        company = getattr(getattr(request, "user", None), "company", None) if request else None
        company_id = getattr(company, "id", None)
        if not company_id:
            return attrs

        target_branch = self._auto_branch()  # тот же источник, что в create()
        name = (attrs.get("name") or getattr(self.instance, "name", "")).strip()

        # ---- проверка уникальности имени внутри компании/филиала ----
        qs = Service.objects.filter(company_id=company_id, name__iexact=name)
        if target_branch is None:
            qs = qs.filter(branch__isnull=True)   # среди глобальных
        else:
            qs = qs.filter(branch=target_branch)  # внутри филиала

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError({
                "name": "Услуга с таким названием уже существует (на этом уровне: глобально/филиал)."
            })

        # ---- проверка категории, если указана ----
        category = attrs.get("category") or getattr(self.instance, "category", None)
        if category:
            if category.company_id != company_id:
                raise serializers.ValidationError({"category": "Категория принадлежит другой компании."})
            if target_branch is not None and category.branch_id not in (None, target_branch.id):
                raise serializers.ValidationError({"category": "Категория принадлежит другому филиалу."})

        return attrs


# ===========================
# Client
# ===========================
class ClientSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = Client
        fields = [
            "id", "company", "branch",
            "full_name", "phone", "email",
            "birth_date", "status", "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]
        ref_name = "BarberClient"

    def validate(self, attrs):
        """
        Клиент создаётся глобально или в текущем филиале пользователя.
        Поле branch read-only, поэтому клиент не может его подменить.
        """
        return attrs


# ===========================
# Appointment
# ===========================
class AppointmentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    client_name = serializers.CharField(source="client.full_name", read_only=True)
    barber_name = serializers.SerializerMethodField()
    services = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.all(),
        many=True,
    )
    services_names = serializers.SlugRelatedField(
        source='services',
        many=True,
        read_only=True,
        slug_field='name',
    )

    class Meta:
        model = Appointment
        fields = [
            "id", "company", "branch",
            "client", "client_name",
            "barber", "barber_name",
            "services", "services_names",
            "start_at", "end_at",
            "price", "discount",        # 👈 новые поля
            "status", "comment",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]

    def get_barber_name(self, obj):
        if not obj.barber:
            return None
        if obj.barber.first_name or obj.barber.last_name:
            return f"{obj.barber.first_name or ''} {obj.barber.last_name or ''}".strip()
        return obj.barber.email

    def create(self, validated_data):
        services = validated_data.pop("services", [])
        instance = super().create(validated_data)
        instance.services.set(services)
        return instance

    def update(self, instance, validated_data):
        services = validated_data.pop("services", None)
        instance = super().update(instance, validated_data)
        if services is not None:
            instance.services.set(services)
        return instance

    def _parse_minutes(self, s: str) -> int:
        """Парсим Service.time: '30', '00:30', '1:15', '30m', '1h', '1h30m' -> минуты."""
        if not s:
            return 0
        s = s.strip()
        try:
            return int(s)  # "30"
        except ValueError:
            pass
        if ":" in s:  # "HH:MM" или "MM:SS" — используем как часы:минуты
            h, m = s.split(":", 1)
            return int(h) * 60 + int(m)
        m = re.match(r'(?i)^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$', s)
        if m:
            return (int(m.group(1) or 0) * 60) + int(m.group(2) or 0)
        return 0

    def validate(self, attrs):
        """
        ОДИН общий validate:
          1) проверка принадлежности client/barber/services компании и филиалу
          2) автоподстановка end_at из суммарной длительности услуг
          3) автоподстановка price из суммарной цены услуг (если не передали)
          4) проверка discount (0–100) и end_at > start_at
        """
        attrs = super().validate(attrs)

        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) if request else None
        company_id = getattr(user_company, "id", None)
        target_branch = self._auto_branch()

        # текущее значение client/barber/services с учётом partial
        client = attrs.get("client") or getattr(self.instance, "client", None)
        barber = attrs.get("barber") or getattr(self.instance, "barber", None)
        services = attrs.get("services") or (self.instance.services.all() if self.instance else [])

        # --- company проверки ---
        for obj, name in [(client, "client"), (barber, "barber")]:
            if obj and getattr(obj, "company_id", None) != company_id:
                raise serializers.ValidationError({name: "Объект не принадлежит вашей компании."})
        for service in services:
            if getattr(service, "company_id", None) != company_id:
                raise serializers.ValidationError({"services": "Одна из услуг принадлежит другой компании."})

        # --- branch проверки ---
        if target_branch is not None:
            tb_id = target_branch.id
            if client and getattr(client, "branch_id", None) not in (None, tb_id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})
            for service in services:
                if getattr(service, "branch_id", None) not in (None, tb_id):
                    raise serializers.ValidationError({
                        "services": f"Услуга '{service.name}' принадлежит другому филиалу."
                    })

        # --- время ---
        start_at = attrs.get("start_at") or getattr(self.instance, "start_at", None)
        end_at = attrs.get("end_at") or getattr(self.instance, "end_at", None)

        # Если конец не передан — вычисляем из услуг
        if start_at and not end_at and services:
            total_minutes = 0
            for s in services:
                total_minutes += self._parse_minutes(getattr(s, "time", None))
            if total_minutes > 0:
                attrs["end_at"] = start_at + timedelta(minutes=total_minutes)
                end_at = attrs["end_at"]

        if start_at and end_at and not (end_at > start_at):
            raise serializers.ValidationError({"end_at": "Должно быть строго позже start_at."})

        # --- цена и скидка ---
        price = attrs.get("price", None)
        discount = attrs.get("discount", None)

        # если цена не передана — суммируем цены услуг
        if price is None and services:
            total_price = sum((s.price or 0) for s in services)
            attrs["price"] = total_price
            price = total_price

        # скидка: 0–100
        if discount is not None:
            if discount < 0 or discount > 100:
                raise serializers.ValidationError({"discount": "Скидка должна быть от 0 до 100."})

        return attrs


# ===========================
# Folder
# ===========================
class FolderSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=Folder.objects.all(), allow_null=True, required=False
    )
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Folder
        fields = ["id", "company", "branch", "name", "parent", "parent_name"]
        read_only_fields = ["id", "company", "branch", "parent_name"]
        ref_name = "BarberFolder"

    def validate_parent(self, parent):
        """
        Родительская папка (если указана) должна быть вашей компании
        и глобальной/филиала пользователя.
        """
        if parent is None:
            return parent
        request = self.context.get("request")
        user_company_id = getattr(getattr(request, "user", None), "company_id", None) if request else None
        target_branch = self._auto_branch()

        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError("Родительская папка принадлежит другой компании.")

        if target_branch is not None and parent.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Родительская папка принадлежит другому филиалу.")
        return parent


# ===========================
# Document
# ===========================
class DocumentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    folder_name = serializers.CharField(source="folder.name", read_only=True)

    class Meta:
        ref_name = "BarberDocument"
        model = Document
        fields = [
            "id", "company", "branch",
            "name", "file",
            "folder", "folder_name",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at", "folder_name"]

    def validate_folder(self, folder):
        """
        Папка должна быть компании пользователя и глобальной/филиала пользователя.
        """
        if folder is None:
            return folder
        request = self.context.get("request")
        user_company_id = getattr(getattr(request, "user", None), "company_id", None) if request else None
        target_branch = self._auto_branch()

        folder_company_id = getattr(getattr(folder, "company", None), "id", None)
        if folder_company_id and user_company_id and folder_company_id != user_company_id:
            raise serializers.ValidationError("Папка принадлежит другой компании.")

        if target_branch is not None and folder.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Папка принадлежит другому филиалу.")
        return folder



class PayoutSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    """
    Создаёт выплату и сразу считает:
      - appointments_count  — кол-во записей за период
      - total_revenue       — выручка за период
      - payout_amount       — сумма выплаты
    """

    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    barber_name = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            "id",
            "company",
            "branch",
            "barber",
            "barber_name",
            "period",
            "mode",
            "rate",
            "appointments_count",
            "total_revenue",
            "payout_amount",
            "comment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "company",
            "branch",
            "appointments_count",
            "total_revenue",
            "payout_amount",
            "created_at",
            "updated_at",
        ]

    # ----- helpers -----

    def get_barber_name(self, obj):
        b = obj.barber
        if not b:
            return None
        if b.first_name or b.last_name:
            return f"{b.first_name or ''} {b.last_name or ''}".strip()
        return b.email

    def validate_period(self, value: str) -> str:
        """
        Период в формате YYYY-MM (например, 2025-11).
        """
        import re

        if not re.match(r"^\d{4}-\d{2}$", value):
            raise serializers.ValidationError("Период должен быть в формате YYYY-MM, например: 2025-11.")
        year, month = map(int, value.split("-"))
        if not (1 <= month <= 12):
            raise serializers.ValidationError("Месяц должен быть от 01 до 12.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)

        company = self._user_company()
        barber = attrs.get("barber") or getattr(self.instance, "barber", None)
        mode = attrs.get("mode") or getattr(self.instance, "mode", None)
        rate = attrs.get("rate") or getattr(self.instance, "rate", None)

        # барбер должен быть из той же компании
        if company and barber and getattr(barber, "company_id", None) != getattr(company, "id", None):
            raise serializers.ValidationError({"barber": "Сотрудник принадлежит другой компании."})

        # проверка ставки для процента
        if mode == Payout.Mode.PERCENT and rate is not None:
            if rate < 0 or rate > 100:
                raise serializers.ValidationError({"rate": "Для режима 'percent' ставка должна быть от 0 до 100."})

        return attrs

    def _period_bounds(self, period: str):
        """
        YYYY-MM -> (date_start, date_end_exclusive)
        """
        year, month = map(int, period.split("-"))
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        return start, end

    # ----- create с расчётом выплаты -----

    def create(self, validated_data):
        company = self._user_company()
        if not company:
            raise serializers.ValidationError("У пользователя не задана компания.")

        branch = self._auto_branch()
        barber = validated_data["barber"]
        period = validated_data["period"]
        mode = validated_data["mode"]
        rate = Decimal(str(validated_data["rate"]))

        start_date, end_date = self._period_bounds(period)

        qs = Appointment.objects.filter(
            company=company,
            barber=barber,
            start_at__date__gte=start_date,
            start_at__date__lt=end_date,
            status=Appointment.Status.COMPLETED,  # считаем только завершённые
        )
        if branch is not None:
            qs = qs.filter(branch=branch)

        appointments_count = qs.count()

        from django.db.models import Sum

        total_revenue = qs.aggregate(total=Sum("price"))["total"] or Decimal("0.00")

        # расчёт выплаты
        if mode == Payout.Mode.RECORD:
            payout_amount = rate * Decimal(appointments_count)
        elif mode == Payout.Mode.FIXED:
            payout_amount = rate
        elif mode == Payout.Mode.PERCENT:
            payout_amount = (total_revenue * rate) / Decimal("100")
        else:
            payout_amount = Decimal("0.00")

        payout_amount = payout_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


        validated_data["company"] = company
        validated_data["branch"] = branch
        validated_data["appointments_count"] = appointments_count
        validated_data["total_revenue"] = total_revenue
        validated_data["payout_amount"] = payout_amount

        # обходим create из миксина и идём сразу в ModelSerializer
        return super(CompanyBranchReadOnlyMixin, self).create(validated_data)
class PayoutSaleSerializer(serializers.ModelSerializer):
    # принимаем "2025-11" и ISO, в БД хранится datetime,
    # наружу показываем обратно "2025-11"
    period = serializers.DateTimeField(
        input_formats=["%Y-%m", "iso-8601"],
        format="%Y-%m",
    )

    class Meta:
        model = PayoutSale
        fields = [
            "id",
            "period",
            "old_total_fund",
            "new_total_fund",
            "total",
        ]
        read_only_fields = ["id", "total"]

    def _calc_total(self, old_fund, new_fund) -> Decimal:
        # total = old - new (на сколько фонд упал)
        return (Decimal(old_fund) - Decimal(new_fund)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def _get_company(self):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if not user:
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _get_branch(self, request, company):
        """
        Берём филиал только из ?branch=<uuid>, если он принадлежит компании.
        Если нет/кривой — считаем запись глобальной (branch=None).
        """
        if not request or not company:
            return None

        from apps.users.models import Branch

        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if not branch_id:
            return None

        try:
            return Branch.objects.get(id=branch_id, company=company)
        except (Branch.DoesNotExist, ValueError):
            return None

    def create(self, validated_data):
        request = self.context.get("request")

        company = self._get_company()
        if not company:
            raise serializers.ValidationError("У пользователя не задана компания.")

        branch = self._get_branch(request, company)

        validated_data["company"] = company
        validated_data["branch"] = branch

        old_fund = validated_data["old_total_fund"]
        new_fund = validated_data["new_total_fund"]
        validated_data["total"] = self._calc_total(old_fund, new_fund)

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # компанию/филиал не даём менять
        validated_data.pop("company", None)
        validated_data.pop("branch", None)

        instance = super().update(instance, validated_data)
        instance.total = self._calc_total(
            instance.old_total_fund,
            instance.new_total_fund,
        )
        instance.save(update_fields=["old_total_fund", "new_total_fund", "total"])
        return instance
