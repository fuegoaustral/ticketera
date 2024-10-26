import hashlib
import hmac

from deprepagos import settings
from events.models import Event


def current_event(request):
    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        event = Event.objects.latest('id')
    return {
        'event': event,
    }


def app_url(request):
    return {
        'APP_URL': settings.APP_URL
    }


def chatwoot_token(request):
    return {
        'CHATWOOT_TOKEN': settings.CHATWOOT_TOKEN
    }


def env(request):
    return {
        'ENV': settings.ENV
    }


def chatwoot_identifier_hash(request):
    if hasattr(request, 'user'):
        secret = bytes(settings.CHATWOOT_IDENTITY_VALIDATION, 'utf-8')
        message = bytes(request.user.username, 'utf-8')

        hash = hmac.new(secret, message, hashlib.sha256)
        identifier_hash = hash.hexdigest()
        return {
            'CHATWOOT_IDENTIFIER_HASH': identifier_hash
        }
    return {'CHATWOOT_IDENTIFIER_HASH': None}
