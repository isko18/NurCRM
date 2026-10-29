import django_filters as df
import django_filters
from .models import Debt, DebtPayment, TransactionRecord

class TransactionRecordFilter(df.FilterSet):
    date_from = df.DateFilter(field_name="date", lookup_expr="gte")
    date_to   = df.DateFilter(field_name="date", lookup_expr="lte")
    amount_min = df.NumberFilter(field_name="amount", lookup_expr="gte")
    amount_max = df.NumberFilter(field_name="amount", lookup_expr="lte")

    class Meta:
        model = TransactionRecord
        fields = ["status", "date", "name"]  # базовые поля для фильтрации
        
        

class DebtFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to   = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Debt
        fields = ["date_from", "date_to", "phone"]


class DebtPaymentFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="paid_at", lookup_expr="gte")
    date_to   = django_filters.DateFilter(field_name="paid_at", lookup_expr="lte")

    class Meta:
        model = DebtPayment
        fields = ["date_from", "date_to"]