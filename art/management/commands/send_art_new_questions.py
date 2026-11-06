import hashlib
import logging

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from tickets.models import MessageIdempotency
from utils.email import send_mail

from art.models import ArtProgram


TEMPLATE = 'art_new_questions'


def _first_name(user):
    profile = getattr(user, 'profile', None)
    return user.first_name or (profile.nickname if profile else '') or ''


def _send(artwork, email):
    send_mail(
        template_name=TEMPLATE,
        recipient_list=[email],
        context={
            'artwork': artwork,
            'first_name': _first_name(artwork.owner),
            'artwork_path': reverse('artwork_edit', args=[artwork.pk]),
        },
    )


class Command(BaseCommand):
    help = ('Avisa a cada responsable de una propuesta de la convocatoria vigente que sumamos preguntas al formulario. '
            'Sin flags es dry run; --only EMAIL manda una prueba; --send manda a todos (una vez por propuesta).')

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--only', metavar='EMAIL', help='Manda solo a este email (prueba, no queda registrado).')
        group.add_argument('--send', action='store_true', help='Manda a todos los responsables.')

    def handle(self, *args, only=None, send=False, **options):
        program = ArtProgram.objects.select_related('event').filter(is_current=True).first()
        if not program:
            raise CommandError('No hay convocatoria de Arte vigente.')
        artworks = list(
            program.event.artworks.select_related('owner__profile', 'event')
            .exclude(owner__isnull=True).exclude(owner__email='').order_by('pk')
        )

        if only:
            artwork = next((a for a in artworks if a.owner.email.lower() == only.lower()), None) or (artworks[0] if artworks else None)
            if not artwork:
                raise CommandError('La convocatoria vigente no tiene propuestas.')
            _send(artwork, only)
            self.stdout.write(self.style.SUCCESS(f'Prueba enviada a {only} con la propuesta {artwork.pk} · {artwork.title}'))
            return

        self.stdout.write(f'{program.event.name}: {len(artworks)} propuestas con responsable con email')
        if not send:
            for artwork in artworks:
                self.stdout.write(f'{artwork.pk} · {artwork.title} · {artwork.owner.email}')
            self.stdout.write('DRY RUN: no se envió nada. Usá --send para enviar.')
            return

        sent = skipped = failed = 0
        for artwork in artworks:
            key = f'art:{TEMPLATE}:{artwork.pk}'
            reservation, created = MessageIdempotency.objects.get_or_create(
                hash=hashlib.sha256(key.encode()).hexdigest(),
                defaults={'email': artwork.owner.email, 'payload': {'key': key}},
            )
            if not created:
                skipped += 1
                continue
            try:
                _send(artwork, artwork.owner.email)
            except Exception:
                reservation.delete()
                failed += 1
                logging.exception('No se pudo avisar las preguntas nuevas de la propuesta %s', artwork.pk)
                self.stdout.write(self.style.ERROR(f'FALLÓ {artwork.pk} · {artwork.owner.email}'))
                continue
            sent += 1
            self.stdout.write(f'enviado {artwork.pk} · {artwork.owner.email}')
        self.stdout.write(self.style.SUCCESS(f'Enviados: {sent} · ya enviados antes: {skipped} · fallidos: {failed}'))
