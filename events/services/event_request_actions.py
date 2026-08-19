from urllib.parse import quote

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse

SIGNING_SALT = 'event-request-review'
TOKEN_MAX_AGE = 60 * 60 * 24 * 14  # 14 días
ACTION_APPROVE = 'approve'
ACTION_REJECT = 'reject'
URL_ACTIONS = {
    'aprobar': ACTION_APPROVE,
    'desaprobar': ACTION_REJECT,
}


def _signer():
    return TimestampSigner(salt=SIGNING_SALT)


def make_action_token(event_request_id, action):
    return _signer().sign(f'{action}:{event_request_id}')


def parse_action_token(token):
    try:
        value = _signer().unsign(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    action, sep, pk = value.partition(':')
    if not sep or action not in (ACTION_APPROVE, ACTION_REJECT) or not pk.isdigit():
        return None
    return action, int(pk)


def action_url(event_request, url_action):
    action = URL_ACTIONS[url_action]
    token = make_action_token(event_request.pk, action)
    path = reverse('event_request_review', kwargs={
        'request_id': event_request.pk,
        'action': url_action,
    })
    return f'{settings.APP_URL.rstrip("/")}{path}?t={quote(token, safe="")}'
