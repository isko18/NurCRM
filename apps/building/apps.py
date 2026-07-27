from django.apps import AppConfig


class BuildingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.building"
    verbose_name = "Строительство (объекты, ЖК)"

    def ready(self):
        from django.db.models.signals import post_save

        from .models import BuildingCashFlow
        from .salary_cash import on_cashflow_approved
        from .onec_bridge import sync_cashflow

        def _on_cashflow_save(sender, instance, **kwargs):
            if instance.status != BuildingCashFlow.Status.APPROVED:
                return
            if getattr(instance, "source_business_operation_id", None):
                on_cashflow_approved(instance)
            # Единая точка выгрузки денег в 1С: любое проведённое движение кассы
            # (касса, ЗП, аванс, рассрочка, закупки — все создают BuildingCashFlow)
            # уходит как ПКО/РКО. Идемпотентно, no-op если интеграция выключена.
            sync_cashflow(instance)

        # weak=False обязателен: _on_cashflow_save — локальное замыкание, при слабой
        # ссылке (default) оно собирается GC после ready() и сигнал молча отваливается.
        post_save.connect(_on_cashflow_save, sender=BuildingCashFlow, weak=False)

        # Write-back проведения из 1С (inbound callback) → BuildingTreaty.erp_*.
        try:
            from apps.onec.events import document_posted
            from .onec_bridge import on_document_posted

            document_posted.connect(on_document_posted, weak=False)
        except Exception:
            pass
