from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin  # Импортируем BaseUserAdmin
from .models import User, Company, Industry, SubscriptionPlan, Feature, Sector

# Модель UserAdmin для настройки админки пользователя
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'company', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),  # Пароль будет редактируемым, если он не пустой
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
            form.base_fields['password'].required = False  # Сделаем поле пароля не обязательным при редактировании
        return form


# Модель CompanyAdmin для настройки админки компании
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
        return ', '.join([f'{e.first_name} {e.last_name} ({e.get_role_display()})' for e in employees])
    employees_list.short_description = 'Сотрудники'

class SectorInline(admin.TabularInline):
    model = Industry.sectors.through
    extra = 1
    verbose_name = 'Отрасль'
    verbose_name_plural = 'Отрасли'
    
# Модель IndustryAdmin для настройки админки видов деятельности
@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)
    inlines = [SectorInline]
    exclude = ('sectors',) 


# Модель SubscriptionPlan для настройки админки тарифов
class FeatureInline(admin.TabularInline):
    model = SubscriptionPlan.features.through  # Это связь ManyToMany
    extra = 1  # Количество пустых строк для добавления

# Модель для отображения и редактирования тарифов
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'description')
    search_fields = ('name',)
    fields = ('name', 'price', 'description', 'features')
    filter_horizontal = ('features',)  # Для ManyToManyField features

    inlines = [FeatureInline]  # Добавляем inline для функций
    
@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)