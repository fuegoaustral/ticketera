from django.apps import AppConfig


class TicketsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tickets'

    def ready(self):
        from django.core.signals import request_finished

        from utils.script_prefix import reset_script_prefix

        request_finished.connect(reset_script_prefix, dispatch_uid='utils.reset_script_prefix')
