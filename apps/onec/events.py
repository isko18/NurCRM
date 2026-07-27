from django.dispatch import Signal

# Отправляется, когда 1С подтвердила проведение документа (inbound callback).
# providing kwargs: sync_record (OneCSyncRecord)
# Модули-источники (building и др.) подписываются, чтобы обновить свои объекты
# (напр. BuildingTreaty.erp_*). Так apps/onec остаётся генериком без зависимости
# от building.
document_posted = Signal()
