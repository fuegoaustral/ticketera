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

def donation_amount(request):
    return {
        'DONATION_AMOUNT': settings.DONATION_AMOUNT
    }

def chatwoot_token(request):
    return {
        'CHATWOOT_TOKEN': settings.CHATWOOT_TOKEN
    }