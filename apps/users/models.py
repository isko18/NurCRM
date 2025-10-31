from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
import uuid
from apps.users.managers import UserManager
from datetime import timedelta
from django.utils import timezone


class Feature(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Функция'
        verbose_name_plural = 'Функции'


class SubscriptionPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    name = models.CharField(max_length=128)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, null=True)
    # ManyToManyField корректно использовать с blank=True (null=True не поддерживается)
    features = models.ManyToManyField(Feature, blank=True)

    def __str__(self):
        return f"{self.name} - {self.price}₽"

    class Meta:
        verbose_name = 'Тариф'
        verbose_name_plural = 'Тарифы'

    def has_feature(self, feature_name):
        """Проверка наличия функции в тарифе"""
        return self.features.filter(name=feature_name).exists()


class Roles(models.TextChoices):
    ADMIN = "admin", "Администратор"
    OWNER = "owner", "Владелец"


class Sector(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, verbose_name='Название отрасли')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Отрасль"
        verbose_name_plural = "Отрасли"


class Industry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, verbose_name='Название вида деятельности')
    sectors = models.ManyToManyField(Sector, blank=True, related_name='industries', verbose_name='Отрасли')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Вид деятельности"
        verbose_name_plural = "Виды деятельности"


class CustomRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='custom_roles',
        verbose_name='Компания'
    )
    name = models.CharField(max_length=64, verbose_name="Название роли")

    class Meta:
        verbose_name = "Доп. роль"
        verbose_name_plural = "Доп. роли"
        unique_together = ("company", "name")

    def __str__(self):
        return self.name


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    email = models.EmailField(unique=True, verbose_name='Email')
    password = models.CharField(max_length=128, verbose_name='Пароль')
    first_name = models.CharField(max_length=64, verbose_name='Имя')
    last_name = models.CharField(max_length=64, verbose_name='Фамилия')
    avatar = models.URLField(blank=True, null=True, verbose_name='Аватар (URL)')
    phone_number = models.CharField(max_length=64, verbose_name='Номер телефона', blank=True, null=True)
    track_number = models.CharField(max_length=64, verbose_name='Номер машины', blank=True, null=True)
    
    company = models.ForeignKey(
        "Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="employees",
        verbose_name="Компания"
    )

    role = models.CharField(
        max_length=32,
        choices=Roles.choices,
        blank=True,
        null=True,
        verbose_name='Системная роль'
    )
    custom_role = models.ForeignKey(
        CustomRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="Кастомная роль"
    )

    # ===== Права доступа (существующие) =====
    can_view_dashboard = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к обзору')
    can_view_cashbox = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к кассе')
    can_view_departments = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к отделам')
    can_view_orders = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к заказам')
    can_view_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к аналитике')
    can_view_products = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к товарам')
    can_view_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к бронированию')
    can_view_department_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к аналитике отделов')
    can_view_employees = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к сотрудникам')
    can_view_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к клиентам')
    can_view_brand_category = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к брендам и категориям')
    can_view_settings = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к настройкам')
    can_view_sale = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к продажам')

    # ===== Права доступа (НОВЫЕ) =====
    can_view_building_work_process = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к процессам строительства')
    can_view_building_objects = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к объектам')
    can_view_additional_services = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к доп. услугам')
    can_view_debts = models.BooleanField(default=False, blank=True, null=True, verbose_name='Доступ к долгам')

    # Барбершоп
    can_view_barber_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name='Барбершоп: клиенты')
    can_view_barber_services = models.BooleanField(default=False, blank=True, null=True, verbose_name='Барбершоп: услуги')
    can_view_barber_history = models.BooleanField(default=False, blank=True, null=True, verbose_name='Барбершоп: история')
    can_view_barber_records = models.BooleanField(default=False, blank=True, null=True, verbose_name='Барбершоп: записи')

    # Хостел
    can_view_hostel_rooms = models.BooleanField(default=False, blank=True, null=True, verbose_name='Хостел: комнаты')
    can_view_hostel_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name='Хостел: бронирование')
    can_view_hostel_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name='Хостел: клиенты')
    can_view_hostel_analytics = models.BooleanField(default=False, blank=True, null=True, verbose_name='Хостел: аналитика')

    # Кафе
    can_view_cafe_menu = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: меню')
    can_view_cafe_orders = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: заказы')
    can_view_cafe_purchasing = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: закупки')
    can_view_cafe_booking = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: бронирование')
    can_view_cafe_clients = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: клиенты')
    can_view_cafe_tables = models.BooleanField(default=False, blank=True, null=True, verbose_name='Кафе: столы')

    # Школа
    can_view_school_students = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: ученики')
    can_view_school_groups = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: группы')
    can_view_school_lessons = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: занятия')
    can_view_school_teachers = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: преподаватели')
    can_view_school_leads = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: лиды')
    can_view_school_invoices = models.BooleanField(default=False, blank=True, null=True, verbose_name='Школа: счета')
    # дубликат can_view_clients здесь намеренно отсутствует
    can_view_client_requests = models.BooleanField(default=False, verbose_name='Доступ к заявкам клиентов')
    can_view_salary = models.BooleanField(default=False, verbose_name='Доступ к зарплатам')
    can_view_sales = models.BooleanField(default=False, verbose_name='Доступ к продажам')
    can_view_services = models.BooleanField(default=False, verbose_name='Доступ к услугам')
    can_view_agent = models.BooleanField(default=False, verbose_name='Доступ к агентам')
    can_view_catalog = models.BooleanField(default=False, verbose_name='Доступ к каталогу')

    # Распределение сотрудника по филиалам (через промежуточную модель)
    branches = models.ManyToManyField(
        "Branch",
        through="BranchMembership",
        blank=True,
        related_name="users",
        verbose_name="Филиалы"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        return self.email

    @property
    def role_display(self) -> str:
        if self.role:
            return self.get_role_display()
        if self.custom_role:
            return self.custom_role.name
        return "Без роли"

    # ---- Удобные методы для филиалов ----
    def is_in_branch(self, branch) -> bool:
        """
        Состоит ли сотрудник в филиале.
        branch=None — доступ к «глобальным» данным компании.
        """
        if branch is None:
            return True
        if not self.pk:
            return False
        return self.branch_memberships.filter(branch=branch).exists()

    @property
    def allowed_branch_ids(self):
        """Список id филиалов, к которым у пользователя есть доступ."""
        return list(self.branch_memberships.values_list("branch_id", flat=True))

    @property
    def primary_branch(self):
        """Основной филиал сотрудника (если помечен)."""
        membership = self.branch_memberships.select_related("branch").filter(is_primary=True).first()
        return membership.branch if membership else None


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name='Название компании')
    subscription_plan = models.ForeignKey('SubscriptionPlan', on_delete=models.SET_NULL, null=True, blank=True)
    industry = models.ForeignKey(
        'Industry',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Вид деятельности'
    )
    sector = models.ForeignKey(
        'Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Отрасль'
    )
    owner = models.OneToOneField('User', on_delete=models.CASCADE, related_name='owned_company', verbose_name='Владелец компании')

    # Новые поля юридических/банковских данных
    llc = models.CharField("Название компании", max_length=255, blank=True, null=True)
    inn = models.CharField("ИНН", max_length=32, blank=True, null=True)
    okpo = models.CharField("ОКПО", max_length=32, blank=True, null=True)
    score = models.CharField("Расчетный счет", max_length=64, blank=True, null=True)
    bik = models.CharField("БИК", max_length=32, blank=True, null=True)
    address = models.CharField("Адрес", max_length=255, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    start_date = models.DateTimeField(verbose_name='Дата начала', blank=True, null=True)
    end_date = models.DateTimeField(verbose_name='Дата окончания', blank=True, null=True)

    can_view_documents = models.BooleanField(default=False, verbose_name='Доступ к документам')
    can_view_whatsapp = models.BooleanField(default=False, verbose_name='Доступ к whatsapp')
    can_view_instagram = models.BooleanField(default=False, verbose_name='Доступ к instagram')
    can_view_telegram = models.BooleanField(default=False, verbose_name='Доступ к telegram')

    def save(self, *args, **kwargs):
        if not self.start_date:
            self.start_date = timezone.now()
        if not self.end_date and self.start_date:
            self.end_date = self.start_date + timedelta(days=10)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Компания'
        verbose_name_plural = 'Компании'


class Branch(models.Model):
    """
    Филиал компании (точка продаж, офис, подразделение).
    Поддерживает сценарий, где branch=None = глобальный объект компании.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    company = models.ForeignKey(
        "Company",
        on_delete=models.CASCADE,
        related_name="branches",
        verbose_name="Компания",
    )

    name = models.CharField(max_length=128, verbose_name="Название филиала")
    code = models.SlugField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Код филиала (slug, например для URL или субдомена)",
        help_text="Например: spb, moscow, online — уникален в пределах компании.",
    )
    address = models.CharField(max_length=255, blank=True, null=True, verbose_name="Адрес")
    phone = models.CharField(max_length=32, blank=True, null=True, verbose_name="Телефон")
    email = models.EmailField(blank=True, null=True, verbose_name="Email филиала")
    timezone = models.CharField(
        max_length=64,
        default="Asia/Bishkek",
        verbose_name="Часовой пояс",
        help_text="Используется для расписаний, графиков и аналитики"
    )

    # Свой тариф (если нужно ограничить функционал на уровне филиала)
    subscription_plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branches",
        verbose_name="Тариф филиала",
    )

    # Отдельные включённые фичи (поверх тарифа)
    features = models.ManyToManyField(
        "Feature",
        blank=True,
        related_name="branches",
        verbose_name="Доп. функции филиала"
    )

    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Филиал"
        verbose_name_plural = "Филиалы"
        unique_together = (("company", "name"), ("company", "code"))

    def __str__(self):
        return f"{self.company.name} / {self.name}"

    def save(self, *args, **kwargs):
        """Гарантирует уникальность code внутри компании при автогенерации."""
        if not self.code:
            self.code = f"branch-{self.pk or uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    # -------- Вспомогательные методы --------
    def effective_plan(self):
        """Возвращает тариф филиала или компании."""
        return self.subscription_plan or getattr(self.company, "subscription_plan", None)

    def has_feature(self, feature_name: str) -> bool:
        """
        Проверка наличия функции:
        - сначала смотрит фичи филиала,
        - потом тариф филиала,
        - потом тариф компании.
        """
        if self.features.filter(name=feature_name).exists():
            return True
        plan = self.effective_plan()
        if plan and plan.has_feature(feature_name):
            return True
        return False


class BranchMembership(models.Model):
    """
    Членство сотрудника в филиале компании.
    Позволяет распределять сотрудников по филиалам (один сотрудник — несколько филиалов).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="branch_memberships", verbose_name="Сотрудник"
    )
    branch = models.ForeignKey(
        "Branch", on_delete=models.CASCADE, related_name="branch_memberships", verbose_name="Филиал"
    )
    # опционально: роль/метки в рамках филиала
    role = models.CharField(max_length=64, blank=True, null=True, verbose_name="Роль в филиале")
    is_primary = models.BooleanField(default=False, verbose_name="Основной филиал")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Членство в филиале"
        verbose_name_plural = "Членства в филиалах"
        unique_together = (("user", "branch"),)
        indexes = [
            models.Index(fields=["user", "branch"]),
            models.Index(fields=["branch", "is_primary"]),
        ]

    def __str__(self):
        return f"{self.user.email} → {self.branch}"

    def clean(self):
        """
        Сотрудник и филиал должны относиться к одной компании.
        """
        if self.user and self.branch:
            if not self.user.company_id:
                raise ValidationError({"user": "У сотрудника не указана компания."})
            if self.user.company_id != self.branch.company_id:
                raise ValidationError("Сотрудник и филиал принадлежат разным компаниям.")
