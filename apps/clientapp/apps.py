from django.apps import AppConfig


class ClientAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clientapp"
    verbose_name = "Приложение клиентов"

    def ready(self):
        import apps.clientapp.signals  # noqa: F401
