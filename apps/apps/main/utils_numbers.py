# например apps/main/utils_numbers.py
from django.db import transaction
from django.db.models import Max
from apps.main.models import Sale

def ensure_sale_doc_number(sale: Sale) -> int:
    """Присваивает doc_number, если он ещё не установлен. Возвращает номер."""
    if sale.doc_number:
        return sale.doc_number
    with transaction.atomic():
        # блокируем выборку по компании, чтобы номер не задвоился при гонке
        last = (Sale.objects.select_for_update()
                .filter(company=sale.company)
                .aggregate(m=Max("doc_number"))["m"] or 0)
        sale.doc_number = last + 1
        sale.save(update_fields=["doc_number"])
    return sale.doc_number
