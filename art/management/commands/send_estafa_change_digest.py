from django.core.management.base import BaseCommand

from art.digest import send_estafa_change_digest


class Command(BaseCommand):
    help = 'Envía a ESTAFA el resumen de los cambios de las obras que todavía no se avisaron.'

    def handle(self, *args, **options):
        sent = send_estafa_change_digest()
        self.stdout.write(self.style.SUCCESS(f'Resúmenes enviados: {sent}'))
