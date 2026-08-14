import hashlib
import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from twilio.rest import Client

from tickets.models import MessageIdempotency
from utils.email import send_mail

from .models import ArtProgram, ArtworkGrantItem, ArtworkPhoto, Event


BLOCKS = {
    'proposal': ('Propuesta', lambda artwork: not artwork.submitted_at),
    'grant': ('Beca', lambda artwork: artwork.grant_requested and (
        not artwork.grant_justification
        or not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.BUDGET).exists()
        or artwork.grant_status in (artwork.GrantStatus.NOT_REQUESTED, artwork.GrantStatus.INFO_REQUIRED)
    )),
    'guide': ('Desplegable', lambda artwork: not artwork.public_title or not artwork.public_description),
    'logistics': ('Ingreso y salida', lambda artwork: not artwork.arrival_date or not artwork.departure_date),
    'checkout': ('Checkout', lambda artwork: not artwork.checkout_completed),
    'grant_report': ('Rendición de beca', lambda artwork: artwork.grant_status in (
        artwork.GrantStatus.APPROVED, artwork.GrantStatus.PAID,
    ) and (
        not artwork.grant_report
        or not artwork.grant_items.filter(phase=ArtworkGrantItem.Phase.EXPENSE).exists()
        or not artwork.photos.filter(stage__in=(ArtworkPhoto.Stage.FINAL, ArtworkPhoto.Stage.GRANT_REPORT)).exists()
    )),
}


def _send_once(user, artwork, block, deadline, channel, send):
    key = f'art:{artwork.pk}:{block}:{deadline.isoformat()}:{timezone.localdate().isoformat()}:{user.pk}:{channel}'
    digest = hashlib.sha256(key.encode()).hexdigest()
    reservation, created = MessageIdempotency.objects.get_or_create(
        hash=digest,
        defaults={'email': user.email, 'payload': {'key': key}},
    )
    if not created:
        return False
    try:
        send()
    except Exception:
        reservation.delete()
        raise
    return True


def send_art_reminders(event=None, context=None):
    """Cron diario: avisa según los días configurados para cada checkpoint incompleto."""
    today = timezone.localdate()
    sent = 0
    programs = ArtProgram.objects.select_related('event').filter(is_current=True, event__active=True)
    if isinstance(event, Event):
        programs = programs.filter(event=event)
    for program in programs:
        for block, (label, incomplete) in BLOCKS.items():
            if block.startswith('grant') and not program.grants_enabled:
                continue
            deadline = getattr(program, f'{block}_deadline')
            reminder_days = program.reminder_days
            if not deadline or (timezone.localdate(deadline) - today).days not in reminder_days:
                continue
            for artwork in program.event.artworks.select_related('owner').prefetch_related('collaborators'):
                if artwork.status in (artwork.Status.REJECTED, artwork.Status.CANCELLED, artwork.Status.COMPLETED):
                    continue
                if not incomplete(artwork):
                    continue
                path = reverse('artwork_edit', kwargs={'artwork_id': artwork.pk})
                users = [
                    user for user in [artwork.owner, *artwork.collaborators.exclude(pk=artwork.owner_id)]
                    if user and user.email
                ]
                for user in users:
                    if program.reminder_email_enabled:
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

                    sender = getattr(settings, 'TWILIO_WHATSAPP_FROM', '') if program.reminder_whatsapp_enabled else ''
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
