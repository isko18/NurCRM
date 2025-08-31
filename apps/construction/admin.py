from django.contrib import admin
from apps.construction.models import Department, Cashbox, CashFlow

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'employee_count', 'created_at')
    list_filter = ('company',)
    search_fields = ('name', 'company__name')
    filter_horizontal = ('employees',)

    def employee_count(self, obj):
        return obj.employees.count()
    employee_count.short_description = 'Кол-во сотрудников'


@admin.register(Cashbox)
class CashboxAdmin(admin.ModelAdmin):
    list_display = ('name', 'department',)
    list_filter = ('department__company',)
    search_fields = ('department__name',)


@admin.register(CashFlow)
class CashFlowAdmin(admin.ModelAdmin):
    list_display = ('cashbox', 'type', 'name', 'amount', 'created_at')
    list_filter = ('type', 'cashbox__department__company')
    search_fields = ('name', 'cashbox__department__name')
    date_hierarchy = 'created_at'
