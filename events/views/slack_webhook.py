import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from events.models import EventRequest
from events.services.event_request_processing import approve_event_request, reject_event_request
from events.services.event_request_slack import (
    ACTION_APPROVE,
    ACTION_REJECT,
    REJECT_REASON,
    update_event_request_slack_message,
)

logger = logging.getLogger(__name__)

_MAX_TIMESTAMP_SKEW = 60 * 5


def verify_slack_request(request):
    timestamp = request.META.get('HTTP_X_SLACK_REQUEST_TIMESTAMP', '')
    signature = request.META.get('HTTP_X_SLACK_SIGNATURE', '')
    secret = settings.SLACK_SIGNING_SECRET or ''
    if not timestamp or not signature or not secret:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > _MAX_TIMESTAMP_SKEW:
        logger.warning('Slack webhook timestamp fuera de ventana: %s', timestamp)
        return False
    basestring = f'v0:{timestamp}:{request.body.decode("utf-8")}'
    digest = hmac.new(
        secret.encode('utf-8'),
        basestring.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    expected = f'v0={digest}'
    return hmac.compare_digest(expected, signature)


def _actor_label(payload):
    user = payload.get('user') or {}
    return user.get('username') or user.get('name') or user.get('id') or ''


@csrf_exempt
def slack_event_request_webhook(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'method not allowed'}, status=405)

    if not verify_slack_request(request):
        return JsonResponse({'error': 'invalid signature'}, status=403)

    raw_payload = request.POST.get('payload') or ''
    try:
        payload = json.loads(raw_payload or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    if payload.get('type') != 'block_actions':
        return HttpResponse(status=200)

    actions = payload.get('actions') or []
    if not actions:
        return HttpResponse(status=200)

    action = actions[0]
    action_id = action.get('action_id')
    try:
        request_id = int(action.get('value') or 0)
    except (TypeError, ValueError):
        return HttpResponse(status=200)

    event_request = EventRequest.objects.filter(pk=request_id).first()
    if not event_request:
        logger.warning('Slack action %s para propuesta inexistente #%s', action_id, request_id)
        return HttpResponse(status=200)

    actor = _actor_label(payload)

    if action_id == ACTION_APPROVE:
        ok, reply = approve_event_request(event_request, actor_label=actor)
    elif action_id == ACTION_REJECT:
        ok, reply = reject_event_request(
            event_request,
            reason=REJECT_REASON,
            actor_label=actor,
        )
    else:
        return HttpResponse(status=200)

    if not ok:
        event_request.refresh_from_db()
        already_approved = event_request.status == EventRequest.Status.APPROVED
        update_event_request_slack_message(
            event_request,
            approved=already_approved,
            actor_label=actor,
        )
        logger.info('Slack action ignorada o fallida: %s', reply)

    return HttpResponse(status=200)
