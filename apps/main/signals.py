# # apps/main/signals.py

# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from apps.main.models import Task, Product
# from apps.construction.models import Department, Cashbox, CashFlow
# from apps.main.tasks import create_task_notification

# @receiver(post_save, sender=Task)
# def notify_assigned_user_async(sender, instance, created, **kwargs):
#     if created and instance.assigned_to:
#         create_task_notification.delay(str(instance.id))
        
        
# @receiver(post_save, sender=Product)
# def create_expense_on_purchase(sender, instance: Product, created, **kwargs):
#     """
#     Создаём расход по закупке товара.
#     Только при создании или увеличении количества.
#     """
#     if created and instance.purchase_price > 0 and instance.quantity > 0:
#         # находим отдел компании (например, первый)
#         dept = Department.objects.filter(company=instance.company).first()
#         if not dept:
#             return
#         cashbox, _ = Cashbox.objects.get_or_create(department=dept)

#         # создаём расход
#         CashFlow.objects.create(
#             cashbox=cashbox,
#             type="expense",
#             name=f"Закупка товара: {instance.name}",
#             amount=instance.purchase_price * instance.quantity,
#         )