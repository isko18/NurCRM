import django_filters as df
from .models import TransactionRecord

class TransactionRecordFilter(df.FilterSet):
    date_from = df.DateFilter(field_name="date", lookup_expr="gte")
    date_to   = df.DateFilter(field_name="date", lookup_expr="lte")
    amount_min = df.NumberFilter(field_name="amount", lookup_expr="gte")
    amount_max = df.NumberFilter(field_name="amount", lookup_expr="lte")

    class Meta:
        model = TransactionRecord
        fields = ["status", "date", "name"]  # базовые поля для фильтрации