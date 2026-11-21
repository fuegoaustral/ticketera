import hashlib
import logging

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from tickets.models import MessageIdempotency
from utils.email import send_mail

from art.models import ArtProgram, Artwork


TEMPLATE = 'art_grants_open'


def _names_sentence(names):
    names = list(names)
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} o {names[-1]}"


class Command(BaseCommand):
    help = ('Avisa a cada persona con una propuesta en la convocatoria vigente (responsables y equipo) que abrió la '
            'solicitud de becas. Sin flags es dry run; --only EMAIL manda una prueba; --send manda a todos (una vez por persona).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--contact', action='append', required=True, metavar='NOMBRE',
            help='A quién consultar, como se nombra en el email. Se puede repetir.',
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--only', metavar='EMAIL', help='Manda solo a este email (prueba, no queda registrado).')
        group.add_argument('--send', action='store_true', help='Manda a todas las personas.')

    def handle(self, *args, contact, only=None, send=False, **options):
        program = ArtProgram.objects.select_related('event').filter(is_current=True).first()
        if not program:
            raise CommandError('No hay convocatoria de Arte vigente.')
        if not program.grants_enabled or program.checkpoint_state('grant') != 'open':
            raise CommandError('Las becas de la convocatoria vigente no están abiertas.')

        emails = {}
        artworks = (
            program.event.artworks.exclude(status=Artwork.Status.REJECTED)
            .select_related('owner').prefetch_related('collaborators').order_by('pk')
        )
        for artwork in artworks:
            for user in (artwork.owner, *artwork.collaborators.all()):
                if user and user.email:
                    emails.setdefault(user.email.lower(), user.email)
        recipients = sorted(emails.values(), key=str.lower)

        context = {
            'program': program,
            'event': program.event,
            'contacts_sentence': _names_sentence(contact),
            'art_path': reverse('art_dashboard'),
        }

        def deliver(email):
            send_mail(template_name=TEMPLATE, recipient_list=[email], context=dict(context))

        if only:
            deliver(only)
            self.stdout.write(self.style.SUCCESS(f'Prueba enviada a {only}'))
            return

        self.stdout.write(f'{program.event.name}: {len(recipients)} personas')
        if not send:
            for email in recipients:
                self.stdout.write(email)
            self.stdout.write('DRY RUN: no se envió nada. Usá --send para enviar.')
            return

        sent = skipped = failed = 0
        for email in recipients:
            key = f'art:{TEMPLATE}:{program.pk}:{email.lower()}'
            reservation, created = MessageIdempotency.objects.get_or_create(
                hash=hashlib.sha256(key.encode()).hexdigest(),
                defaults={'email': email, 'payload': {'key': key}},
            )
            if not created:
                skipped += 1
                continue
            try:
                deliver(email)
            except Exception:
                reservation.delete()
                failed += 1
                logging.exception('No se pudo avisar la apertura de becas a %s', email)
                self.stdout.write(self.style.ERROR(f'FALLÓ {email}'))
                continue
            sent += 1
            self.stdout.write(f'enviado {email}')
        self.stdout.write(self.style.SUCCESS(f'Enviados: {sent} · ya enviados antes: {skipped} · fallidos: {failed}'))
