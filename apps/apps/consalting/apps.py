from django.apps import AppConfig


class СonsultingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.consalting'
    verbose_name='Консалтинг'

    def ready(self):
        # регистрируем подписчиков на события воронки (автоматизация + realtime)
        from . import signals  # noqa: F401
