from django.core.management.base import BaseCommand

from events.art_reminders import send_art_reminders


class Command(BaseCommand):
    help = 'Envía recordatorios pendientes de los checkpoints de Arte.'

    def handle(self, *args, **options):
        sent = send_art_reminders()
        self.stdout.write(self.style.SUCCESS(f'Recordatorios enviados: {sent}'))
