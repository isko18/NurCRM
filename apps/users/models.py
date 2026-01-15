from django.db import models, transaction
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta
import uuid

from apps.users.managers import UserManager


class Feature(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    name = models.CharField(max_length=128, db_index=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Функция"
        verbose_name_plural = "Функции"
        indexes = [
            models.Index(fields=["name"]),
        ]


class SubscriptionPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    name = models.CharField(max_length=128, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, null=True)
    features = models.ManyToManyField(Feature, blank=True)

    def __str__(self):
        return f"{self.name} - {self.price}₽"

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        indexes = [
            models.Index(fields=["name"]),
        ]

    def has_feature(self, feature_name: str) -> bool:
        return self.features.filter(name=feature_name).exists()


class Roles(models.TextChoices):
    ADMIN = "admin", "Администратор"
    OWNER = "owner", "Владелец"


class Sector(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, db_index=True, verbose_name="Название отрасли")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Отрасль"
        verbose_name_plural = "Отрасли"


class Industry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, db_index=True, verbose_name="Название вида деятельности")
    sectors = models.ManyToManyField(Sector, blank=True, related_name="industries", verbose_name="Отрасли")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Вид деятельности"
        verbose_name_plural = "Виды деятельности"


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name="Название компании")
    slug = models.SlugField("Slug", max_length=80, unique=True, db_index=True, blank=True)

    subscription_plan = models.ForeignKey("SubscriptionPlan", on_delete=models.SET_NULL, null=True, blank=True)
    industry = models.ForeignKey("Industry", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Вид деятельности")
    sector = models.ForeignKey("Sector", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Отрасль")
    phone = models.CharField(max_length=60, verbose_name="Номер телефона")
    phones_howcase = models.CharField(max_length=60, verbose_name="Номер для обращения по витрине")

    owner = models.OneToOneField("User", on_delete=models.CASCADE, related_name="owned_company", verbose_name="Владелец компании")

    llc = models.CharField("Название компании", max_length=255, blank=True, null=True)
    inn = models.CharField("ИНН", max_length=32, blank=True, null=True)
    okpo = models.CharField("ОКПО", max_length=32, blank=True, null=True)
    score = models.CharField("Расчетный счет", max_length=64, blank=True, null=True)
    bik = models.CharField("БИК", max_length=32, blank=True, null=True)
    address = models.CharField("Адрес", max_length=255, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    start_date = models.DateTimeField(verbose_name="Дата начала", blank=True, null=True)
    end_date = models.DateTimeField(verbose_name="Дата окончания", blank=True, null=True)

    can_view_documents = models.BooleanField(default=False, verbose_name="Доступ к документам")
    can_view_whatsapp = models.BooleanField(default=False, verbose_name="Доступ к whatsapp")
    can_view_instagram = models.BooleanField(default=False, verbose_name="Доступ к instagram")
    can_view_telegram = models.BooleanField(default=False, verbose_name="Доступ к telegram")

    scale_api_token = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        null=True,
        verbose_name="Токен для весов/агентов",
        help_text="Постоянный токен, которым подключаются весы к API",
    )

    def ensure_scale_api_token(self):
        if not self.scale_api_token:
            self.scale_api_token = str(uuid.uuid4())
            self.save(update_fields=["scale_api_token"])
        return self.scale_api_token

    def _generate_unique_slug(self) -> str:
        base = slugify(self.name)[:60] or "company"
        candidate = base
        i = 2
        while type(self).objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{i}"
            i += 1
            if len(candidate) > 80:
                candidate = f"{base[:50]}-{uuid.uuid4().hex[:8]}"
        return candidate

    def save(self, *args, **kwargs):
        # даты
        if not self.start_date:
            self.start_date = timezone.now()
        if not self.end_date and self.start_date:
            self.end_date = self.start_date + timedelta(days=10)

        # slug
        if not self.slug:
            self.slug = self._generate_unique_slug()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["end_date"]),
        ]

class CustomRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="custom_roles",
        verbose_name="Компания",
    )
    name = models.CharField(max_length=64, verbose_name="Название роли")

    class Meta:
        verbose_name = "Доп. роль"
        verbose_name_plural = "Доп. роли"
        unique_together = ("company", "name")
        indexes = [
            models.Index(fields=["company", "name"]),
        ]

    def __str__(self):
        return self.name


class Branch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    company = models.ForeignKey("Company", on_delete=models.CASCADE, related_name="branches", verbose_name="Компания")

    name = models.CharField(max_length=128, verbose_name="Название филиала")
    code = models.SlugField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Код филиала (slug)",
        help_text="Например: spb, moscow, online — уникален в пределах компании.",
    )
    address = models.CharField(max_length=255, blank=True, null=True, verbose_name="Адрес")
    phone = models.CharField(max_length=32, blank=True, null=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, null=True, verbose_name="Email филиала")

    timezone = models.CharField(
        max_length=64,
        default="Asia/Bishkek",
        verbose_name="Часовой пояс",
        help_text="Используется для расписаний, графиков и аналитики",
    )

    subscription_plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branches",
        verbose_name="Тариф филиала",
    )

    features = models.ManyToManyField("Feature", blank=True, related_name="branches", verbose_name="Доп. функции филиала")

    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Филиал"
        verbose_name_plural = "Филиалы"
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uq_branch_company_name"),
            models.UniqueConstraint(fields=["company", "code"], name="uq_branch_company_code"),
        ]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "code"]),
        ]

    def __str__(self):
        return f"{self.company.name} / {self.name}"

    def _generate_unique_code(self) -> str:
        # Очень редко, но коллизии возможны — делаем гарантированно уникально ВНУТРИ компании
        for _ in range(20):
            candidate = uuid.uuid4().hex[:10]
            if not Branch.objects.filter(company_id=self.company_id, code=candidate).exists():
                return candidate
        # если что-то совсем странное — fallback длиннее
        return uuid.uuid4().hex

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_unique_code()
        super().save(*args, **kwargs)

    def effective_plan(self):
        return self.subscription_plan or getattr(self.company, "subscription_plan", None)

    def has_feature(self, feature_name: str) -> bool:
        if self.features.filter(name=feature_name).exists():
            return True
        plan = self.effective_plan()
        return bool(plan and plan.has_feature(feature_name))


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    email = models.EmailField(unique=True, verbose_name="Email")
    password = models.CharField(max_length=128, verbose_name="Пароль")

    first_name = models.CharField(max_length=64, verbose_name="Имя")
    last_name = models.CharField(max_length=64, verbose_name="Фамилия")
    avatar = models.URLField(blank=True, null=True, verbose_name="Аватар (URL)")
    phone_number = models.CharField(max_length=64, verbose_name="Номер телефона", blank=True, null=True)
    track_number = models.CharField(max_length=64, verbose_name="Номер машины", blank=True, null=True)

    company = models.ForeignKey(
        "Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="employees",
        verbose_name="Компания",
    )

    role = models.CharField(max_length=32, choices=Roles.choices, blank=True, null=True, verbose_name="Системная роль")
    custom_role = models.ForeignKey(
        CustomRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="Кастомная роль",
    )

    # ===== Права доступа =====
    can_view_dashboard = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к обзору")
    can_view_cashbox = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к кассе")
    can_view_departments = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к отделам")
    can_view_orders = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к заказам")
    can_view_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к аналитике")
    can_view_products = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к товарам")
    can_view_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к бронированию")
    can_view_department_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к аналитике отделов")
    can_view_employees = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к сотрудникам")
    can_view_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к клиентам")
    can_view_brand_category = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к брендам и категориям")
    can_view_settings = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к настройкам")
    can_view_sale = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к продажам")

    can_view_building_work_process = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к процессам строительства")
    can_view_building_objects = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к объектам")
    can_view_additional_services = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к доп. услугам")
    can_view_debts = models.BooleanField(default=False, blank=True, null=True, verbose_name="Доступ к долгам")

    can_view_barber_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name="Барбершоп: клиенты")
    can_view_barber_services = models.BooleanField(default=False, blank=True, null=True, verbose_name="Барбершоп: услуги")
    can_view_barber_history = models.BooleanField(default=False, blank=True, null=True, verbose_name="Барбершоп: история")
    can_view_barber_records = models.BooleanField(default=False, blank=True, null=True, verbose_name="Барбершоп: записи")

    can_view_hostel_rooms = models.BooleanField(default=False, blank=True, null=True, verbose_name="Хостел: комнаты")
    can_view_hostel_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name="Хостел: бронирование")
    can_view_hostel_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name="Хостел: клиенты")
    can_view_hostel_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name="Хостел: аналитика")

    can_view_cafe_menu = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: меню")
    can_view_cafe_orders = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: заказы")
    can_view_cafe_purchasing = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: закупки")
    can_view_cafe_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: бронирование")
    can_view_cafe_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: клиенты")
    can_view_cafe_tables = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: столы")
    can_view_cafe_cook = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: Кухня")
    can_view_cafe_inventory = models.BooleanField(default=False, blank=True, null=True, verbose_name="Кафе: Инвентаризация")

    can_view_school_students = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: ученики")
    can_view_school_groups = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: группы")
    can_view_school_lessons = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: занятия")
    can_view_school_teachers = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: преподаватели")
    can_view_school_leads = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: лиды")
    can_view_school_invoices = models.BooleanField(default=False, blank=True, null=True, verbose_name="Школа: счета")

    can_view_client_requests = models.BooleanField(default=False, verbose_name="Доступ к заявкам клиентов")
    can_view_salary = models.BooleanField(default=False, verbose_name="Доступ к зарплатам")
    can_view_sales = models.BooleanField(default=False, verbose_name="Доступ к продажам")
    can_view_services = models.BooleanField(default=False, verbose_name="Доступ к услугам")
    can_view_agent = models.BooleanField(default=False, verbose_name="Доступ к агентам")
    can_view_catalog = models.BooleanField(default=False, verbose_name="Доступ к каталогу")
    can_view_branch = models.BooleanField(default=False, verbose_name="Доступ к филиалу")
    can_view_logistics = models.BooleanField(default=False, verbose_name="Доступ к логистике")
    can_view_request = models.BooleanField(default=False, verbose_name="Доступ к запросам")
    can_view_shifts = models.BooleanField(default=False, verbose_name="Доступ к смене")
    can_view_cashier = models.BooleanField(default=False, verbose_name="Доступ к интерфейсу кассы")
    can_view_document = models.BooleanField(default=False, verbose_name="Доступ к документам")
    can_view_market_scales = models.BooleanField(default=False, verbose_name="Доступ к весам")
    can_view_market_label = models.BooleanField(default=False, verbose_name="Доступ к печати этикеток")
    
    branches = models.ManyToManyField(
        "Branch",
        through="BranchMembership",
        blank=True,
        related_name="users",
        verbose_name="Филиалы",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Удалён")
    deleted_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_users",
        verbose_name="Кем удалён",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        indexes = [
            models.Index(fields=["company", "role"]),
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["deleted_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.email

    @property
    def role_display(self) -> str:
        if self.role:
            return self.get_role_display()
        if self.custom_role:
            return self.custom_role.name
        return "Без роли"

    def is_in_branch(self, branch) -> bool:
        if branch is None:
            return True
        if not self.pk:
            return False
        return self.branch_memberships.filter(branch=branch).exists()

    @property
    def allowed_branch_ids(self):
        return list(self.branch_memberships.values_list("branch_id", flat=True))

    @property
    def primary_branch(self):
        membership = self.branch_memberships.select_related("branch").filter(is_primary=True).first()
        return membership.branch if membership else None

    @transaction.atomic
    def soft_delete(self, by_user=None):
        if self.deleted_at:
            return

        old_email = self.email or ""
        stamp = uuid.uuid4().hex
        new_email = f"deleted__{stamp}__{old_email}"[:254]

        self.is_active = False
        self.deleted_at = timezone.now()
        if by_user and getattr(by_user, "pk", None):
            self.deleted_by = by_user

        self.email = new_email
        self.set_unusable_password()
        self.save(update_fields=["is_active", "deleted_at", "deleted_by", "email", "password"])


class BranchMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="branch_memberships", verbose_name="Сотрудник")
    branch = models.ForeignKey("Branch", on_delete=models.CASCADE, related_name="branch_memberships", verbose_name="Филиал")

    role = models.CharField(max_length=64, blank=True, null=True, verbose_name="Роль в филиале")
    is_primary = models.BooleanField(default=False, verbose_name="Основной филиал")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Членство в филиале"
        verbose_name_plural = "Членства в филиалах"
        constraints = [
            models.UniqueConstraint(fields=["user", "branch"], name="uq_membership_user_branch"),
            # ✅ гарантируем: у одного юзера может быть только 1 primary membership
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_primary=True),
                name="uq_membership_user_primary_one",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "branch"]),
            models.Index(fields=["branch", "is_primary"]),
            models.Index(fields=["user", "is_primary"]),
        ]

    def __str__(self):
        return f"{self.user.email} → {self.branch}"

    def clean(self):
        if self.user and self.branch:
            if not self.user.company_id:
                raise ValidationError({"user": "У сотрудника не указана компания."})
            if self.user.company_id != self.branch.company_id:
                raise ValidationError("Сотрудник и филиал принадлежат разным компаниям.")


class ScaleDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="scales", verbose_name="Компания")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="scales", verbose_name="Филиал")

    name = models.CharField(max_length=128, verbose_name="Название весов")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="IP агента/весов")

    is_active = models.BooleanField(default=True, verbose_name="Активны")
    last_seen_at = models.DateTimeField(blank=True, null=True, verbose_name="Последнее подключение")
    products_last_sync_at = models.DateTimeField(blank=True, null=True, verbose_name="Последняя загрузка товаров")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Весы"
        verbose_name_plural = "Весы"
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["branch", "is_active"]),
            models.Index(fields=["last_seen_at"]),
        ]

    def __str__(self):
        if self.branch:
            return f"{self.company.name} / {self.branch.name} / {self.name}"
        return f"{self.company.name} / {self.name}"
