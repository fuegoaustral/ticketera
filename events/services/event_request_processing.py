import logging
import re

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from events.models import EventRequest
from events.services.event_request_chatwoot import send_chatwoot_reply
from utils.email import send_mail
from tickets.models import TicketType

logger = logging.getLogger(__name__)

DEFAULT_MAX_TICKETS = 300
DEFAULT_MAX_TICKETS_PER_ORDER = 5

APROBAR_COMMAND = re.compile(r'^APROBAR(?:\s+#?(?P<request_id>\d+))?\s*$', re.IGNORECASE)
RECHAZAR_COMMAND = re.compile(
    r'^RECHAZAR(?:\s+#?(?P<request_id>\d+))?(?:\s+(?P<reason>.+))?\s*$',
    re.IGNORECASE,
)


def _unique_event_slug(name):
    base_slug = slugify(name) or 'evento'
    slug = base_slug
    counter = 1
    from events.models import Event
    while Event.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1
    return slug


def _proposal_max_tickets(event_request):
    return event_request.max_tickets or DEFAULT_MAX_TICKETS


@transaction.atomic
def create_event_from_request(event_request):
    from events.models import Event

    end = event_request.end
    slug = _unique_event_slug(event_request.name)
    ticket_type_count = event_request.ticket_types.count()
    max_tickets = _proposal_max_tickets(event_request)

    event = Event(
        name=event_request.name,
        title=event_request.name,
        description=event_request.description,
        location=event_request.location,
        location_url=event_request.location_url or '',
        start=event_request.start,
        end=end,
        header_image=event_request.header_image,
        transfers_enabled_until=event_request.start,
        max_tickets=max_tickets,
        max_tickets_per_order=DEFAULT_MAX_TICKETS_PER_ORDER,
        venue_capacity=max_tickets,
        active=True,
        is_main=False,
        slug=slug,
        show_multiple_tickets=ticket_type_count > 1,
        attendee_must_be_registered=False,
        has_volunteers=False,
        send_transfer_notifications=False,
    )
    event.save()
    event.admins.add(event_request.requested_by)

    now = timezone.now()
    for index, ticket_type in enumerate(event_request.ticket_types.all().order_by('id'), start=1):
        TicketType.objects.create(
            event=event,
            name=ticket_type.name,
            description=ticket_type.description or '',
            price=ticket_type.price,
            ticket_count=max_tickets,
            cardinality=index,
            date_from=now,
            date_to=end,
            show_in_caja=True,
        )

    event_request.created_event = event
    event_request.status = EventRequest.Status.APPROVED
    event_request.resolved_at = timezone.now()
    event_request.save(update_fields=['created_event', 'status', 'resolved_at', 'updated_at'])
    return event


def approve_event_request(event_request):
    if event_request.status != EventRequest.Status.PENDING:
        return False, f'La propuesta #{event_request.pk} ya está {event_request.get_status_display().lower()}.'

    event = create_event_from_request(event_request)
    send_chatwoot_reply(
        event_request,
        (
            f'✅ Propuesta #{event_request.pk} aprobada.\n'
            f'Evento creado: {event.name} (`{event.slug}`)\n'
            f'Admin asignado: {event_request.requested_by.email}'
        ),
    )
    _send_approval_email(event_request, event)
    logger.info('Propuesta #%s aprobada; evento %s creado', event_request.pk, event.slug)
    return True, f'Propuesta #{event_request.pk} aprobada. Evento `{event.slug}` creado.'


def _send_approval_email(event_request, event):
    requester = event_request.requested_by
    to_email = (requester.email or '').strip()
    if not to_email:
        logger.warning(
            'Propuesta #%s aprobada pero el solicitante no tiene email',
            event_request.pk,
        )
        return

    try:
        send_mail(
            template_name='event_request_approved',
            recipient_list=[to_email],
            context={
                'user': requester,
                'event': event,
                'event_request': event_request,
                'event_admin_path': reverse('event_admin', kwargs={'event_slug': event.slug}),
                'event_config_path': reverse('event_management', kwargs={'event_slug': event.slug}),
                'ticket_types_path': reverse('ticket_types_management', kwargs={'event_slug': event.slug}),
            },
        )
    except Exception:
        logger.exception(
            'No se pudo enviar email de aprobación para propuesta #%s',
            event_request.pk,
        )


def reject_event_request(event_request, reason=''):
    if event_request.status != EventRequest.Status.PENDING:
        return False, f'La propuesta #{event_request.pk} ya está {event_request.get_status_display().lower()}.'

    event_request.status = EventRequest.Status.REJECTED
    event_request.rejection_reason = (reason or '').strip()
    event_request.resolved_at = timezone.now()
    event_request.save(update_fields=['status', 'rejection_reason', 'resolved_at', 'updated_at'])

    reply = f'❌ Propuesta #{event_request.pk} rechazada.'
    if event_request.rejection_reason:
        reply += f'\nMotivo: {event_request.rejection_reason}'
    send_chatwoot_reply(event_request, reply)
    logger.info('Propuesta #%s rechazada', event_request.pk)
    return True, f'Propuesta #{event_request.pk} rechazada.'


def _resolve_event_request(*, request_id=None, conversation_id=None):
    if request_id:
        return EventRequest.objects.filter(pk=request_id).first()
    if conversation_id:
        return EventRequest.objects.filter(
            chatwoot_conversation_id=conversation_id,
            status=EventRequest.Status.PENDING,
        ).first()
    return None


def parse_agent_command(content):
    text = _normalize_command_content(content)
    if not text:
        return None

    match = APROBAR_COMMAND.match(text)
    if match:
        request_id = match.group('request_id')
        return 'approve', int(request_id) if request_id else None

    match = RECHAZAR_COMMAND.match(text)
    if match:
        request_id = match.group('request_id')
        reason = match.group('reason') or ''
        return 'reject', int(request_id) if request_id else None, reason.strip()

    return _extract_command_fallback(content)


def _normalize_command_content(content):
    """
    Slack/Chatwoot reformatea comandos al sincronizar:
    - `APROBAR 6` (code)
    - _APROBAR 6_ (italic)
    - *APROBAR 6* (bold)
    """
    text = (content or '').strip()
    wrappers = '`*_~'
    changed = True
    while changed and len(text) >= 2:
        changed = False
        if text[0] == text[-1] and text[0] in wrappers:
            text = text[1:-1].strip()
            changed = True
    return text


def _extract_command_fallback(content):
    """Último recurso: buscar APROBAR/RECHAZAR dentro del texto con markdown."""
    text = _normalize_command_content(content)
    if re.match(r'^APROBAR\s*$', text, re.IGNORECASE):
        return 'approve', None

    match = re.match(
        r'^RECHAZAR(?:\s+(?P<reason>.+))?\s*$',
        text,
        re.IGNORECASE,
    )
    if match:
        reason = (match.group('reason') or '').strip()
        return 'reject', None, reason

    match = re.search(
        r'\b(APROBAR|RECHAZAR)\s+#?(?P<request_id>\d+)(?:\s+(?P<reason>.+))?',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    cmd = match.group(1).upper()
    request_id = int(match.group('request_id'))
    if cmd == 'APROBAR':
        return 'approve', request_id
    reason = (match.group('reason') or '').strip()
    return 'reject', request_id, reason


def handle_agent_command(content, *, conversation_id=None):
    parsed = parse_agent_command(content)
    if not parsed:
        return None

    if parsed[0] == 'approve':
        _, request_id = parsed
        event_request = _resolve_event_request(
            request_id=request_id,
            conversation_id=conversation_id,
        )
        if not event_request:
            return False, 'No encontré una propuesta pendiente vinculada a esta conversación.'
        ok, message = approve_event_request(event_request)
        return ok, message

    _, request_id, reason = parsed
    event_request = _resolve_event_request(
        request_id=request_id,
        conversation_id=conversation_id,
    )
    if not event_request:
        return False, 'No encontré una propuesta pendiente vinculada a esta conversación.'
    ok, message = reject_event_request(event_request, reason=reason)
    return ok, message
