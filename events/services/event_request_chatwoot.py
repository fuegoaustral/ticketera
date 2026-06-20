import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

_inbox_meta_cache = {}


def chatwoot_api_configured():
    return bool(
        settings.CHATWOOT_API_ACCESS_TOKEN
        and settings.CHATWOOT_ACCOUNT_ID
        and settings.CHATWOOT_SOPORTE_INBOX_ID
    )


def chatwoot_missing_config():
    missing = []
    if not settings.CHATWOOT_API_ACCESS_TOKEN:
        missing.append('CHATWOOT_API_ACCESS_TOKEN')
    if not settings.CHATWOOT_ACCOUNT_ID:
        missing.append('CHATWOOT_ACCOUNT_ID')
    if not settings.CHATWOOT_SOPORTE_INBOX_ID:
        missing.append('CHATWOOT_SOPORTE_INBOX_ID')
    return missing


def _extract_contact_id(data):
    if not data or not isinstance(data, dict):
        return None
    if data.get('id'):
        return data['id']
    payload = data.get('payload')
    if isinstance(payload, dict):
        contact = payload.get('contact') or payload
        if isinstance(contact, dict) and contact.get('id'):
            return contact['id']
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and first.get('id'):
            return first['id']
    return None


def _api_url(path):
    base = settings.CHATWOOT_BASE_URL.rstrip('/')
    return f'{base}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}{path}'


def _headers():
    return {
        'api_access_token': settings.CHATWOOT_API_ACCESS_TOKEN,
        'Content-Type': 'application/json',
    }


def _request(method, path, **kwargs):
    response = requests.request(
        method,
        _api_url(path),
        headers=_headers(),
        timeout=20,
        **kwargs,
    )
    try:
        data = response.json()
    except ValueError:
        data = {'raw': response.text}
    if not response.ok:
        logger.error('Chatwoot API %s %s failed: %s', method, path, data)
        return None
    return data


def _format_ticket_types(event_request):
    lines = []
    for ticket_type in event_request.ticket_types.all():
        stock = event_request.max_tickets or 300
        line = f'- {ticket_type.name}: ${ticket_type.price:,.2f} (stock: {stock})'
        if ticket_type.description:
            line += f' ({ticket_type.description})'
        lines.append(line)
    return '\n'.join(lines) or '- (sin tipos)'


def build_proposal_message(event_request):
    requester = event_request.requested_by
    requester_label = requester.get_full_name() or requester.email
    start_local = timezone.localtime(event_request.start)
    end_dt = event_request.end or (event_request.start + timedelta(hours=6))
    end_local = timezone.localtime(end_dt)
    description_plain = strip_tags(event_request.description).strip()
    admin_url = f'{settings.APP_URL.rstrip("/")}/admin/events/eventrequest/{event_request.pk}/change/'

    return (
        f'📅 *Nueva propuesta de evento #{event_request.pk}*\n\n'
        f'*Nombre:* {event_request.name}\n'
        f'*Solicitante:* {requester_label} ({requester.email})\n'
        f'*Inicio:* {start_local.strftime("%d/%m/%Y %H:%M")}\n'
        f'*Fin:* {end_local.strftime("%d/%m/%Y %H:%M")}\n'
        f'*Dirección:* {event_request.location}\n'
        + (f'*Maps:* {event_request.location_url}\n' if event_request.location_url else '')
        + f'*Cupo máximo:* {event_request.max_tickets} entradas\n'
        + f'\n*Descripción:*\n{description_plain}\n\n'
        f'*Tipos de entrada:*\n{_format_ticket_types(event_request)}\n\n'
        f'Admin: {admin_url}\n\n'
        f'_Respondé con `APROBAR {event_request.pk}` o `RECHAZAR {event_request.pk} motivo...`_'
    )


def _find_contact_by_email(email):
    data = _request('GET', '/contacts/search', params={'q': email})
    if not data:
        return None
    payload = data.get('payload') or []
    for item in payload:
        contact = item if isinstance(item, dict) else {}
        if contact.get('email') == email and contact.get('id'):
            return contact['id']
    if payload and isinstance(payload[0], dict) and payload[0].get('id'):
        return payload[0]['id']
    return None


def _get_or_create_contact(event_request):
    user = event_request.requested_by
    existing_id = _find_contact_by_email(user.email)
    if existing_id:
        return existing_id

    identifier = f'ticketera-user-{user.pk}'
    payload = {
        'inbox_id': int(settings.CHATWOOT_SOPORTE_INBOX_ID),
        'name': user.get_full_name() or user.email,
        'email': user.email,
        'identifier': identifier,
        'custom_attributes': {
            'ticketera_user_id': user.pk,
        },
    }
    data = _request('POST', '/contacts', json=payload)
    if not data:
        return None
    contact_id = _extract_contact_id(data)
    if contact_id:
        return contact_id
    logger.error('Chatwoot contact response sin id para propuesta #%s: %s', event_request.pk, data)
    return None


def _create_conversation(contact_id):
    payload = {
        'inbox_id': int(settings.CHATWOOT_SOPORTE_INBOX_ID),
        'contact_id': contact_id,
        'status': 'open',
    }
    data = _request('POST', '/conversations', json=payload)
    if not data:
        return None
    return data.get('id')


def _get_inbox_meta():
    inbox_id = str(settings.CHATWOOT_SOPORTE_INBOX_ID)
    if inbox_id not in _inbox_meta_cache:
        data = _request('GET', f'/inboxes/{inbox_id}')
        _inbox_meta_cache[inbox_id] = data if isinstance(data, dict) else {}
    return _inbox_meta_cache[inbox_id]


def _inbox_allows_incoming_messages():
    """Solo inboxes API aceptan incoming por Application API (y alertan a agentes)."""
    return _get_inbox_meta().get('channel_type') == 'Channel::Api'


def _assign_conversation(conversation_id):
    assignee_id = getattr(settings, 'CHATWOOT_SOPORTE_ASSIGNEE_ID', '') or ''
    if not assignee_id:
        return
    _request(
        'POST',
        f'/conversations/{conversation_id}/assignments',
        json={'assignee_id': int(assignee_id)},
    )


def _create_message(conversation_id, content, *, message_type='outgoing', private=False):
    payload = {
        'content': content,
        'message_type': message_type,
        'private': private,
    }
    return _request('POST', f'/conversations/{conversation_id}/messages', json=payload)


def _post_proposal_messages(conversation_id, event_request):
    """
    Inbox API: incoming dispara notificación a agentes (unread + push/email).
    WebWidget: solo outgoing vía API (422 en incoming); no alerta a soporte.
    """
    proposal_text = build_proposal_message(event_request)
    agent_note = (
        f'Comandos para agentes:\n'
        f'• `APROBAR {event_request.pk}` — crea el evento y da admin al solicitante\n'
        f'• `RECHAZAR {event_request.pk} motivo opcional` — rechaza la propuesta'
    )
    if _inbox_allows_incoming_messages():
        public_type = 'incoming'
    else:
        public_type = 'outgoing'
        logger.warning(
            'Inbox %s es WebWidget: la propuesta #%s no va a alertar a agentes. '
            'Usá un inbox API (Settings → Inboxes → API) en CHATWOOT_SOPORTE_INBOX_ID.',
            settings.CHATWOOT_SOPORTE_INBOX_ID,
            event_request.pk,
        )

    if not _create_message(
        conversation_id,
        proposal_text,
        message_type=public_type,
        private=False,
    ):
        return False
    _create_message(
        conversation_id,
        agent_note,
        message_type='outgoing',
        private=True,
    )
    return True


def post_event_request_to_chatwoot(event_request):
    missing = chatwoot_missing_config()
    if missing:
        logger.warning(
            'Chatwoot API incompleta (%s); propuesta #%s sin conversación',
            ', '.join(missing),
            event_request.pk,
        )
        return False

    contact_id = _get_or_create_contact(event_request)
    if not contact_id:
        return False

    conversation_id = _create_conversation(contact_id)
    if not conversation_id:
        return False

    # Guardar IDs antes de postear mensajes (por si falla un paso posterior)
    event_request.chatwoot_contact_id = contact_id
    event_request.chatwoot_conversation_id = conversation_id
    event_request.save(update_fields=[
        'chatwoot_contact_id',
        'chatwoot_conversation_id',
        'updated_at',
    ])

    if not _post_proposal_messages(conversation_id, event_request):
        logger.error(
            'Propuesta #%s: conversación %s creada pero falló el envío de mensajes',
            event_request.pk,
            conversation_id,
        )
        return False

    _assign_conversation(conversation_id)
    return True


def send_chatwoot_reply(event_request, content, *, private=False):
    if not event_request.chatwoot_conversation_id or not chatwoot_api_configured():
        return False
    return bool(_create_message(
        event_request.chatwoot_conversation_id,
        content,
        message_type='outgoing',
        private=private,
    ))
