from django.db import IntegrityError, transaction
from django.db.models import Max

from apps.main.models import Sale, SaleDocCounter


def reserve_sale_doc_numbers(company_id, count: int = 1) -> int:
    """
    Резервирует `count` подряд идущих номеров чеков компании и возвращает первый.
    Строка счётчика блокируется до конца транзакции, поэтому номера не задваиваются.
    """
    with transaction.atomic():
        counter = SaleDocCounter.objects.select_for_update().filter(company_id=company_id).first()
        if counter is None:
            start = Sale.objects.filter(company_id=company_id).aggregate(m=Max("doc_number"))["m"] or 0
            try:
                with transaction.atomic():
                    SaleDocCounter.objects.create(company_id=company_id, last_number=start)
            except IntegrityError:
                pass
            counter = SaleDocCounter.objects.select_for_update().get(company_id=company_id)
        first = counter.last_number + 1
        counter.last_number += count
        counter.save(update_fields=["last_number"])
    return first


def ensure_sale_doc_number(sale: Sale) -> int:
    """Присваивает doc_number, если он ещё не установлен. Возвращает номер. Номер не меняется."""
    if sale.doc_number:
        return sale.doc_number
    with transaction.atomic():
        current = Sale.objects.select_for_update().filter(pk=sale.pk).values_list("doc_number", flat=True).first()
        if current:
            sale.doc_number = current
            return current
        sale.doc_number = reserve_sale_doc_numbers(sale.company_id)
        Sale.objects.filter(pk=sale.pk).update(doc_number=sale.doc_number)
    return sale.doc_number
