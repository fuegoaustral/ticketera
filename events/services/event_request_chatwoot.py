import logging

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
        error_text = str(data)
        logger.error('Chatwoot API %s %s failed: %s', method, path, data)
        if response.status_code == 403 and 'API access is not enabled' in error_text:
            logger.error(
                'Chatwoot Cloud bloqueó la Application API (plan Hacker/free). '
                'Las propuestas no van a llegar al inbox hasta un plan pago o Slack/email.'
            )
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
    from events.services.event_request_actions import action_url

    requester = event_request.requested_by
    requester_label = _contact_display_name(requester)
    start_local = timezone.localtime(event_request.start)
    end_local = timezone.localtime(event_request.end)
    description_plain = strip_tags(event_request.description).strip()
    admin_url = f'{settings.APP_URL.rstrip("/")}/admin/events/eventrequest/{event_request.pk}/change/'
    approve_url = action_url(event_request, 'aprobar')
    reject_url = action_url(event_request, 'desaprobar')

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
        f'👉 [✅ Aprobar]({approve_url})\n'
        f'👉 [❌ Desaprobar]({reject_url})'
    )


def _proposal_cards(event_request):
    from events.services.event_request_actions import action_url

    return {
        'items': [
            {
                'title': f'Propuesta #{event_request.pk}: {event_request.name}',
                'description': 'Un clic alcanza. No hace falta responder en el chat.',
                'actions': [
                    {
                        'type': 'link',
                        'text': 'Aprobar',
                        'uri': action_url(event_request, 'aprobar'),
                    },
                    {
                        'type': 'link',
                        'text': 'Desaprobar',
                        'uri': action_url(event_request, 'desaprobar'),
                    },
                ],
            },
        ],
    }


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


def _contact_display_name(user):
    name = (user.get_full_name() or '').strip() or user.email
    env = getattr(settings, 'ENV', 'local')
    if env and env not in ('local', ''):
        return f'[{env}] - {name}'
    return name


def _update_contact(contact_id, user):
    payload = {
        'name': _contact_display_name(user),
        'email': user.email,
    }
    _request('PUT', f'/contacts/{contact_id}', json=payload)


def _get_or_create_contact(event_request):
    user = event_request.requested_by
    existing_id = _find_contact_by_email(user.email)
    if existing_id:
        _update_contact(existing_id, user)
        return existing_id

    identifier = f'ticketera-user-{user.pk}'
    payload = {
        'inbox_id': int(settings.CHATWOOT_SOPORTE_INBOX_ID),
        'name': _contact_display_name(user),
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


def _extract_source_id(data, inbox_id=None):
    if not data or not isinstance(data, dict):
        return None
    if data.get('source_id'):
        inbox = data.get('inbox') or {}
        if inbox_id is None:
            return data['source_id']
        item_inbox_id = data.get('inbox_id') or (inbox.get('id') if isinstance(inbox, dict) else None)
        if item_inbox_id is None or int(item_inbox_id) == int(inbox_id):
            return data['source_id']

    payload = data.get('payload')
    if isinstance(payload, dict):
        nested = _extract_source_id(payload, inbox_id)
        if nested:
            return nested
        contact = payload.get('contact')
        if isinstance(contact, dict):
            nested = _extract_source_id(contact, inbox_id)
            if nested:
                return nested

    inboxes = data.get('contact_inboxes') or []
    if isinstance(payload, dict):
        inboxes = inboxes or payload.get('contact_inboxes') or []
        contact = payload.get('contact') if isinstance(payload.get('contact'), dict) else {}
        inboxes = inboxes or contact.get('contact_inboxes') or []
    target = int(inbox_id) if inbox_id is not None else None
    for item in inboxes:
        if not isinstance(item, dict) or not item.get('source_id'):
            continue
        inbox = item.get('inbox') or {}
        item_inbox_id = item.get('inbox_id') or (inbox.get('id') if isinstance(inbox, dict) else None)
        if target is None or item_inbox_id is None or int(item_inbox_id) == target:
            return item['source_id']
    return None


def _ensure_contact_inbox(contact_id):
    inbox_id = int(settings.CHATWOOT_SOPORTE_INBOX_ID)
    data = _request('GET', f'/contacts/{contact_id}')
    source_id = _extract_source_id(data, inbox_id)
    if source_id:
        return source_id
    created = _request(
        'POST',
        f'/contacts/{contact_id}/contact_inboxes',
        json={'inbox_id': inbox_id},
    )
    source_id = _extract_source_id(created, inbox_id)
    if source_id:
        return source_id
    logger.error(
        'Chatwoot no devolvió source_id para contact %s inbox %s: %s',
        contact_id,
        inbox_id,
        created,
    )
    return None


def _extract_conversation_id(data):
    if not data or not isinstance(data, dict):
        return None
    if data.get('id'):
        return data['id']
    payload = data.get('payload')
    if isinstance(payload, dict):
        conversation = payload.get('conversation') or payload
        if isinstance(conversation, dict) and conversation.get('id'):
            return conversation['id']
    return None


def _create_conversation(contact_id):
    payload = {
        'inbox_id': int(settings.CHATWOOT_SOPORTE_INBOX_ID),
        'contact_id': contact_id,
        'status': 'open',
    }
    source_id = _ensure_contact_inbox(contact_id)
    if source_id:
        payload['source_id'] = str(source_id)
    assignee_id = getattr(settings, 'CHATWOOT_SOPORTE_ASSIGNEE_ID', '') or ''
    if assignee_id:
        payload['assignee_id'] = int(assignee_id)
    data = _request('POST', '/conversations', json=payload)
    conversation_id = _extract_conversation_id(data)
    if conversation_id:
        return conversation_id
    logger.error('Chatwoot conversation response sin id: %s', data)
    return None


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


def _create_message(
    conversation_id,
    content,
    *,
    message_type='outgoing',
    private=False,
    content_type=None,
    content_attributes=None,
):
    payload = {
        'content': content,
        'message_type': message_type,
        'private': private,
    }
    if content_type:
        payload['content_type'] = content_type
    if content_attributes is not None:
        payload['content_attributes'] = content_attributes
    return _request('POST', f'/conversations/{conversation_id}/messages', json=payload)


def _post_proposal_messages(conversation_id, event_request):
    """
    Inbox API: incoming dispara notificación a agentes (unread + push/email).
    WebWidget: solo outgoing vía API (422 en incoming); no alerta a soporte.
    """
    proposal_text = build_proposal_message(event_request)
    agent_note = (
        f'Un clic en Aprobar / Desaprobar resuelve la propuesta.\n'
        f'Fallback: `APROBAR {event_request.pk}` o `RECHAZAR {event_request.pk}`.'
    )
    if _inbox_allows_incoming_messages():
        public_type = 'incoming'
    else:
        # Website/WebWidget rejects incoming via Application API (422).
        public_type = 'outgoing'

    if not _create_message(
        conversation_id,
        proposal_text,
        message_type=public_type,
        private=False,
    ):
        return False

    _create_message(
        conversation_id,
        f'Propuesta #{event_request.pk}',
        message_type='outgoing',
        private=False,
        content_type='cards',
        content_attributes=_proposal_cards(event_request),
    )
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


def send_conversation_reply(conversation_id, content, *, private=False):
    if not conversation_id or not chatwoot_api_configured():
        return False
    return bool(_create_message(
        conversation_id,
        content,
        message_type='outgoing',
        private=private,
    ))
