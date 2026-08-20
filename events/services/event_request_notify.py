import logging
from dataclasses import dataclass
from urllib.parse import quote

from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from events.services.event_request_actions import (
    ACTION_APPROVE,
    ACTION_REJECT,
    make_action_token,
)
from events.services.event_request_chatwoot import post_event_request_to_chatwoot
from events.services.event_request_slack import post_event_request_to_slack
from utils.email import send_staff_mail

logger = logging.getLogger(__name__)


@dataclass
class EventRequestNotifyResult:
    chatwoot: bool = False
    slack: bool = False
    email: bool = False

    @property
    def notified(self):
        return self.chatwoot or self.slack or self.email


def _review_path(event_request, url_action, action):
    token = make_action_token(event_request.pk, action)
    path = reverse('event_request_review', kwargs={
        'request_id': event_request.pk,
        'action': url_action,
    })
    return f'{path}?t={quote(token, safe="")}'


def _ticket_type_lines(event_request):
    lines = []
    for ticket_type in event_request.ticket_types.all():
        stock = event_request.max_tickets or 300
        line = f'{ticket_type.name}: ${ticket_type.price:,.2f} (stock: {stock})'
        if ticket_type.description:
            line += f' — {strip_tags(ticket_type.description).strip()}'
        lines.append(line)
    return lines


def send_event_request_review_email(event_request):
    requester = event_request.requested_by
    requester_name = (requester.get_full_name() or '').strip() or requester.email
    description = strip_tags(event_request.description or '').strip()
    if len(description) > 800:
        description = description[:797] + '...'
    try:
        return bool(send_staff_mail(
            template_name='event_request_pending',
            context={
                'event_request': event_request,
                'requester_name': requester_name,
                'requester_email': requester.email,
                'start_local': timezone.localtime(event_request.start),
                'end_local': timezone.localtime(event_request.end),
                'description': description,
                'ticket_types': _ticket_type_lines(event_request),
                'approve_path': _review_path(event_request, 'aprobar', ACTION_APPROVE),
                'reject_path': _review_path(event_request, 'desaprobar', ACTION_REJECT),
                'admin_path': reverse(
                    'admin:events_eventrequest_change',
                    args=[event_request.pk],
                ),
            },
        ))
    except Exception:
        logger.exception(
            'No se pudo enviar email de propuesta #%s al staff',
            event_request.pk,
        )
        return False


def notify_event_request_for_review(event_request):
    """
    Chatwoot y Slack si están configurados. Si ambos fallan (p. ej. Chatwoot Cloud
    sin API en plan Hacker, o Slack sin env vars), avisa al staff por email con
    links de un clic para aprobar/desaprobar.
    """
    result = EventRequestNotifyResult()
    try:
        result.chatwoot = bool(post_event_request_to_chatwoot(event_request))
    except Exception:
        logger.exception('Chatwoot falló para propuesta #%s', event_request.pk)
    try:
        result.slack = bool(post_event_request_to_slack(event_request))
    except Exception:
        logger.exception('Slack falló para propuesta #%s', event_request.pk)

    if not result.notified:
        result.email = send_event_request_review_email(event_request)
        if result.email:
            logger.info('Propuesta #%s notificada al staff por email', event_request.pk)
        else:
            logger.error('Propuesta #%s sin ningún canal de notificación', event_request.pk)
    return result
