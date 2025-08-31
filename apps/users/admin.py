from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Company, Industry, SubscriptionPlan, Feature, Sector, CustomRole


# 👤 Пользователь
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'email', 'first_name', 'last_name', 'company', 'role', 'custom_role',
        'can_view_cashbox', 'can_view_orders',
        'can_view_clients', 'can_view_settings', 'can_view_sale',
        'is_staff', 'is_active'
    )
    list_filter = ('role', 'custom_role', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Персональная информация', {
            'fields': ('first_name', 'last_name', 'avatar', 'company', 'role', 'custom_role')
        }),
        ('Разрешения по разделам', {
            'fields': (
                'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
                'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
                'can_view_products', 'can_view_booking',
                'can_view_employees', 'can_view_clients',
                'can_view_brand_category', 'can_view_settings', 'can_view_sale',
            )
        }),
        ('Права доступа', {
            'fields': (
                'is_active', 'is_staff', 'is_superuser',
                'groups', 'user_permissions'
            )
        }),
        ('Даты', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email', 'password1', 'password2',
                'first_name', 'last_name', 'avatar', 'company', 'role', 'custom_role',
                'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
                'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
                'can_view_products', 'can_view_booking',
                'can_view_employees', 'can_view_clients',
                'can_view_brand_category', 'can_view_settings', 'can_view_sale',
                'is_staff', 'is_superuser', 'is_active'
            ),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is not None and 'password' in form.base_fields:
            form.base_fields['password'].required = False
        return form

    def save_model(self, request, obj, form, change):
        if obj.role in ['owner', 'admin']:
            permission_fields = [
                'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
                'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
                'can_view_products', 'can_view_booking',
                'can_view_employees', 'can_view_clients',
                'can_view_brand_category', 'can_view_settings', 'can_view_sale'
            ]
            for field in permission_fields:
                setattr(obj, field, True)
        super().save_model(request, obj, form, change)


# 🏢 Компания
@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_industry_name', 'sector', 'owner', 'employee_count', 'created_at', 'start_date', 'end_date')
    search_fields = ('name', 'industry__name', 'sector__name', 'owner__email')
    ordering = ('name',)
    readonly_fields = ('employees_list',)

    fieldsets = (
        (None, {'fields': ('name', 'industry', 'sector', 'subscription_plan', 'owner')}),
        ('Сотрудники', {'fields': ('employees_list',)}),
        ('Даты', {'fields': ('created_at', 'start_date', 'end_date')}),
    )

    def get_industry_name(self, obj):
        return obj.industry.name if obj.industry else '-'
    get_industry_name.short_description = 'Вид деятельности'

    def employee_count(self, obj):
        return obj.employees.count()
    employee_count.short_description = 'Кол-во сотрудников'

    def employees_list(self, obj):
        employees = obj.employees.all()[:5]
        if not employees:
            return "Нет сотрудников"
        names = []
        for e in employees:
            role_display = e.custom_role.name if e.custom_role else e.get_role_display()
            names.append(f'{e.first_name} {e.last_name} ({role_display})')
        result = ', '.join(names)
        total = obj.employees.count()
        if total > 5:
            result += f" и ещё {total - 5}"
        return result
    employees_list.short_description = 'Сотрудники'


# 🏭 Сектора (inline в индустрии)
class SectorInline(admin.TabularInline):
    model = Industry.sectors.through
    extra = 1
    verbose_name = 'Отрасль'
    verbose_name_plural = 'Отрасли'


# 🧩 Индустрия
@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)
    inlines = [SectorInline]
    exclude = ('sectors',)


# ⭐ Фича
@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


# 📦 Тариф
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'description')
    search_fields = ('name',)
    fields = ('name', 'price', 'description', 'features')
    filter_horizontal = ('features',)
    readonly_fields = ('id',)


# 📚 Сектор
@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


# 🎭 Кастомные роли
@admin.register(CustomRole)
class CustomRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'company')
    search_fields = ('name', 'company__name')
    ordering = ('name',)
