from django.apps import AppConfig


class CajaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'caja'
    verbose_name = 'Caja'

    def ready(self):
        import caja.signals  # noqa: F401
