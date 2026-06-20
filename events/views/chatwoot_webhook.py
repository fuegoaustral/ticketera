import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from events.services.event_request_processing import handle_agent_command

logger = logging.getLogger(__name__)


def _is_agent_message(payload):
    message_type = payload.get('message_type')
    if message_type in (1, 'outgoing', 'Outgoing'):
        return not payload.get('private', False)
    return False


@csrf_exempt
def chatwoot_event_request_webhook(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    if payload.get('event') != 'message_created':
        return HttpResponse(status=200)

    message = payload.get('message') or payload
    if not _is_agent_message(message):
        return HttpResponse(status=200)

    content = message.get('content', '')
    conversation = message.get('conversation') or payload.get('conversation') or {}
    conversation_id = conversation.get('id')

    result = handle_agent_command(content, conversation_id=conversation_id)
    if result is None:
        return HttpResponse(status=200)

    ok, reply = result
    if not ok:
        logger.info('Comando Chatwoot ignorado o fallido: %s', reply)
    else:
        logger.info('Comando Chatwoot procesado: %s', reply)

    return HttpResponse(status=200)
