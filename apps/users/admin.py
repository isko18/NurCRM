from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Company, Industry

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'company', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Персональная информация', {'fields': ('first_name', 'last_name', 'avatar', 'company', 'role')}),
        ('Права доступа', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Даты', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'first_name', 'last_name', 'avatar', 'company', 'role', 'is_staff', 'is_superuser', 'is_active'),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is not None and 'password' in form.base_fields:
            form.base_fields['password'].required = False
        return form


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_industry_name', 'owner', 'employee_count', 'created_at')
    search_fields = ('name', 'industry__name', 'owner__email')
    ordering = ('name',)
    readonly_fields = ('employees_list',)

    def get_industry_name(self, obj):
        return obj.industry.name if obj.industry else '-'
    get_industry_name.short_description = 'Вид деятельности'

    def employee_count(self, obj):
        return obj.employees.count()
    employee_count.short_description = 'Кол-во сотрудников'

    def employees_list(self, obj):
        employees = obj.employees.all()
        if not employees:
            return "Нет сотрудников"
        return ', '.join([
            f'{e.first_name} {e.last_name} ({e.get_role_display()})'
            for e in employees
        ])
    employees_list.short_description = 'Сотрудники'


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)
