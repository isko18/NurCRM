from django.apps import AppConfig


class InstagramConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.instagram"
    label = "instagram"
    verbose_name = "Instagram интеграция"


    def ready(self):
    # регистрируем сигналы для автологина при входе пользователя
        from . import signals # noqa: F401