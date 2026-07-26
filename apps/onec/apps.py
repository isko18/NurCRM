from django.apps import AppConfig


class OnecConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.onec"
    verbose_name = "Интеграция с 1С"

    def ready(self):
        # Регистрируем сигналы (сброс кеша токена при смене настроек).
        from . import signals  # noqa: F401
