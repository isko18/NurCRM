from django.apps import AppConfig


class EkassaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ekassa"
    verbose_name = "eKassa"

    def ready(self):
        from apps.ekassa import signals  # noqa: F401
