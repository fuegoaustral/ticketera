import logging

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

ACTION_APPROVE = 'event_request_approve'
ACTION_REJECT = 'event_request_reject'
REJECT_REASON = 'Rechazada desde Slack'

_SLACK_API = 'https://slack.com/api'


def slack_post_configured():
    return bool(settings.SLACK_BOT_TOKEN and settings.SLACK_EVENT_REQUESTS_CHANNEL)


def slack_api_configured():
    return slack_post_configured() and bool(settings.SLACK_SIGNING_SECRET)


def slack_missing_config():
    missing = []
    if not settings.SLACK_BOT_TOKEN:
        missing.append('SLACK_BOT_TOKEN')
    if not settings.SLACK_SIGNING_SECRET:
        missing.append('SLACK_SIGNING_SECRET')
    if not settings.SLACK_EVENT_REQUESTS_CHANNEL:
        missing.append('SLACK_EVENT_REQUESTS_CHANNEL')
    return missing


def _headers():
    return {
        'Authorization': f'Bearer {settings.SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json; charset=utf-8',
    }


def _slack_api(method, payload):
    try:
        response = requests.post(
            f'{_SLACK_API}/{method}',
            headers=_headers(),
            json=payload,
            timeout=20,
        )
    except requests.RequestException:
        logger.exception('Slack API %s falló (red)', method)
        return None
    try:
        data = response.json()
    except ValueError:
        logger.error('Slack API %s respuesta no JSON: %s', method, response.text)
        return None
    if not data.get('ok'):
        logger.error('Slack API %s error: %s', method, data.get('error') or data)
        return None
    return data


def _mrkdwn_escape(text):
    return (text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _format_ticket_types(event_request):
    lines = []
    for ticket_type in event_request.ticket_types.all():
        stock = event_request.max_tickets or 300
        line = f'• {ticket_type.name}: ${ticket_type.price:,.2f} (stock: {stock})'
        if ticket_type.description:
            line += f' — {strip_tags(ticket_type.description).strip()}'
        lines.append(line)
    return '\n'.join(lines) or '• (sin tipos)'


def _proposal_text(event_request):
    requester = event_request.requested_by
    requester_name = (requester.get_full_name() or '').strip() or requester.email
    start_local = timezone.localtime(event_request.start)
    end_local = timezone.localtime(event_request.end)
    description_plain = strip_tags(event_request.description).strip()
    if len(description_plain) > 500:
        description_plain = description_plain[:497] + '...'
    admin_url = (
        f'{settings.APP_URL.rstrip("/")}'
        f'/admin/events/eventrequest/{event_request.pk}/change/'
    )
    maps_line = (
        f'*Maps:* {event_request.location_url}\n'
        if event_request.location_url else ''
    )
    return (
        f':calendar: *Nueva propuesta de evento #{event_request.pk}*\n\n'
        f'*Nombre:* {_mrkdwn_escape(event_request.name)}\n'
        f'*Solicitante:* {_mrkdwn_escape(requester_name)} ({requester.email})\n'
        f'*Inicio:* {start_local.strftime("%d/%m/%Y %H:%M")}\n'
        f'*Fin:* {end_local.strftime("%d/%m/%Y %H:%M")}\n'
        f'*Dirección:* {_mrkdwn_escape(event_request.location)}\n'
        f'{maps_line}'
        f'*Cupo máximo:* {event_request.max_tickets} entradas\n\n'
        f'*Descripción:*\n{_mrkdwn_escape(description_plain)}\n\n'
        f'*Tipos de entrada:*\n{_mrkdwn_escape(_format_ticket_types(event_request))}\n\n'
        f'<{admin_url}|Abrir en admin>'
    )


def _pending_blocks(event_request):
    request_id = str(event_request.pk)
    return [
        {
            'type': 'section',
            'block_id': f'event_request_body_{request_id}',
            'text': {
                'type': 'mrkdwn',
                'text': _proposal_text(event_request),
            },
        },
        {
            'type': 'actions',
            'block_id': f'event_request_actions_{request_id}',
            'elements': [
                {
                    'type': 'button',
                    'text': {'type': 'plain_text', 'text': 'Aprobar', 'emoji': True},
                    'style': 'primary',
                    'action_id': ACTION_APPROVE,
                    'value': request_id,
                },
                {
                    'type': 'button',
                    'text': {'type': 'plain_text', 'text': 'Desaprobar', 'emoji': True},
                    'style': 'danger',
                    'action_id': ACTION_REJECT,
                    'value': request_id,
                },
            ],
        },
    ]


def _resolved_blocks(event_request, *, approved, actor_label=None):
    if approved:
        status_line = f':white_check_mark: *Propuesta #{event_request.pk} aprobada.*'
        if event_request.created_event_id:
            event = event_request.created_event
            status_line += f'\nEvento creado: {event.name} (`{event.slug}`)'
    else:
        status_line = f':x: *Propuesta #{event_request.pk} desaprobada.*'
        if event_request.rejection_reason:
            status_line += f'\nMotivo: {event_request.rejection_reason}'
    if actor_label:
        status_line += f'\nPor: {actor_label}'
    return [
        {
            'type': 'section',
            'block_id': f'event_request_resolved_{event_request.pk}',
            'text': {
                'type': 'mrkdwn',
                'text': f'{_proposal_text(event_request)}\n\n{status_line}',
            },
        },
    ]


def _fallback_text(event_request):
    return f'Nueva propuesta de evento #{event_request.pk}: {event_request.name}'


def post_event_request_to_slack(event_request):
    if not slack_post_configured():
        missing = [name for name in ('SLACK_BOT_TOKEN', 'SLACK_EVENT_REQUESTS_CHANNEL') if not getattr(settings, name, '')]
        logger.warning(
            'Slack incompleto (%s); propuesta #%s sin mensaje',
            ', '.join(missing),
            event_request.pk,
        )
        return False

    data = _slack_api('chat.postMessage', {
        'channel': settings.SLACK_EVENT_REQUESTS_CHANNEL,
        'text': _fallback_text(event_request),
        'blocks': _pending_blocks(event_request),
    })
    if not data:
        return False

    event_request.slack_channel = data.get('channel') or settings.SLACK_EVENT_REQUESTS_CHANNEL
    event_request.slack_message_ts = data.get('ts') or ''
    event_request.save(update_fields=['slack_channel', 'slack_message_ts', 'updated_at'])
    return True


def update_event_request_slack_message(event_request, *, approved, actor_label=None):
    if not event_request.slack_channel or not event_request.slack_message_ts:
        return False
    if not slack_api_configured():
        return False

    data = _slack_api('chat.update', {
        'channel': event_request.slack_channel,
        'ts': event_request.slack_message_ts,
        'text': (
            f'Propuesta #{event_request.pk} '
            f'{"aprobada" if approved else "desaprobada"}'
        ),
        'blocks': _resolved_blocks(
            event_request,
            approved=approved,
            actor_label=actor_label,
        ),
    })
    return bool(data)
