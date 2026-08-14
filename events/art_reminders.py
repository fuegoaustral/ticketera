import hashlib
import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from twilio.rest import Client

from tickets.models import MessageIdempotency
from utils.email import send_mail

from .models import ArtProgram


BLOCKS = {
    'proposal': ('Propuesta', lambda artwork: not artwork.submitted_at),
    'grant': ('Beca', lambda artwork: artwork.grant_requested and (not artwork.grant_amount or not artwork.grant_budget or not artwork.grant_justification)),
    'guide': ('Desplegable', lambda artwork: not artwork.public_title or not artwork.public_description),
    'logistics': ('Ingreso y salida', lambda artwork: not artwork.arrival_date or not artwork.departure_date),
    'checkout': ('Checkout', lambda artwork: not artwork.checkout_completed),
    'grant_report': ('Rendición de beca', lambda artwork: artwork.grant_status == artwork.GrantStatus.APPROVED and (not artwork.grant_report or not artwork.grant_photos.exists())),
}


def _send_once(user, artwork, block, deadline, channel, send):
    key = f'art:{artwork.pk}:{block}:{deadline.isoformat()}:{timezone.localdate().isoformat()}:{user.pk}:{channel}'
    digest = hashlib.sha256(key.encode()).hexdigest()
    if MessageIdempotency.objects.filter(hash=digest).exists():
        return False
    send()
    MessageIdempotency.objects.create(email=user.email, hash=digest, payload={'key': key})
    return True


def send_art_reminders(event=None, context=None):
    """Cron diario: avisa 3 y 1 días antes de cada checkpoint incompleto."""
    today = timezone.localdate()
    sent = 0
    programs = ArtProgram.objects.select_related('event').filter(event__active=True)
    for program in programs:
        for block, (label, incomplete) in BLOCKS.items():
            if block.startswith('grant') and not program.grants_enabled:
                continue
            deadline = getattr(program, f'{block}_deadline')
            if not deadline or (timezone.localdate(deadline) - today).days not in (3, 1):
                continue
            for artwork in program.event.artworks.select_related('owner').prefetch_related('collaborators'):
                if not incomplete(artwork):
                    continue
                path = reverse('artwork_edit', kwargs={'artwork_id': artwork.pk})
                users = [artwork.owner, *artwork.collaborators.exclude(pk=artwork.owner_id)]
                for user in users:
                    try:
                        sent += _send_once(
                            user, artwork, block, deadline, 'email',
                            lambda user=user: send_mail(
                                template_name='art_checkpoint_reminder',
                                recipient_list=[user.email],
                                context={'user': user, 'artwork': artwork, 'checkpoint': label, 'deadline': deadline, 'artwork_path': path},
                            ),
                        )
                    except Exception:
                        logging.exception('No se pudo enviar el recordatorio de Arte por email a %s', user.email)

                    sender = getattr(settings, 'TWILIO_WHATSAPP_FROM', '')
                    profile = getattr(user, 'profile', None)
                    if not sender or not profile or not profile.phone:
                        continue
                    try:
                        sent += _send_once(
                            user, artwork, block, deadline, 'whatsapp',
                            lambda profile=profile: Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
                                from_=sender if sender.startswith('whatsapp:') else f'whatsapp:{sender}',
                                to=f'whatsapp:{profile.phone}',
                                body=f'Fuego Austral: completá {label} de “{artwork.title}” antes del {deadline:%d/%m}. {settings.APP_URL}{path}',
                            ),
                        )
                    except Exception:
                        logging.exception('No se pudo enviar el recordatorio de Arte por WhatsApp a %s', user.email)
    return sent
