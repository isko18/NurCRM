from django.apps import AppConfig


class BuildingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.building"
    verbose_name = "Строительство (объекты, ЖК)"

    def ready(self):
        from django.db.models.signals import post_save

        from .models import BuildingCashFlow, BuildingDebtLedgerEntry
        from .salary_cash import on_cashflow_approved
        from .onec_bridge import sync_cashflow, sync_debt

        def _on_cashflow_save(sender, instance, **kwargs):
            if instance.status != BuildingCashFlow.Status.APPROVED:
                return
            if getattr(instance, "source_business_operation_id", None):
                on_cashflow_approved(instance)
            # Единая точка выгрузки денег в 1С: любое проведённое движение кассы
            # (касса, ЗП, аванс, рассрочка, закупки — все создают BuildingCashFlow)
            # уходит как ПКО/РКО. Идемпотентно, no-op если интеграция выключена.
            sync_cashflow(instance)

        def _on_debt_save(sender, instance, **kwargs):
            # Единая точка для долгов и бартера → КорректировкаДолга.
            # sync_debt сам отфильтрует оплаты (они покрыты кассой).
            sync_debt(instance)

        # weak=False обязателен: обработчики — локальные замыкания, при слабой ссылке
        # (default) они собираются GC после ready() и сигнал молча отваливается.
        post_save.connect(_on_cashflow_save, sender=BuildingCashFlow, weak=False)
        post_save.connect(_on_debt_save, sender=BuildingDebtLedgerEntry, weak=False)

        # Write-back проведения из 1С (inbound callback) → BuildingTreaty.erp_*.
        try:
            from apps.onec.events import document_posted
            from .onec_bridge import on_document_posted

            document_posted.connect(on_document_posted, weak=False)
        except Exception:
            pass
